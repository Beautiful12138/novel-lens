"""实体、关系和撤回在真实 PostgreSQL 上的行为、并发及事务验证。"""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from typing import Any
from uuid import UUID, uuid4

import pytest
from part_fixtures import import_work
from sqlalchemy import Connection, select, text
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import DBAPIError
from test_assets import assets as assets
from test_assets import create_annotation, create_tag, revision
from test_assets import source as source

import novel_lens.assets as asset_module
from novel_lens.asset_contracts import (
    AnnotationCreate,
    AnnotationGet,
    AnnotationList,
    AnnotationOut,
    AssetWriteOut,
    EntityCreate,
    EntityGet,
    EntityList,
    EntityOut,
    EntitySearch,
    EntityUpdate,
    RelationCreate,
    RelationExpand,
    RelationGet,
    RelationNode,
    RelationOut,
    RelationSearch,
    RelationSetStatus,
    RelationSnapshot,
    RelationUpdate,
)
from novel_lens.assets import AssetService
from novel_lens.contracts import SourceRange
from novel_lens.database import Database
from novel_lens.errors import ServiceError
from novel_lens.importing import ImportService
from novel_lens.reading import ReadingService
from novel_lens.schema import relation_nodes


def entity(assets: AssetService, work_id: UUID, **changes: Any) -> EntityOut:
    result = assets.create_entity(
        EntityCreate.model_validate(
            dict(
                request_id=uuid4(),
                work_id=work_id,
                type="character",
                canonical_name="同名",
                aliases=["AliasCase", "共享", "共享"],
                note="身份说明",
            )
            | changes
        )
    ).result
    assert isinstance(result, EntityOut)
    return result


def relation_request(source: tuple[UUID, list[SourceRange]], **changes: Any) -> RelationCreate:
    return RelationCreate.model_validate(
        dict(
            request_id=uuid4(),
            work_id=source[0],
            title="建立与回收",
            relation_type="伏笔回收",
            nodes=[
                RelationNode(source_range=r, role=role)
                for r, role in zip(source[1], ["建立", "回收"], strict=True)
            ],
            note="　private-relation-note\n" + "说明" * 110,
        )
        | changes
    )


def relation(
    assets: AssetService, source: tuple[UUID, list[SourceRange]], **changes: Any
) -> RelationSnapshot:
    result = assets.create_relation(relation_request(source, **changes)).result
    assert isinstance(result, RelationSnapshot)
    return result


def relation_revision(value: RelationSnapshot, **changes: Any) -> RelationUpdate:
    return RelationUpdate.model_validate(
        dict(
            request_id=uuid4(),
            work_id=value.work_id,
            relation_id=value.id,
            expected_version=value.version,
            title=value.title,
            relation_type=value.relation_type,
            note=value.note,
            tag_ids=value.tag_ids,
            entity_ids=value.entity_ids,
            nodes=[RelationNode(source_range=n.source_range, role=n.role) for n in value.nodes],
        )
        | changes
    )


def status_request(
    value: RelationOut, status: str = "withdrawn", **changes: Any
) -> RelationSetStatus:
    return RelationSetStatus.model_validate(
        dict(
            request_id=uuid4(),
            work_id=value.work_id,
            relation_id=value.id,
            expected_version=value.version,
            status=status,
        )
        | changes
    )


def test_entity_identity_search_and_annotation_links(
    assets: AssetService,
    source: tuple[UUID, list[SourceRange]],
    database: Database,
) -> None:
    work_id, ranges = source
    kinds = ["character", "location", "item", "organization", "concept"]
    values = [entity(assets, work_id, type=kind) for kind in kinds]
    duplicate = entity(assets, work_id, aliases=["literal%_\\match"])
    assert duplicate.id != values[0].id
    assert assets.list_entities(EntityList(work_id=work_id)).items == [*values, duplicate]
    assert len(assets.list_entities(EntitySearch(work_id=work_id, query="同名")).items) == 6
    assert assets.list_entities(EntitySearch(work_id=work_id, query="%_\\")).items == [duplicate]
    assert assets.list_entities(EntitySearch(work_id=work_id, query="aliascase")).items == values
    assert assets.list_entities(EntitySearch(work_id=work_id, query="身份说")).items == [
        *values,
        duplicate,
    ]
    assert assets.list_entities(EntitySearch(work_id=work_id, query="不存在")).items == []
    page = assets.list_entities(EntityList(work_id=work_id, type="character", limit=1))
    assert page.items == [values[0]] and page.next_cursor
    assert assets.list_entities(
        EntityList(work_id=work_id, type="character", cursor=page.next_cursor)
    ).items == [duplicate]
    with pytest.raises(ServiceError, match="游标"):
        assets.list_entities(EntityList(work_id=work_id, cursor=page.next_cursor))
    other_work = import_work(
        ImportService(database, 10000), f"分部：{uuid4()}\n标题：章\n段".encode(), uuid4()
    ).work.id
    foreign = entity(assets, other_work)
    assert assets.list_entities(EntitySearch(work_id=other_work, query="同名")).items == [foreign]
    with pytest.raises(ServiceError) as absent:
        assets.get_entity(EntityGet(work_id=work_id, entity_id=foreign.id))
    assert absent.value.code == "ENTITY_NOT_FOUND"
    tag = create_tag(assets, uuid4().hex, "标签")
    a = create_annotation(
        assets, source, entity_ids=[values[0].id, duplicate.id, duplicate.id], tag_ids=[tag.id]
    )
    create_annotation(assets, source, entity_ids=[values[0].id])
    query = AnnotationList(
        work_id=work_id,
        entity_ids=[duplicate.id, values[0].id],
        tag_ids=[tag.id],
        source_range=ranges[1],
    )
    assert [v.id for v in assets.list_annotations(query).items] == [a.id]
    assert assets.list_annotations(query).items[0].entity_count == 2
    changed = assets.update_entity(
        EntityUpdate(
            request_id=uuid4(),
            work_id=work_id,
            entity_id=values[0].id,
            expected_version=1,
            type="character",
            canonical_name="新名称",
            aliases=[],
            note=None,
        )
    ).result
    assert isinstance(changed, EntityOut) and changed.version == 2
    assert assets.get_annotation(AnnotationGet(work_id=work_id, annotation_id=a.id)) == a
    kept = assets.update_annotation(revision(a, note="旧客户端修改")).result
    assert isinstance(kept, AnnotationOut) and kept.entity_ids == a.entity_ids
    failed = revision(kept, entity_ids=[foreign.id])
    with pytest.raises(ServiceError) as invalid:
        assets.update_annotation(failed)
    assert invalid.value.code == "ENTITY_NOT_FOUND"
    assert assets.get_annotation(AnnotationGet(work_id=work_id, annotation_id=a.id)) == kept
    cleared = assets.update_annotation(revision(kept, entity_ids=[])).result
    assert isinstance(cleared, AnnotationOut) and cleared.entity_ids == []
    for invalid_query in (
        query.model_copy(update={"entity_ids": [foreign.id]}),
        query.model_copy(update={"entity_ids": [uuid4()]}),
    ):
        with pytest.raises(ServiceError) as invalid:
            assets.list_annotations(invalid_query)
        assert invalid.value.code == "ENTITY_NOT_FOUND"
    with pytest.raises(ServiceError) as absent:
        assets.list_entities(EntityList(work_id=uuid4()))
    assert absent.value.code == "WORK_NOT_FOUND"


def test_relations_search_expand_and_reversible_withdrawal(
    assets: AssetService,
    source: tuple[UUID, list[SourceRange]],
    database: Database,
) -> None:
    e1, e2 = entity(assets, source[0]), entity(assets, source[0])
    t1, t2 = [create_tag(assets, uuid4().hex, "标签") for _ in range(2)]
    r = relation(assets, source, tag_ids=[t2.id, t1.id], entity_ids=[e2.id, e1.id])
    same = relation(assets, source)
    # 关系可直接建立，整个作品无需任何标注。
    assert assets.list_annotations(AnnotationList(work_id=source[0])).items == []
    query = RelationSearch(
        work_id=source[0],
        query="PRIVATE-relation-note",
        relation_type="伏笔回收",
        tag_ids=[t1.id, t2.id],
        entity_ids=[e1.id, e2.id],
        source_range=source[1][1],
    )
    summary = assets.search_relations(query).items
    assert len(summary) == 1 and summary[0].id == r.id
    assert summary[0].note_truncated and summary[0].note_preview == r.note[:200]
    assert (summary[0].node_count, summary[0].tag_count, summary[0].entity_count) == (2, 2, 2)
    assert assets.search_relations(query.model_copy(update={"query": "%_"})).items == []
    identifier = RelationGet(work_id=r.work_id, relation_id=r.id)
    detail = assets.get_relation(identifier)
    assert "nodes" not in detail.model_dump() and detail.node_count == 2
    page = assets.expand_relation(
        RelationExpand(**identifier.model_dump(), expected_version=1, limit=1)
    )
    assert page.next_cursor and page.items == [r.nodes[0]]
    tail = assets.expand_relation(
        RelationExpand(**identifier.model_dump(), expected_version=1, cursor=page.next_cursor)
    )
    assert tail.items == [r.nodes[1]] and tail.next_cursor is None
    listing = assets.search_relations(RelationSearch(work_id=r.work_id, limit=1))
    assert listing.next_cursor
    assert (
        assets.search_relations(RelationSearch(work_id=r.work_id, cursor=listing.next_cursor))
        .items[0]
        .id
        == same.id
    )
    withdrawn_request = status_request(r)
    withdrawn = assets.set_relation_status(withdrawn_request).result
    assert isinstance(withdrawn, RelationSnapshot)
    assert withdrawn.status == "withdrawn" and withdrawn.nodes == r.nodes
    assert withdrawn.tag_ids == r.tag_ids and withdrawn.entity_ids == r.entity_ids
    assert assets.search_relations(query).items == []
    assert (
        assets.search_relations(query.model_copy(update={"status": "withdrawn"})).items[0].id
        == r.id
    )
    assert len(assets.search_relations(RelationSearch(work_id=r.work_id, status=None)).items) == 2
    assert assets.get_relation(identifier).status == "withdrawn"
    assert (
        assets.expand_relation(RelationExpand(**identifier.model_dump(), expected_version=2)).items
        == r.nodes
    )
    with pytest.raises(ServiceError) as conflict:
        assets.expand_relation(
            RelationExpand(**identifier.model_dump(), expected_version=1, cursor=page.next_cursor)
        )
    assert conflict.value.code == "VERSION_CONFLICT"
    with pytest.raises(ServiceError) as conflict:
        assets.expand_relation(
            RelationExpand(**identifier.model_dump(), expected_version=2, cursor=page.next_cursor)
        )
    assert conflict.value.code == "INVALID_CURSOR"
    with pytest.raises(ServiceError) as invalid:
        assets.search_relations(
            RelationSearch(work_id=r.work_id, status=None, cursor=listing.next_cursor)
        )
    assert invalid.value.code == "INVALID_CURSOR"
    changed = assets.update_relation(relation_revision(withdrawn, note="已核对误判")).result
    assert isinstance(changed, RelationSnapshot) and changed.status == "withdrawn"
    restored = assets.set_relation_status(status_request(changed, "active")).result
    assert isinstance(restored, RelationSnapshot) and restored.version == 4
    assert restored.nodes == r.nodes and restored.note == changed.note
    assert assets.set_relation_status(withdrawn_request).result == withdrawn
    assert assets.write_result(withdrawn_request.request_id).result == withdrawn
    assert assets.get_relation(identifier).status == "active"
    assert assets.search_relations(query.model_copy(update={"query": "误判"})).items[0].id == r.id
    # 位置相交包含边界；多个节点命中同一范围也只返回一次关系。
    p = (
        ReadingService(database)
        .list_paragraphs(r.work_id, source[1][0].section_id, 100, None)
        .items
    )
    overlap = source[1][0].model_copy(update={"start_paragraph_id": p[-1].id})
    assets.update_relation(
        relation_revision(
            restored,
            nodes=[
                RelationNode(source_range=source[1][0]),
                RelationNode(source_range=overlap),
            ],
        )
    )
    assert (
        len(assets.search_relations(RelationSearch(work_id=r.work_id, source_range=overlap)).items)
        == 2
    )


@pytest.mark.parametrize("kind", ["work", "endpoint", "reversed", "missing", "tag", "entity"])
def test_invalid_relation_references_are_atomic(
    assets: AssetService,
    source: tuple[UUID, list[SourceRange]],
    kind: str,
) -> None:
    old = relation(assets, source)
    first = source[1][0]
    bad_range = first.model_copy(
        update={
            "work": {"work_id": uuid4()},
            "endpoint": {"end_paragraph_id": source[1][1].end_paragraph_id},
            "reversed": {
                "start_paragraph_id": first.end_paragraph_id,
                "end_paragraph_id": first.start_paragraph_id,
            },
            "missing": {"start_paragraph_id": uuid4()},
            "tag": {},
            "entity": {},
        }[kind]
    )
    bad = relation_revision(
        old,
        nodes=[RelationNode(source_range=bad_range), RelationNode(source_range=source[1][1])],
        tag_ids=[uuid4()] if kind == "tag" else [],
        entity_ids=[uuid4()] if kind == "entity" else [],
    )
    with pytest.raises(ServiceError):
        assets.update_relation(bad)
    assert assets.get_relation(RelationGet(work_id=old.work_id, relation_id=old.id)).version == 1
    with pytest.raises(ServiceError) as absent:
        assets.write_result(bad.request_id)
    assert absent.value.code == "WRITE_NOT_COMMITTED"


@pytest.mark.parametrize("kind", ["entity", "relation"])
def test_new_asset_concurrency_and_request_recovery(
    assets: AssetService,
    source: tuple[UUID, list[SourceRange]],
    kind: str,
) -> None:
    request: EntityCreate | RelationCreate = (
        EntityCreate(request_id=uuid4(), work_id=source[0], type="character", canonical_name="名字")
        if kind == "entity"
        else relation_request(source)
    )
    barrier = Barrier(3)

    def create() -> AssetWriteOut:
        barrier.wait(20)
        return (
            assets.create_entity(request)
            if isinstance(request, EntityCreate)
            else assets.create_relation(request)
        )

    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda _: create(), range(3)))
    assert sorted(r.replayed for r in results) == [False, True, True]
    old = results[0].result
    assert all(r.result == old for r in results)
    barrier = Barrier(2)

    def change(index: int) -> AssetWriteOut | str:
        barrier.wait(20)
        try:
            if isinstance(old, EntityOut):
                return assets.update_entity(
                    EntityUpdate(
                        request_id=uuid4(),
                        work_id=old.work_id,
                        entity_id=old.id,
                        expected_version=1,
                        type="character",
                        canonical_name=f"修改{index}",
                        aliases=[],
                        note=None,
                    )
                )
            assert isinstance(old, RelationSnapshot)
            if index == 0:
                return assets.update_relation(relation_revision(old, note="内容修改"))
            return assets.set_relation_status(status_request(old))
        except ServiceError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        changes = list(pool.map(change, range(2)))
    assert changes.count("VERSION_CONFLICT") == 1
    assert assets.write_result(request.request_id).result == old
    altered = request.model_copy(update={"note": "不同输入"})
    with pytest.raises(ServiceError) as conflict:
        (
            assets.create_entity(altered)
            if isinstance(altered, EntityCreate)
            else assets.create_relation(altered)
        )
    assert conflict.value.code == "REQUEST_CONFLICT"
    with pytest.raises(ServiceError) as conflict:
        assets.create_annotation(
            AnnotationCreate(
                request_id=request.request_id, work_id=source[0], source_ranges=source[1]
            )
        )
    assert conflict.value.code == "REQUEST_CONFLICT"


@pytest.mark.parametrize(
    "table",
    [
        "relation_nodes",
        "relation_tags",
        "relation_entities",
        "asset_write_requests",
        "annotation_entities",
    ],
)
def test_new_association_failures_rollback(
    assets: AssetService,
    source: tuple[UUID, list[SourceRange]],
    database: Database,
    table: str,
) -> None:
    e = entity(assets, source[0])
    t = create_tag(assets, uuid4().hex, "独立")
    r = relation(assets, source, entity_ids=[e.id], tag_ids=[t.id])
    a = create_annotation(assets, source, entity_ids=[e.id])
    update = relation_revision(r, note="不应留下")
    event = "UPDATE" if table == "asset_write_requests" else "INSERT"
    with database.engine.begin() as connection:
        connection.execute(
            text(
                "CREATE FUNCTION relation_test_failure() RETURNS trigger LANGUAGE plpgsql "
                "AS $$ BEGIN RAISE EXCEPTION 'injected'; END $$"
            )
        )
        connection.execute(
            text(
                f"CREATE TRIGGER relation_test_fail BEFORE {event} ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION relation_test_failure()"
            )
        )
    try:
        if table == "annotation_entities":
            with pytest.raises(DBAPIError):
                assets.update_annotation(revision(a, note="失败", entity_ids=[e.id]))
            with pytest.raises(DBAPIError):
                create_annotation(assets, source, entity_ids=[e.id])
        else:
            with pytest.raises(DBAPIError):
                assets.update_relation(update)
            with pytest.raises(DBAPIError):
                relation(assets, source, entity_ids=[e.id], tag_ids=[t.id])
            if table == "asset_write_requests":
                with pytest.raises(DBAPIError):
                    assets.set_relation_status(status_request(r))
                with pytest.raises(DBAPIError):
                    entity(assets, source[0])
    finally:
        with database.engine.begin() as connection:
            connection.execute(text(f"DROP TRIGGER relation_test_fail ON {table}"))
            connection.execute(text("DROP FUNCTION relation_test_failure()"))
    assert assets.get_annotation(AnnotationGet(work_id=a.work_id, annotation_id=a.id)) == a
    assert assets.get_relation(RelationGet(work_id=r.work_id, relation_id=r.id)) == RelationOut(
        **r.model_dump()
    )
    assert (
        assets.expand_relation(
            RelationExpand(work_id=r.work_id, relation_id=r.id, expected_version=1)
        ).items
        == r.nodes
    )
    assert len(assets.search_relations(RelationSearch(work_id=r.work_id)).items) == 1
    assert assets.get_entity(EntityGet(work_id=e.work_id, entity_id=e.id)) == e
    assert assets.get_tag(t.id) == t
    with pytest.raises(ServiceError) as absent:
        assets.write_result(update.request_id)
    assert absent.value.code == "WRITE_NOT_COMMITTED"


def test_relation_detail_has_one_snapshot(
    assets: AssetService,
    source: tuple[UUID, list[SourceRange]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    e = entity(assets, source[0])
    old = relation(assets, source, entity_ids=[e.id])
    selected, changed = Event(), Event()
    original = asset_module.relation_out

    def paused(connection: Connection, row: RowMapping) -> RelationOut:
        if row["version"] == 1:
            selected.set()
            assert changed.wait(20)
        return original(connection, row)

    monkeypatch.setattr(asset_module, "relation_out", paused)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            assets.get_relation, RelationGet(work_id=old.work_id, relation_id=old.id)
        )
        try:
            assert selected.wait(20)
            assets.update_relation(relation_revision(old, note="新版", entity_ids=[]))
        finally:
            changed.set()
        assert future.result(timeout=20) == RelationOut(**old.model_dump())


def test_capacity_and_database_constraints(
    assets: AssetService,
    source: tuple[UUID, list[SourceRange]],
    database: Database,
) -> None:
    for create in (
        lambda: entity(assets, source[0], note="界" * (1024 * 1024 // 3)),
        lambda: relation(assets, source, note="界" * (1024 * 1024 // 3)),
    ):
        with pytest.raises(ServiceError) as oversized:
            create()
        assert oversized.value.code == "ASSET_TOO_LARGE"
    assert assets.list_entities(EntityList(work_id=source[0])).items == []
    assert assets.search_relations(RelationSearch(work_id=source[0])).items == []
    r = relation(assets, source)
    with database.engine.connect() as connection:
        row = dict(
            connection.execute(
                select(relation_nodes).where(relation_nodes.c.relation_id == r.id).limit(1)
            )
            .mappings()
            .one()
        )
    for changes in ({"ordinal": 0}, {"ordinal": 3}, {"ordinal": 3, "start_paragraph_id": uuid4()}):
        with pytest.raises(DBAPIError), database.engine.begin() as connection:
            connection.execute(relation_nodes.insert().values(row | changes))
