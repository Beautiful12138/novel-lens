"""标注范围身份、动态覆盖及推理期间编辑的真实数据库行为。"""

from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from test_semantic import DeterministicModel, build, imported, new_index

from novel_lens.asset_contracts import AnnotationCreate, AnnotationUpdate
from novel_lens.assets import AssetService
from novel_lens.contracts import SourceRange
from novel_lens.database import Database
from novel_lens.errors import ServiceError
from novel_lens.semantic import SemanticService
from novel_lens.semantic_contracts import (
    AnnotationSemanticBlocked,
    AnnotationSemanticCoverage,
    SemanticBuild,
    SemanticCreate,
    SemanticGet,
    SemanticSearch,
)


def references(database: Database, work: UUID) -> list[SourceRange]:
    with database.engine.connect() as conn:
        rows = conn.execute(
            text("""
            SELECT p.id,p.section_id FROM paragraphs p JOIN sections s ON s.id=p.section_id
            WHERE s.work_id=:work ORDER BY s.ordinal,p.ordinal
        """),
            {"work": work},
        ).mappings()
        return [
            SourceRange(
                work_id=work,
                section_id=r["section_id"],
                start_paragraph_id=r["id"],
                end_paragraph_id=r["id"],
            )
            for r in rows
        ]


def annotation(database: Database, work: UUID, refs: list[SourceRange]) -> Any:
    return (
        AssetService(database)
        .create_annotation(
            AnnotationCreate(work_id=work, request_id=uuid4(), source_ranges=refs, note="说明")
        )
        .result
    )


def edit(database: Database, old: Any, refs: list[SourceRange], note: str = "新说明") -> Any:
    return (
        AssetService(database)
        .update_annotation(
            AnnotationUpdate(
                work_id=old.work_id,
                request_id=uuid4(),
                annotation_id=old.id,
                expected_version=old.version,
                source_ranges=refs,
                note=note,
                tag_ids=[],
            )
        )
        .result
    )


def annotated_index(service: SemanticService, work: UUID) -> UUID:
    return service.create(
        SemanticCreate(work_id=work, kind="annotation", request_id=uuid4())
    ).index_id


def test_annotation_evidence_coverage_and_aggregation(database: Database) -> None:
    work = imported(database, "标题：甲\n首段\n不应被拼入\n末段\n标题：乙\n另章")
    refs = references(database, work)
    a = annotation(database, work, [refs[2], refs[0]])
    model = DeterministicModel()
    service = SemanticService(database, model)
    index = annotated_index(service, work)
    assert build(service, work, index).coverage.complete
    with database.engine.connect() as conn:
        rows = (
            conn.execute(
                text("SELECT * FROM semantic_index_items WHERE index_id=:id"), {"id": index}
            )
            .mappings()
            .all()
        )
        assert len(rows) == 2 and {r["start_paragraph_id"] for r in rows} == {
            refs[0].start_paragraph_id,
            refs[2].start_paragraph_id,
        }
        assert all(r["start_paragraph_id"] == r["end_paragraph_id"] for r in rows)
    query = SemanticSearch(work_id=work, kind="annotation", query="首段")
    hit = service.search(query).items
    assert len(hit) == 1 and hit[0].kind == "annotation" and hit[0].annotation_id == a.id
    calls = len(model.calls)
    a = edit(database, a, [refs[0], refs[2]])  # 调整数组顺序和说明，身份不变。
    status = service.get(SemanticGet(work_id=work, kind="annotation"))
    assert status.active and status.active.coverage.complete and len(model.calls) == calls
    current = service.search(query).items[0]
    assert current.kind == "annotation" and current.annotation_version == 2
    a = edit(database, a, [refs[3]])
    added = annotation(database, work, [refs[1]])
    status = service.get(SemanticGet(work_id=work, kind="annotation"))
    assert status.active and status.active.generation_status == "ready"
    assert status.active.coverage.model_dump() == dict(
        total=2, covered=0, stale=1, not_indexed=1, blocked=0, pending=0, complete=False
    )
    with pytest.raises(ServiceError) as incomplete:
        service.search(query)
    assert incomplete.value.code == "INDEX_INCOMPLETE"
    assert service.search(query.model_copy(update={"allow_partial": True})).items == []
    replacement = annotated_index(service, work)
    assert build(service, work, replacement).coverage.complete
    assert {h.annotation_id for h in service.search(query).items if h.kind == "annotation"} == {
        a.id,
        added.id,
    }
    full = new_index(service, work)
    assert build(service, work, full).coverage.complete
    full_active = service.get(SemanticGet(work_id=work)).active
    assert full_active is not None and full_active.index_id == full
    with pytest.raises(ServiceError) as wrong_layer:
        service.get(SemanticGet(work_id=work, index_id=replacement))
    assert wrong_layer.value.code == "INDEX_NOT_FOUND"


def test_annotation_edit_during_inference_discards_entire_batch(database: Database) -> None:
    work = imported(database, "标题：甲\n原引用\n新引用\n其他标注")
    refs = references(database, work)
    a = annotation(database, work, [refs[0]])
    annotation(database, work, [refs[2]])
    model = DeterministicModel()
    service = SemanticService(database, model)
    index = annotated_index(service, work)
    model.during_embed = lambda: edit(database, a, [refs[1]])
    request = SemanticBuild(work_id=work, index_id=index, request_id=uuid4())
    result = service.build(request)
    assert result.batch_discarded == 2 and result.batch_ready == 0
    assert result.batch_tokens > 0  # 已提交给模型的成本仍报告，不因丢弃结果清零。
    assert isinstance(result.coverage, AnnotationSemanticCoverage)
    assert result.coverage.stale == 1 and result.coverage.pending == 1
    assert service.build(request).replayed
    model.during_embed = None
    result = build(service, work, index)
    assert result.generation_status == "partial"
    assert result.coverage.covered == 1 and result.coverage.stale == 1
    hits = service.search(
        SemanticSearch(
            work_id=work, kind="annotation", index_id=index, query="原引用", allow_partial=True
        )
    ).items
    assert len(hits) == 1 and hits[0].kind == "annotation" and hits[0].annotation_id != a.id


def test_annotation_blocked_partial_scope_and_pagination(database: Database) -> None:
    work = imported(database, "标题：甲\n" + "长" * 4095 + "\n短段\n" + "巨" * 350000)
    refs = references(database, work)
    a = annotation(database, work, refs)
    model = DeterministicModel()
    service = SemanticService(database, model)
    index = annotated_index(service, work)
    result = build(service, work, index)
    assert result.coverage.blocked == 1 and result.coverage.pending == 0
    page = service.get(SemanticGet(work_id=work, kind="annotation", index_id=index, limit=1))
    assert isinstance(page.blocked[0], AnnotationSemanticBlocked)
    assert page.next_cursor and page.blocked[0].annotation_id == a.id
    following = service.get(
        SemanticGet(
            work_id=work, kind="annotation", index_id=index, cursor=page.next_cursor, limit=1
        )
    )
    assert len(following.blocked) == 1 and following.next_cursor is None
    assert {b.reason for b in page.blocked + following.blocked} == {
        "INPUT_TOO_LONG",
        "TOKENIZATION_INPUT_TOO_LARGE",
    }
    hits = service.search(
        SemanticSearch(work_id=work, kind="annotation", query="短段", allow_partial=True)
    ).items
    assert len(hits) == 1 and hits[0].source_range == refs[1]
    edit(database, a, [refs[1]])
    assert service.get(SemanticGet(work_id=work, kind="annotation", index_id=index)).blocked == []
    assert (
        service.search(
            SemanticSearch(work_id=work, kind="annotation", query="短段", allow_partial=True)
        ).items
        == []
    )


def test_annotation_windows_resume_note_edit_and_layer_lock(database: Database) -> None:
    """长范围重叠不漏段；推理时修改说明可发布，另一层锁不阻塞该层。"""
    from novel_lens.semantic import lock_key

    work = imported(database, "标题：甲\n" + "\n".join(["段" * 50] * 90) + "\n标题：乙\n尾段")
    refs = references(database, work)
    long_range = refs[0].model_copy(update={"end_paragraph_id": refs[89].end_paragraph_id})
    a = annotation(database, work, [long_range, refs[-1]])
    model = DeterministicModel()
    service = SemanticService(database, model)
    with database.engine.connect() as lock:
        lock.execute(text("SELECT pg_advisory_lock(:key)"), {"key": lock_key(work)})
        lock.commit()
        try:
            index = annotated_index(service, work)
            with pytest.raises(ServiceError) as busy:
                new_index(service, work)
            assert busy.value.code == "INDEX_BUSY"
        finally:
            lock.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": lock_key(work)})
            lock.commit()
    model.during_embed = lambda: edit(database, a, [long_range, refs[-1]])
    first = build(service, work, index, 1)
    assert first.batch_ready == 1 and first.batch_discarded == 0
    assert first.coverage.pending == 1
    model.during_embed = None
    for _ in range(12):
        result = build(service, work, index, 1)
        if result.coverage.complete:
            break
    assert result.coverage.complete and result.generation_status == "ready"
    assert all(len(batch) == 1 and sum(batch) <= 4095 for batch in model.calls)
    with database.engine.connect() as conn:
        rows = (
            conn.execute(
                text("""
            SELECT * FROM semantic_index_items WHERE index_id=:id
            ORDER BY range_ordinal,start_ordinal
        """),
                {"id": index},
            )
            .mappings()
            .all()
        )
        first_range = [r for r in rows if r["range_ordinal"] == 1]
        assert len(first_range) > 1
        assert any(
            left["end_ordinal"] >= right["start_ordinal"]
            for left, right in zip(first_range, first_range[1:], strict=False)
        )
        assert all(r["section_id"] == refs[0].section_id for r in first_range)
    current = service.search(SemanticSearch(work_id=work, kind="annotation", query="段")).items[0]
    assert current.kind == "annotation" and current.annotation_version == 2


def test_annotation_storage_failure_replay_scope_and_empty(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    work = imported(database, "标题：甲\n甲\n乙")
    other = imported(database, "标题：乙\n另一作品")
    refs = references(database, work)
    annotation(database, work, refs)
    annotation(database, work, [refs[0]])
    model = DeterministicModel()
    service = SemanticService(database, model)
    index = annotated_index(service, work)
    request = SemanticBuild(work_id=work, index_id=index, request_id=uuid4())
    insert = service._insert_chunk

    def failing(*args: Any) -> None:
        insert(*args)
        raise ServiceError("DATABASE_UNAVAILABLE", "模拟批次存储失败", 503)

    with monkeypatch.context() as patch:
        patch.setattr(service, "_insert_chunk", failing)
        with pytest.raises(ServiceError):
            service.build(request)
    with database.engine.connect() as conn:
        assert (
            conn.execute(
                text("SELECT count(*) FROM semantic_index_items WHERE index_id=:id"), {"id": index}
            ).scalar_one()
            == 0
        )
        assert (
            conn.execute(
                text("SELECT count(*) FROM semantic_build_receipts WHERE index_id=:id"),
                {"id": index},
            ).scalar_one()
            == 0
        )
    assert service.build(request).coverage.complete
    assert service.build(request).replayed
    assert (
        len(service.search(SemanticSearch(work_id=work, kind="annotation", query="甲")).items) == 2
    )
    with pytest.raises(ServiceError) as cross:
        service.search(SemanticSearch(work_id=other, kind="annotation", index_id=index, query="甲"))
    assert cross.value.code == "INDEX_NOT_FOUND"
    empty = annotated_index(service, other)
    assert build(service, other, empty).coverage.total == 0
    assert service.search(SemanticSearch(work_id=other, kind="annotation", query="甲")).items == []
