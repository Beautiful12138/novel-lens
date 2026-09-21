"""真实 PostgreSQL 上验证导航替换、隔离、快照及原子写入。"""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Connection, select, text
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import DBAPIError
from test_assets import assets as assets
from test_assets import source as source

import novel_lens.assets as asset_module
from novel_lens.asset_contracts import (
    AnnotationCreate,
    AssetWriteOut,
    StyleGuideCreate,
    StyleGuideEntry,
    StyleGuideGet,
    StyleGuideOut,
    StyleGuideUpdate,
)
from novel_lens.assets import AssetService
from novel_lens.contracts import SourceRange
from novel_lens.database import Database
from novel_lens.errors import ServiceError
from novel_lens.importing import ImportService
from novel_lens.schema import style_guide_entries, style_guide_ranges, style_guides


def guide_request(source: tuple[UUID, list[SourceRange]]) -> StyleGuideCreate:
    """有限范围的判断，覆盖三种条目类型以及跨章证据顺序。"""
    return StyleGuideCreate(
        request_id=uuid4(),
        work_id=source[0],
        scope_note="基于甲、乙两章\n尚未分析全书。",
        entries=[
            StyleGuideEntry(
                title="日常对白",
                kind="baseline",
                description="动作承接对白。\n 保留段落。",
                applicability="已核对章节的日常场景",
                source_ranges=list(reversed(source[1])),
            ),
            StyleGuideEntry(
                title="争执",
                kind="variation",
                description="语句变短。",
                applicability="冲突加剧时",
                source_ranges=[source[1][0]],
            ),
            StyleGuideEntry(
                title="静止",
                kind="exception",
                description="动作中断。",
                applicability="仅限此处",
                source_ranges=[source[1][1]],
            ),
        ],
    )


def guide_revision(value: StyleGuideOut, **changes: Any) -> StyleGuideUpdate:
    data: dict[str, Any] = dict(
        request_id=uuid4(),
        work_id=value.work_id,
        expected_version=value.version,
        scope_note=value.scope_note,
        entries=value.entries,
    )
    return StyleGuideUpdate(**(data | changes))


def test_guide_replace_clear_and_historical_replay(
    assets: AssetService,
    source: tuple[UUID, list[SourceRange]],
    database: Database,
) -> None:
    request = guide_request(source)
    key = StyleGuideGet(work_id=source[0])
    with pytest.raises(ServiceError) as absent:
        assets.get_style_guide(key)
    assert absent.value.code == "STYLE_GUIDE_NOT_FOUND"
    with pytest.raises(ServiceError) as absent:
        assets.update_style_guide(StyleGuideUpdate(**request.model_dump(), expected_version=1))
    assert absent.value.code == "STYLE_GUIDE_NOT_FOUND"
    first = assets.create_style_guide(request).result
    assert isinstance(first, StyleGuideOut)
    assert first.version == 1 and first.entries == request.entries
    assert assets.get_style_guide(key) == first
    changed_request = guide_revision(first, entries=[first.entries[2], first.entries[0]])
    changed = assets.update_style_guide(changed_request).result
    assert isinstance(changed, StyleGuideOut) and changed.version == 2
    assert changed.entries == changed_request.entries
    assert changed.created_at == first.created_at and changed.updated_at > first.updated_at
    with database.engine.connect() as connection:
        assert (
            len(
                connection.execute(
                    select(style_guide_entries).where(style_guide_entries.c.work_id == source[0])
                ).all()
            )
            == 2
        )
        assert (
            len(
                connection.execute(
                    select(style_guide_ranges).where(style_guide_ranges.c.work_id == source[0])
                ).all()
            )
            == 3
        )
    cleared = assets.update_style_guide(guide_revision(changed, entries=[])).result
    assert isinstance(cleared, StyleGuideOut) and cleared.entries == [] and cleared.version == 3
    assert assets.get_style_guide(key) == cleared
    assert assets.create_style_guide(request).result == first
    assert assets.update_style_guide(changed_request).result == changed
    assert assets.write_result(request.request_id).result == first
    for attempt, expected in [
        (request.model_copy(update={"request_id": uuid4()}), "STYLE_GUIDE_EXISTS"),
        (request.model_copy(update={"scope_note": "不同输入"}), "REQUEST_CONFLICT"),
        (
            request.model_copy(update={"entries": list(reversed(request.entries))}),
            "REQUEST_CONFLICT",
        ),
    ]:
        with pytest.raises(ServiceError) as error:
            assets.create_style_guide(attempt)
        assert error.value.code == expected
    with pytest.raises(ServiceError) as conflict:
        assets.create_annotation(
            AnnotationCreate(
                request_id=request.request_id,
                work_id=source[0],
                source_ranges=source[1],
            )
        )
    assert conflict.value.code == "REQUEST_CONFLICT"
    missing = uuid4()
    for action in (
        lambda: assets.get_style_guide(StyleGuideGet(work_id=missing)),
        lambda: assets.create_style_guide(
            request.model_copy(update={"work_id": missing, "request_id": uuid4()})
        ),
        lambda: assets.update_style_guide(guide_revision(cleared, work_id=missing)),
    ):
        with pytest.raises(ServiceError) as error:
            action()
        assert error.value.code == "WORK_NOT_FOUND"


@pytest.mark.parametrize("kind", ["work", "section", "endpoint", "reversed", "missing"])
def test_guide_invalid_ranges_are_atomic(
    assets: AssetService,
    source: tuple[UUID, list[SourceRange]],
    database: Database,
    kind: str,
) -> None:
    request = guide_request(source)
    old = assets.create_style_guide(request).result
    assert isinstance(old, StyleGuideOut)
    foreign = (
        ImportService(database, 10000)
        .import_source(
            f"书名：{uuid4()}\n标题：外部\n正文".encode(),
            uuid4(),
        )
        .work.id
    )
    first = source[1][0]
    bad_range = first.model_copy(
        update={
            "work": {"work_id": foreign},
            "section": {"section_id": source[1][1].section_id},
            "endpoint": {"end_paragraph_id": source[1][1].end_paragraph_id},
            "reversed": {
                "start_paragraph_id": first.end_paragraph_id,
                "end_paragraph_id": first.start_paragraph_id,
            },
            "missing": {"start_paragraph_id": uuid4()},
        }[kind]
    )
    bad_entry = old.entries[0].model_copy(update={"source_ranges": [bad_range]})
    bad = guide_revision(old, scope_note="不可保存", entries=[bad_entry])
    with pytest.raises(ServiceError) as invalid:
        assets.update_style_guide(bad)
    assert invalid.value.code == (
        "INVALID_RANGE" if kind in ("work", "reversed") else "PARAGRAPH_NOT_FOUND"
    )
    assert assets.get_style_guide(StyleGuideGet(work_id=old.work_id)) == old
    with pytest.raises(ServiceError) as absent:
        assets.write_result(bad.request_id)
    assert absent.value.code == "WRITE_NOT_COMMITTED"
    # 新建导航也不能引用另一作品；失败不留下主记录，可用同键重新提交合法输入。
    foreign_request = request.model_copy(update={"work_id": foreign, "request_id": uuid4()})
    with pytest.raises(ServiceError) as invalid:
        assets.create_style_guide(foreign_request)
    assert invalid.value.code == "INVALID_RANGE"
    with pytest.raises(ServiceError) as absent:
        assets.get_style_guide(StyleGuideGet(work_id=foreign))
    assert absent.value.code == "STYLE_GUIDE_NOT_FOUND"
    assert assets.create_style_guide(foreign_request.model_copy(update={"entries": []})).result


@pytest.mark.parametrize(
    "mode", ["same_create", "distinct_create", "same_update", "distinct_update"]
)
def test_guide_concurrent_writes(
    assets: AssetService,
    source: tuple[UUID, list[SourceRange]],
    mode: str,
) -> None:
    request = guide_request(source)
    update: StyleGuideUpdate | None = None
    if mode.endswith("update"):
        old = assets.create_style_guide(request).result
        assert isinstance(old, StyleGuideOut)
        update = guide_revision(old, scope_note="已重新核对")
    barrier = Barrier(2)

    def write(index: int) -> AssetWriteOut | str:
        current = update or request
        if mode.startswith("distinct"):
            current = current.model_copy(
                update={"request_id": uuid4(), "scope_note": f"范围{index}"}
            )
        barrier.wait(20)
        try:
            return (
                assets.update_style_guide(current)
                if isinstance(current, StyleGuideUpdate)
                else assets.create_style_guide(current)
            )
        except ServiceError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(write, range(2)))
    successes = [r for r in results if isinstance(r, AssetWriteOut)]
    if mode.startswith("same"):
        assert len(successes) == 2 and sorted(r.replayed for r in successes) == [False, True]
        assert successes[0].result == successes[1].result
    else:
        assert len(successes) == 1
        assert ("VERSION_CONFLICT" if update else "STYLE_GUIDE_EXISTS") in results
    latest = assets.get_style_guide(StyleGuideGet(work_id=request.work_id))
    assert latest == successes[0].result and latest.version == (2 if update else 1)


@pytest.mark.parametrize("failure", ["oversize", "database"])
def test_guide_failed_writes_leave_no_partial_state(
    assets: AssetService,
    source: tuple[UUID, list[SourceRange]],
    database: Database,
    failure: str,
) -> None:
    request = guide_request(source)
    if failure == "oversize":
        request = request.model_copy(update={"scope_note": "界" * 350000})
    else:
        with database.engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE FUNCTION guide_test_failure() RETURNS trigger LANGUAGE plpgsql "
                    "AS $$ BEGIN RAISE EXCEPTION 'injected'; END $$"
                )
            )
            connection.execute(
                text(
                    "CREATE TRIGGER guide_test_fail BEFORE INSERT ON style_guide_ranges "
                    "FOR EACH ROW EXECUTE FUNCTION guide_test_failure()"
                )
            )
    try:
        with pytest.raises(ServiceError if failure == "oversize" else DBAPIError) as error:
            assets.create_style_guide(request)
        if failure == "oversize":
            assert isinstance(error.value, ServiceError) and error.value.code == "ASSET_TOO_LARGE"
        with database.engine.connect() as connection:
            for table in (style_guides, style_guide_entries, style_guide_ranges):
                assert (
                    connection.execute(select(table).where(table.c.work_id == source[0])).first()
                    is None
                )
        with pytest.raises(ServiceError) as absent:
            assets.write_result(request.request_id)
        assert absent.value.code == "WRITE_NOT_COMMITTED"
        # 建立有证据的旧版本，保证更新失败不仅回滚版本，也恢复已删除的条目和引用。
        if failure == "database":
            with database.engine.begin() as connection:
                connection.execute(
                    text("ALTER TABLE style_guide_ranges DISABLE TRIGGER guide_test_fail")
                )
        old = assets.create_style_guide(guide_request(source)).result
        if failure == "database":
            with database.engine.begin() as connection:
                connection.execute(
                    text("ALTER TABLE style_guide_ranges ENABLE TRIGGER guide_test_fail")
                )
        assert isinstance(old, StyleGuideOut)
        change = guide_revision(
            old, entries=list(reversed(request.entries)), scope_note=request.scope_note
        )
        with pytest.raises(ServiceError if failure == "oversize" else DBAPIError):
            assets.update_style_guide(change)
        assert assets.get_style_guide(StyleGuideGet(work_id=old.work_id)) == old
        with pytest.raises(ServiceError) as absent:
            assets.write_result(change.request_id)
        assert absent.value.code == "WRITE_NOT_COMMITTED"
    finally:
        if failure == "database":
            with database.engine.begin() as connection:
                connection.execute(text("DROP TRIGGER guide_test_fail ON style_guide_ranges"))
                connection.execute(text("DROP FUNCTION guide_test_failure()"))
    saved = assets.update_style_guide(
        change.model_copy(update={"scope_note": "恢复后保留结论"})
    ).result
    assert (
        isinstance(saved, StyleGuideOut) and saved.version == 2 and saved.entries == change.entries
    )


def test_guide_read_uses_one_snapshot(
    assets: AssetService,
    source: tuple[UUID, list[SourceRange]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old = assets.create_style_guide(guide_request(source)).result
    assert isinstance(old, StyleGuideOut)
    entered, proceed = Event(), Event()
    original = asset_module.style_guide_out

    def paused(connection: Connection, row: RowMapping) -> StyleGuideOut:
        entered.set()
        assert proceed.wait(20)
        return original(connection, row)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with monkeypatch.context() as patch:
            patch.setattr(asset_module, "style_guide_out", paused)
            reading = pool.submit(assets.get_style_guide, StyleGuideGet(work_id=old.work_id))
            try:
                assert entered.wait(20)
                patch.setattr(asset_module, "style_guide_out", original)
                new = assets.update_style_guide(
                    guide_revision(old, scope_note="新范围", entries=[])
                ).result
            finally:
                proceed.set()
            assert reading.result(20) == old
    assert assets.get_style_guide(StyleGuideGet(work_id=old.work_id)) == new
