"""真实 PostgreSQL 上验证资产查询、事务回滚、幂等和并发修订。"""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Connection, select, text
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import DBAPIError

import novel_lens.assets as asset_module
from novel_lens.asset_contracts import (
    AnnotationCreate,
    AnnotationGet,
    AnnotationList,
    AnnotationOut,
    AnnotationUpdate,
    AssetWriteOut,
    TagCreate,
    TagList,
    TagOut,
    TagSearch,
)
from novel_lens.assets import AssetService
from novel_lens.contracts import SourceRange
from novel_lens.database import Database
from novel_lens.errors import ServiceError
from novel_lens.importing import ImportService
from novel_lens.reading import ReadingService
from novel_lens.schema import asset_write_requests, tags


@pytest.fixture
def assets(database: Database) -> AssetService:
    return AssetService(database)


@pytest.fixture
def source(database: Database) -> tuple[UUID, list[SourceRange]]:
    data = f"书名：{uuid4()}\n标题：甲\n首段\n中段\n尾段\n标题：乙\n另一章".encode()
    work = ImportService(database, 10000).import_source(data, uuid4()).work
    reading = ReadingService(database)
    result = []
    for section in reading.list_sections(work.id, 100, None).items:
        items = reading.list_paragraphs(work.id, section.id, 100, None).items
        result.append(
            SourceRange(
                work_id=work.id,
                section_id=section.id,
                start_paragraph_id=items[0].id,
                end_paragraph_id=items[-1].id,
            )
        )
    return work.id, result


def create_tag(assets: AssetService, namespace: str, name: str, **kwargs: Any) -> TagOut:
    result = assets.create_tag(
        TagCreate(
            request_id=uuid4(), namespace=namespace, name=name, description="写法定义", **kwargs
        )
    ).result
    assert isinstance(result, TagOut)
    return result


def create_annotation(
    assets: AssetService, source: tuple[UUID, list[SourceRange]], **kwargs: Any
) -> AnnotationOut:
    result = assets.create_annotation(
        AnnotationCreate(request_id=uuid4(), work_id=source[0], source_ranges=source[1], **kwargs)
    ).result
    assert isinstance(result, AnnotationOut)
    return result


def revision(value: AnnotationOut, **changes: Any) -> AnnotationUpdate:
    data: dict[str, Any] = dict(
        request_id=uuid4(),
        work_id=value.work_id,
        annotation_id=value.id,
        expected_version=value.version,
        source_ranges=value.source_ranges,
        tag_ids=value.tag_ids,
        note=value.note,
    )
    return AnnotationUpdate(**(data | changes))


def test_tags_search_and_scoped_pages(assets: AssetService) -> None:
    namespace = uuid4().hex
    first = create_tag(assets, namespace, "名称测试", aliases=["AliasCase", "共用", "共用"])
    second = create_tag(assets, namespace, "第二条", aliases=["共用", "literal%_\\match"])
    for word, expected in [
        ("名称", [first.id]),
        ("aliascase", [first.id]),
        ("写法定义", [first.id, second.id]),
        ("共用", [first.id, second.id]),
        ("%_\\", [second.id]),
    ]:
        page = assets.list_tags(TagSearch(namespace=namespace, query=word))
        assert [item.id for item in page.items] == expected
    assert first.aliases == ["AliasCase", "共用"]
    page = assets.list_tags(TagList(namespace=namespace, limit=1))
    assert page.next_cursor and page.items == [first]
    assert assets.list_tags(TagList(namespace=namespace, cursor=page.next_cursor)).items == [second]
    with pytest.raises(ServiceError, match="游标"):
        assets.list_tags(TagSearch(namespace=namespace, query="共用", cursor=page.next_cursor))
    with pytest.raises(ServiceError) as conflict:
        create_tag(assets, namespace, "名称测试")
    assert conflict.value.code == "TAG_NAME_CONFLICT"
    assert assets.get_tag(first.id) == first


def test_annotation_ranges_filters_and_revision(
    assets: AssetService,
    source: tuple[UUID, list[SourceRange]],
    database: Database,
) -> None:
    t1, t2 = [create_tag(assets, uuid4().hex, "标签") for _ in range(2)]
    value = create_annotation(
        assets, source, tag_ids=[t2.id, t1.id, t1.id], note="　说明\n" + "长" * 230
    )
    overlap = create_annotation(assets, source)
    query = AnnotationList(work_id=source[0], source_range=source[1][0], tag_ids=[t1.id, t2.id])
    page = assets.list_annotations(query)
    assert [v.id for v in page.items] == [value.id]
    summary = page.items[0]
    assert summary.note_truncated and summary.note_preview == value.note[:200]  # type: ignore[index]
    assert summary.source_range_count == 2 and summary.tag_count == 2
    assert assets.get_annotation(AnnotationGet(work_id=source[0], annotation_id=value.id)) == value
    page = assets.list_annotations(AnnotationList(work_id=source[0], limit=1))
    assert page.next_cursor
    assert (
        assets.list_annotations(AnnotationList(work_id=source[0], cursor=page.next_cursor))
        .items[0]
        .id
        == overlap.id
    )
    with pytest.raises(ServiceError) as invalid:
        assets.list_annotations(query.model_copy(update={"cursor": page.next_cursor}))
    assert invalid.value.code == "INVALID_CURSOR"
    cleared = assets.update_annotation(revision(value, note=None, tag_ids=[])).result
    assert isinstance(cleared, AnnotationOut)
    assert cleared.version == 2 and cleared.note is None and cleared.tag_ids == []
    assert cleared.created_at == value.created_at
    assert assets.list_annotations(query).items == []
    unknown = AnnotationGet(work_id=uuid4(), annotation_id=value.id)
    with pytest.raises(ServiceError) as absent:
        assets.get_annotation(unknown)
    assert absent.value.code == "ANNOTATION_NOT_FOUND"
    reading = ReadingService(database)
    items = reading.list_paragraphs(source[0], source[1][0].section_id, 100, None).items
    # 仅末段与查询相交也必须命中，不要求范围完全相等。
    tail = source[1][0].model_copy(update={"start_paragraph_id": items[-1].id})
    assert (
        len(assets.list_annotations(AnnotationList(work_id=source[0], source_range=tail)).items)
        == 2
    )


@pytest.mark.parametrize("kind", ["work", "section", "reversed", "missing", "tag"])
def test_invalid_references_leave_no_result(
    assets: AssetService,
    source: tuple[UUID, list[SourceRange]],
    kind: str,
) -> None:
    first = source[1][0]
    changes: dict[str, Any] = {
        "work": {"work_id": uuid4()},
        "section": {"end_paragraph_id": source[1][1].end_paragraph_id},
        "reversed": {
            "start_paragraph_id": first.end_paragraph_id,
            "end_paragraph_id": first.start_paragraph_id,
        },
        "missing": {"start_paragraph_id": uuid4()},
        "tag": {},
    }[kind]
    request = AnnotationCreate(
        request_id=uuid4(),
        work_id=source[0],
        source_ranges=[first.model_copy(update=changes)],
        tag_ids=[uuid4()] if kind == "tag" else [],
    )
    with pytest.raises(ServiceError):
        assets.create_annotation(request)
    with pytest.raises(ServiceError) as absent:
        assets.write_result(request.request_id)
    assert absent.value.code == "WRITE_NOT_COMMITTED"
    assert assets.list_annotations(AnnotationList(work_id=source[0])).items == []


def test_concurrent_retry_conflicts_and_snapshot_recovery(
    assets: AssetService,
    source: tuple[UUID, list[SourceRange]],
) -> None:
    request = AnnotationCreate(request_id=uuid4(), work_id=source[0], source_ranges=source[1])
    barrier = Barrier(3)

    def submit() -> AssetWriteOut:
        barrier.wait(timeout=10)
        return assets.create_annotation(request)

    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda _: submit(), range(3)))
    assert sorted(r.replayed for r in results) == [False, True, True]
    old = results[0].result
    assert isinstance(old, AnnotationOut)
    assert all(r.result == old for r in results)
    barrier = Barrier(2)
    updates = [revision(old, note=f"修订 {index}") for index in range(2)]

    def update(r: AnnotationUpdate) -> AssetWriteOut | str:
        barrier.wait(timeout=10)
        try:
            return assets.update_annotation(r)
        except ServiceError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        updates_out = list(pool.map(update, updates))
    assert updates_out.count("VERSION_CONFLICT") == 1
    winner = next(r for r in updates_out if isinstance(r, AssetWriteOut))
    winner_input = next(r for r in updates if r.request_id == winner.request_id)
    assert assets.update_annotation(winner_input).replayed
    current = winner.result
    assert isinstance(current, AnnotationOut)
    latest = assets.update_annotation(revision(current, note="第三版")).result
    assert assets.write_result(request.request_id).result == old
    assert assets.update_annotation(winner_input).result == current
    assert assets.get_annotation(AnnotationGet(work_id=old.work_id, annotation_id=old.id)) == latest
    with pytest.raises(ServiceError) as conflict:
        assets.create_annotation(request.model_copy(update={"note": "不同内容"}))
    assert conflict.value.code == "REQUEST_CONFLICT"
    with pytest.raises(ServiceError) as conflict:
        assets.create_tag(
            TagCreate(
                request_id=request.request_id,
                namespace="测试",
                name="不同操作",
                description="不得复用请求键",
            )
        )
    assert conflict.value.code == "REQUEST_CONFLICT"


def test_concurrent_same_tag_name(assets: AssetService) -> None:
    namespace = uuid4().hex
    barrier = Barrier(2)

    def submit(_: int) -> TagOut | str:
        barrier.wait(timeout=10)
        try:
            return create_tag(assets, namespace, "相同名称")
        except ServiceError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(submit, range(2)))
    assert result.count("TAG_NAME_CONFLICT") == 1
    assert len(assets.list_tags(TagList(namespace=namespace)).items) == 1


@pytest.mark.parametrize("table", ["annotation_ranges", "annotation_tags", "asset_write_requests"])
def test_database_failure_rolls_back_entire_revision(
    assets: AssetService,
    database: Database,
    source: tuple[UUID, list[SourceRange]],
    table: str,
) -> None:
    tag = create_tag(assets, uuid4().hex, "独立标签")
    old = create_annotation(assets, source, note="原说明", tag_ids=[tag.id])
    update = revision(old, note="不应提交", source_ranges=[source[1][1]])
    # 使用真实数据库触发器制造写入阶段故障；标注更新、关联替换和请求占键均须回滚。
    event = "UPDATE" if table == "asset_write_requests" else "INSERT"
    with database.engine.begin() as connection:
        connection.execute(
            text(
                "CREATE FUNCTION asset_test_failure() RETURNS trigger LANGUAGE plpgsql "
                "AS $$ BEGIN RAISE EXCEPTION 'injected write failure'; END $$"
            )
        )
        connection.execute(
            text(
                f"CREATE TRIGGER asset_test_fail BEFORE {event} ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION asset_test_failure()"
            )
        )
    try:
        with pytest.raises(DBAPIError):
            assets.update_annotation(update)
        with pytest.raises(DBAPIError):
            assets.create_annotation(
                AnnotationCreate(
                    request_id=uuid4(), work_id=source[0], source_ranges=source[1], tag_ids=[tag.id]
                )
            )
    finally:
        with database.engine.begin() as connection:
            connection.execute(text(f"DROP TRIGGER asset_test_fail ON {table}"))
            connection.execute(text("DROP FUNCTION asset_test_failure()"))
    assert assets.get_annotation(AnnotationGet(work_id=old.work_id, annotation_id=old.id)) == old
    assert len(assets.list_annotations(AnnotationList(work_id=old.work_id)).items) == 1
    assert assets.get_tag(tag.id) == tag
    with pytest.raises(ServiceError) as absent:
        assets.write_result(update.request_id)
    assert absent.value.code == "WRITE_NOT_COMMITTED"
    assert assets.update_annotation(update).result.version == 2  # type: ignore[union-attr]


def test_detail_uses_one_snapshot_during_concurrent_update(
    assets: AssetService,
    source: tuple[UUID, list[SourceRange]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old = create_annotation(assets, source, note="第一版")
    selected, changed = Event(), Event()
    original = asset_module.annotation_out

    def paused(connection: Connection, row: RowMapping) -> AnnotationOut:
        if row["version"] == 1:
            selected.set()
            assert changed.wait(10), "修改未完成"
        return original(connection, row)

    monkeypatch.setattr(asset_module, "annotation_out", paused)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            assets.get_annotation, AnnotationGet(work_id=old.work_id, annotation_id=old.id)
        )
        try:
            assert selected.wait(10), "详情未读取到主记录"
            assets.update_annotation(revision(old, note="第二版", source_ranges=[source[1][1]]))
        finally:
            changed.set()
        assert future.result(timeout=10) == old


def test_oversize_asset_is_not_committed(assets: AssetService, database: Database) -> None:
    request = TagCreate(
        request_id=uuid4(),
        namespace=uuid4().hex,
        name="超限",
        description="界" * (1024 * 1024 // 3),
    )
    with pytest.raises(ServiceError) as oversized:
        assets.create_tag(request)
    assert oversized.value.code == "ASSET_TOO_LARGE"
    with database.engine.connect() as connection:
        assert (
            connection.execute(
                select(tags.c.id).where(tags.c.namespace == request.namespace)
            ).first()
            is None
        )
        assert (
            connection.execute(
                select(asset_write_requests).where(
                    asset_write_requests.c.request_id == request.request_id
                )
            ).first()
            is None
        )
