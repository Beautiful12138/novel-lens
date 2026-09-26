"""真实数据库上的阅读返回、多作品范围、固定候选续页及已读排除行为。"""

import multiprocessing
from concurrent.futures import ThreadPoolExecutor
from multiprocessing.queues import Queue
from multiprocessing.synchronize import Event
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from pydantic import SecretStr, ValidationError
from sqlalchemy import text
from test_preparation import batch_for, drain, import_book
from test_semantic import DeterministicModel

from novel_lens.config import Settings
from novel_lens.contracts import SourceRange
from novel_lens.database import Database
from novel_lens.errors import ServiceError
from novel_lens.library import LibraryService
from novel_lens.preparation import PreparationService
from novel_lens.reference import ReferenceService
from novel_lens.reference_contracts import (
    CompactReferenceHit,
    CompactReferenceResult,
    LibraryBrowse,
    MarkInput,
    PreparedImport,
    PrepareImport,
    ReferenceQuery,
    ReferenceResult,
    ReferenceScope,
    SourceRead,
)
from novel_lens.work_management import WorkManagementService


def create_in_process(
    url: str, request: dict[str, Any], results: Queue[tuple[str, str | None]], gate: Event
) -> None:
    """子进程仅返回公开结果，连接凭据不进入诊断输出。"""
    database = Database(Settings.model_construct(database_url=SecretStr(url)))
    try:
        gate.wait(20)
        try:
            page = ReferenceService(database, DeterministicModel()).query(
                ReferenceQuery.model_validate(request)
            )
            results.put(("ok", str(page.search_id)))
        except ServiceError as exc:
            results.put((exc.code, None))
        except Exception:
            results.put(("unexpected_error", None))
    finally:
        database.close()


def test_capacity_across_processes(database: Database, postgres_url: str, tmp_path: Path) -> None:
    """真实独立进程争抢最后一个槽位，不能靠各进程自己的互斥维持容量。"""
    model, _, books = setup_books(database, tmp_path)
    service = ReferenceService(database, model)
    request = ReferenceQuery(query="雨声", scope=[ReferenceScope(work_id=books[0].work.id)])
    seed = service.query(request)
    service.sessions.cleanup()
    inserted = []
    with database.engine.begin() as conn:
        free = list(
            conn.execute(
                text(
                    "SELECT n FROM generate_series(1,256) AS x(n) "
                    "WHERE NOT EXISTS(SELECT 1 FROM reference_searches s WHERE s.slot=n) ORDER BY n"
                )
            ).scalars()
        )
        for slot in free[:-1]:
            identifier = uuid4()
            conn.execute(
                text(
                    "INSERT INTO reference_searches(id,slot,snapshot_at,expires_at,payload) "
                    "SELECT :id,:slot,snapshot_at,expires_at,payload "
                    "FROM reference_searches WHERE id=:seed"
                ),
                {"id": identifier, "slot": slot, "seed": seed.search_id},
            )
            inserted.append(identifier)
    context = multiprocessing.get_context("spawn")
    results, gate = context.Queue(), context.Event()
    processes = [
        context.Process(
            target=create_in_process,
            args=(postgres_url, request.model_dump(mode="json", exclude_unset=True), results, gate),
        )
        for _ in range(4)
    ]
    outcomes = []
    try:
        for process in processes:
            process.start()
        gate.set()
        outcomes = [results.get(timeout=60) for _ in processes]
        for process in processes:
            process.join(timeout=30)
            assert process.exitcode == 0
        assert sorted(code for code, _ in outcomes) == ["REFERENCE_SEARCH_CAPACITY"] * 3 + ["ok"]
        with database.engine.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM reference_searches")).scalar() == 256
    finally:
        # 测试进程正常退出；仅清理本用例填充及新建的搜索，不删除共享测试作品。
        for process in processes:
            process.join(timeout=30)
        identifiers = (
            inserted
            + [seed.search_id]
            + [identifier for code, identifier in outcomes if code == "ok"]
        )
        with database.engine.begin() as conn:
            conn.execute(
                text("DELETE FROM reference_searches WHERE id=ANY(CAST(:ids AS uuid[]))"),
                {"ids": [str(identifier) for identifier in identifiers]},
            )


def test_limits_cursor_and_scoped_clue_warning(
    database: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model, prepare, books = setup_books(database, tmp_path)
    service, library = ReferenceService(database, model), LibraryService(database, model)
    section_page = library.browse(LibraryBrowse(view="sections", work_id=books[0].work.id)).sections
    assert section_page is not None
    sections = section_page.items
    selected = [ReferenceScope(work_id=books[0].work.id, section_ids=[sections[0].id])]
    old = service.query(ReferenceQuery(query="雨声", scope=selected))
    other = library.read(SourceRead(work_id=books[0].work.id, section_id=sections[1].id))
    assert other.actual_range is not None
    ref = SourceRange(
        work_id=books[0].work.id, section_id=sections[1].id, **other.actual_range.model_dump()
    )
    batch = batch_for(prepare, books[0])
    prepare.batch(batch.model_copy(update={"marks": [MarkInput(source_ranges=[ref], note="雨声")]}))
    with pytest.raises(ServiceError) as changed:
        service.query(ReferenceQuery(search_id=old.search_id))
    assert changed.value.code == "REFERENCE_SEARCH_STALE"
    quiet = service.query(ReferenceQuery(query="雨声", scope=selected))
    assert "warnings" not in quiet.model_dump()
    whole = service.query(
        ReferenceQuery(query="雨声", scope=[ReferenceScope(work_id=books[0].work.id)], limit=1)
    )
    assert whole.warnings[0].code == "CLUES_PENDING"
    with pytest.raises(ServiceError) as mixed:
        service.query(
            ReferenceQuery(search_id=quiet.search_id, cursor=whole.next_cursor or "invalid")
        )
    assert mixed.value.code == "INVALID_CURSOR"
    with database.engine.connect() as conn:
        before = conn.execute(text("SELECT count(*) FROM reference_searches")).scalar()
    request = ReferenceQuery(query="雨声", scope=selected)
    with monkeypatch.context() as patch:
        patch.setattr("novel_lens.reference_sessions.MAX_ASSET_RESULT_BYTES", 1)
        with pytest.raises(ServiceError) as page_size:
            service.query(request)
        assert page_size.value.code == "RESULT_TOO_LARGE"
    with monkeypatch.context() as patch:
        patch.setattr("novel_lens.reference_sessions.MAX_SEARCH_BYTES", 1)
        with pytest.raises(ServiceError) as snapshot_size:
            service.query(request)
        assert snapshot_size.value.code == "REFERENCE_SEARCH_TOO_LARGE"
    with database.engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM reference_searches")).scalar() == before


def setup_books(
    database: Database, tmp_path: Path
) -> tuple[DeterministicModel, PreparationService, list[PreparedImport]]:
    model = DeterministicModel()
    prepare = PreparationService(database, 1024 * 1024, model)
    books = [
        import_book(prepare, tmp_path, f"{n}雨声起因\n回应\n标题：后章\n{n}雨声再次响起")
        for n in range(2)
    ]
    for book in books:
        drain(prepare, book, model)
    return model, prepare, books


def all_pages(
    service: ReferenceService, page: CompactReferenceResult | ReferenceResult
) -> list[CompactReferenceHit]:
    items: list[CompactReferenceHit] = list(page.items)
    while page.next_cursor:
        page = service.query(ReferenceQuery(search_id=page.search_id, cursor=page.next_cursor))
        items.extend(page.items)
    return items


def test_multiscope_fixed_pages_projection_and_reading(database: Database, tmp_path: Path) -> None:
    model, _, books = setup_books(database, tmp_path)
    service, library = ReferenceService(database, model), LibraryService(database, model)
    scope = [ReferenceScope(work_id=b.work.id) for b in books]
    request = ReferenceQuery(query="雨声", scope=scope, limit=1)
    first = service.query(request)
    assert first.next_cursor
    assert "diagnostics" not in first.model_dump() and "warnings" not in first.model_dump()
    assert "score" not in first.model_dump()["items"][0]
    calls = len(model.calls)
    detailed = service.query(ReferenceQuery(search_id=first.search_id, format="full"))
    assert isinstance(detailed, ReferenceResult)
    assert detailed.items[0].source_range == first.items[0].source_range
    assert detailed.next_cursor == first.next_cursor
    assert detailed.diagnostics["scope"] == sorted(
        [{"work_id": str(b.work.id)} for b in books], key=lambda v: v["work_id"]
    )
    assert "score" in detailed.model_dump()["items"][0]
    next_request = ReferenceQuery(search_id=first.search_id, cursor=first.next_cursor)
    with ThreadPoolExecutor(max_workers=3) as pool:
        pages = list(pool.map(lambda _: service.query(next_request).model_dump(), range(3)))
    assert pages[0] == pages[1] == pages[2]
    model.fail = True  # 已保存搜索不依赖模型在线，新查询仍需模型。
    stored = all_pages(ReferenceService(database, model), first)
    assert len(model.calls) == calls
    assert {item.source_range.work_id for item in stored} == {b.work.id for b in books}
    assert len({str(item.source_range) for item in stored}) == len(stored)
    model.fail = False
    reverse = service.query(ReferenceQuery(query="雨声", scope=list(reversed(scope)), limit=3))
    assert [i.source_range for i in all_pages(service, reverse)] == [i.source_range for i in stored]
    ref = first.items[0].source_range
    compact = library.read(SourceRead(**ref.model_dump(), limit=1))
    full = library.read(SourceRead(**ref.model_dump(), limit=1, format="full"))
    assert compact.actual_range == full.actual_range and compact.next_cursor == full.next_cursor
    assert compact.items[0].text == full.items[0].text
    assert "source_position" not in compact.model_dump()["items"][0]
    assert "source_position" in full.model_dump()["items"][0]
    browse = library.browse(LibraryBrowse(view="parts", work_id=books[0].work.id)).model_dump()
    assert set(browse) == {"view", "work", "parts"}
    assert set(browse["parts"]["items"][0]) == {"id", "name", "ordinal"}
    jobs = library.browse(LibraryBrowse(view="jobs", work_id=books[0].work.id)).model_dump()
    assert jobs["jobs"]["items"][0]["target"]["part_id"] == books[0].part.id


def test_scope_and_exclusion_do_not_discard_unread_context(
    database: Database, tmp_path: Path
) -> None:
    model, prepare, books = setup_books(database, tmp_path)
    service, library = ReferenceService(database, model), LibraryService(database, model)
    sections = library.browse(LibraryBrowse(view="sections", work_id=books[0].work.id)).sections
    assert sections is not None
    chapter = sections.items[0].id
    scope = [ReferenceScope(work_id=books[0].work.id, section_ids=[chapter])]
    read = library.read(SourceRead(work_id=books[0].work.id, section_id=chapter))
    start, end = read.items[0].id, read.items[-1].id
    first = SourceRange(
        work_id=books[0].work.id,
        section_id=chapter,
        start_paragraph_id=start,
        end_paragraph_id=start,
    )
    last = first.model_copy(update={"start_paragraph_id": end, "end_paragraph_id": end})
    partial = service.query(ReferenceQuery(query="雨声", scope=scope, exclude_ranges=[first]))
    assert partial.items and partial.items[0].source_range.end_paragraph_id == end
    outsider = batch_for(prepare, books[1]).source_range
    excluded = service.query(
        ReferenceQuery(query="雨声", scope=scope, exclude_ranges=[first, last, first, outsider])
    )
    assert not excluded.items and excluded.next_cursor is None
    assert library.read(SourceRead(**first.model_dump())).items[0].text == read.items[0].text
    with database.engine.connect() as conn:
        assert (
            conn.execute(
                text("SELECT count(*) FROM analysis_coverage WHERE job_id=:j"),
                {"j": books[0].job.id},
            ).scalar()
            == 0
        )
    wrong = ReferenceQuery(
        query="雨声", scope=[ReferenceScope(work_id=books[1].work.id, section_ids=[chapter])]
    )
    with pytest.raises(ServiceError) as error:
        service.query(wrong)
    assert error.value.code == "SECTION_NOT_FOUND"


def test_exclusion_before_candidate_window(
    database: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = DeterministicModel()
    prepare = PreparationService(database, 1024 * 1024, model)
    book = import_book(prepare, tmp_path, "雨声一\n标题：第二章\n雨声二\n标题：第三章\n雨声三")
    drain(prepare, book, model)
    service, library = ReferenceService(database, model), LibraryService(database, model)
    monkeypatch.setattr("novel_lens.reference.CHANNEL_WINDOW", 1)
    request = ReferenceQuery(query="雨声", scope=[ReferenceScope(work_id=book.work.id)], limit=1)
    first = service.query(request)
    assert first.candidate_window_limited
    next_search = service.query(
        request.model_copy(update={"exclude_ranges": [first.items[0].source_range]})
    )
    assert next_search.items[0].source_range != first.items[0].source_range
    assert (
        "二"
        in library.read(SourceRead(**next_search.items[0].source_range.model_dump())).items[0].text
    )


def test_snapshot_business_changes_and_pure_index_progress(
    database: Database, tmp_path: Path
) -> None:
    model, prepare, books = setup_books(database, tmp_path)
    service = ReferenceService(database, model)
    request = ReferenceQuery(
        query="雨声", scope=[ReferenceScope(work_id=books[0].work.id)], limit=1
    )
    first = service.query(request)
    with database.engine.begin() as conn:
        conn.execute(
            text("UPDATE reference_queue SET indexed_revision=0 WHERE work_id=:w"),
            {"w": books[0].work.id},
        )
    assert (
        service.query(ReferenceQuery(search_id=first.search_id)).model_dump() == first.model_dump()
    )
    prepare.batch(batch_for(prepare, books[0]))  # 零标记仅推进阅读进度。
    assert (
        service.query(ReferenceQuery(search_id=first.search_id)).model_dump() == first.model_dump()
    )
    manager = WorkManagementService(database)
    manager.set_visibility(books[0].work.id, "hidden")
    manager.set_visibility(books[0].work.id, "visible")

    def stale_read(_: int) -> str:
        with pytest.raises(ServiceError) as stale:
            service.query(ReferenceQuery(search_id=first.search_id))
        return stale.value.code

    with ThreadPoolExecutor(max_workers=3) as pool:
        assert list(pool.map(stale_read, range(3))) == ["REFERENCE_SEARCH_STALE"] * 3
    fresh = service.query(request)
    with database.engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE reference_searches SET snapshot_at=now()-interval '2 hours',"
                "expires_at=now()-interval '1 hour' WHERE id=:id"
            ),
            {"id": fresh.search_id},
        )
    with pytest.raises(ServiceError) as expired:
        service.query(ReferenceQuery(search_id=fresh.search_id))
    assert expired.value.code == "REFERENCE_SEARCH_UNAVAILABLE"
    service.sessions.cleanup()
    with database.engine.connect() as conn:
        assert (
            conn.execute(
                text("SELECT count(*) FROM reference_searches WHERE id=ANY(CAST(:ids AS uuid[]))"),
                {"ids": [first.search_id, fresh.search_id]},
            ).scalar()
            == 0
        )

    contract_page = service.query(request)
    old_contract = model.contract_id
    model.contract_id = "0" * 64
    with pytest.raises(ServiceError) as contract_change:
        service.query(ReferenceQuery(search_id=contract_page.search_id))
    assert contract_change.value.code == "REFERENCE_SEARCH_STALE"
    model.contract_id = old_contract
    append_page = service.query(request)
    appended = tmp_path / "追加.txt"
    appended.write_text("分部：新卷\n标题：新章\n又下雨了", encoding="utf-8")
    prepare.import_file(
        PrepareImport(work_id=books[0].work.id, request_id=uuid4(), file_path=str(appended))
    )
    with pytest.raises(ServiceError) as append_change:
        service.query(ReferenceQuery(search_id=append_page.search_id))
    assert append_change.value.code == "REFERENCE_SEARCH_STALE"
    delete_page = service.query(
        ReferenceQuery(query="雨声", scope=[ReferenceScope(work_id=books[1].work.id)])
    )
    manager.delete(books[1].work.id, confirm_name=books[1].work.name)
    with pytest.raises(ServiceError) as deleted:
        service.query(ReferenceQuery(search_id=delete_page.search_id))
    assert deleted.value.code in {"REFERENCE_SEARCH_STALE", "REFERENCE_SEARCH_UNAVAILABLE"}


@pytest.mark.parametrize(
    "changes",
    [
        {"work_id": uuid4()},
        {"scope": []},
        {"scope": [{"work_id": uuid4(), "part_ids": []}]},
        {"search_id": uuid4()},
        {"cursor": "arbitrary"},
    ],
)
def test_invalid_query_cannot_widen_or_mix_operations(changes: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ReferenceQuery.model_validate({"scope": [{"work_id": uuid4()}], "query": "雨声"} | changes)
