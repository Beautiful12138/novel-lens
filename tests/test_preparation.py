"""真实 PostgreSQL 验证每批原子性、幂等、自动索引和清理隔离。"""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from test_semantic import DeterministicModel

from novel_lens.asset_contracts import AnnotationSetStatus, Recovery, TagUpdate
from novel_lens.assets import AssetService
from novel_lens.database import Database
from novel_lens.errors import ServiceError
from novel_lens.preparation import PreparationService
from novel_lens.reference_contracts import (
    MarkInput,
    PreparationReceipt,
    PrepareBatch,
    PrepareCleanup,
    PreparedImport,
    PrepareFinish,
    PrepareImport,
    PrepareStatus,
    TagInput,
)
from novel_lens.reference_index import ReferenceIndexer


@pytest.fixture
def model() -> DeterministicModel:
    return DeterministicModel()


@pytest.fixture
def preparation(database: Database, model: DeterministicModel) -> PreparationService:
    return PreparationService(database, 1024 * 1024, model)


def import_book(
    service: PreparationService, tmp_path: Path, body: str = "甲\n乙"
) -> PreparedImport:
    path = tmp_path / f"{uuid4()}.txt"
    path.write_text("分部：第一卷\n标题：第一章\n" + body, encoding="utf-8")
    return service.import_file(
        PrepareImport(request_id=uuid4(), name=str(uuid4()), file_path=str(path))
    )


def batch_for(service: PreparationService, book: PreparedImport) -> PrepareBatch:
    status = service.status(PrepareStatus(work_id=book.work.id, job_id=book.job.id))
    return PrepareBatch(
        request_id=uuid4(),
        work_id=book.work.id,
        job_id=book.job.id,
        expected_version=status.job.version,
        source_range=status.remaining.items[0].source_range,
        recovery=Recovery(next_action="继续剩余范围"),
        outcome_note="已读完本批原文",
    )


def drain(service: PreparationService, book: PreparedImport, model: DeterministicModel) -> None:
    worker = ReferenceIndexer(service.database, model)
    for _ in range(80):
        worker.tick()
        if (
            service.status(PrepareStatus(work_id=book.work.id, job_id=book.job.id)).indexes.state
            == "ready"
        ):
            return
    pytest.fail("自动索引未在有界批次内就绪")


def test_atomic_import_replay_and_source_reuse(
    preparation: PreparationService, tmp_path: Path
) -> None:
    path = tmp_path / "source.txt"
    path.write_text("格式错误", encoding="utf-8")
    request = PrepareImport(request_id=uuid4(), name=str(uuid4()), file_path=str(path))
    with pytest.raises(ServiceError, match="格式"):
        preparation.import_file(request)
    with preparation.database.engine.connect() as conn:
        assert (
            conn.execute(
                text("SELECT count(*) FROM works WHERE name=:n"), {"n": request.name}
            ).scalar()
            == 0
        )
    path.write_text("分部：卷一\n标题：第一章\n完整原文", encoding="utf-8")
    book = preparation.import_file(request)
    assert book.job.target.part_id == book.part.id
    assert preparation.import_file(request).replayed
    duplicate = preparation.import_file(
        PrepareImport(request_id=uuid4(), work_id=book.work.id, file_path=str(path))
    )
    assert duplicate.part.id == book.part.id and duplicate.job.id == book.job.id
    path.unlink()
    receipt = preparation.receipt(PreparationReceipt(request_id=request.request_id))
    assert isinstance(receipt, PreparedImport) and receipt.part.id == book.part.id


def test_batch_failure_preserves_previous_and_rolls_back_everything(
    preparation: PreparationService,
    tmp_path: Path,
) -> None:
    book = import_book(preparation, tmp_path)
    batch = batch_for(preparation, book)
    ref = batch.source_range
    first = batch.model_copy(
        update={"source_range": ref.model_copy(update={"end_paragraph_id": ref.start_paragraph_id})}
    )
    saved = preparation.batch(first)
    assert preparation.batch(first).replayed
    second = batch_for(preparation, book)
    tag = TagInput(namespace="写法", name=str(uuid4()), description="可核对的写法")
    bad = second.model_copy(
        update={
            "marks": [
                MarkInput(source_ranges=[ref], tags=[tag], note="有效标记"),
                MarkInput(
                    source_ranges=[ref.model_copy(update={"end_paragraph_id": uuid4()})],
                    note="无效引用",
                ),
            ]
        }
    )
    with pytest.raises(ServiceError):
        preparation.batch(bad)
    state = preparation.status(PrepareStatus(work_id=book.work.id, job_id=book.job.id))
    assert state.job.version == saved.version and state.job.counts.processed == 1
    with preparation.database.engine.connect() as conn:
        assert (
            conn.execute(
                text("SELECT count(*) FROM annotations WHERE work_id=:w"), {"w": book.work.id}
            ).scalar()
            == 0
        )
        assert (
            conn.execute(text("SELECT count(*) FROM tags WHERE name=:n"), {"n": tag.name}).scalar()
            == 0
        )
    fixed = bad.model_copy(update={"marks": bad.marks[:1]})
    result = preparation.batch(fixed)
    assert result.version == saved.version + 1
    assert preparation.batch(fixed).annotations == result.annotations
    assert (
        preparation.status(PrepareStatus(work_id=book.work.id, job_id=book.job.id)).remaining.items
        == []
    )


def test_concurrent_version_and_exact_retry(
    preparation: PreparationService, tmp_path: Path
) -> None:
    book = import_book(preparation, tmp_path)
    batch = batch_for(preparation, book)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(preparation.batch, [batch, batch]))
    assert sorted(r.replayed for r in results) == [False, True]
    with pytest.raises(ServiceError) as error:
        preparation.batch(batch.model_copy(update={"request_id": uuid4()}))
    assert error.value.code == "VERSION_CONFLICT"


def test_completed_revision_is_atomic_and_replay_safe(
    preparation: PreparationService, tmp_path: Path, model: DeterministicModel
) -> None:
    book = import_book(preparation, tmp_path)
    batch = batch_for(preparation, book)
    saved = preparation.batch(
        batch.model_copy(
            update={"marks": [MarkInput(source_ranges=[batch.source_range], note="初稿")]}
        )
    )
    drain(preparation, book, model)
    finish = PrepareFinish(
        request_id=uuid4(),
        work_id=book.work.id,
        job_id=book.job.id,
        expected_version=saved.version,
        recovery=Recovery(next_action="完成"),
        note="已核对",
    )
    completed = preparation.finish(finish).job
    revision = batch.model_copy(
        update={
            "request_id": uuid4(),
            "expected_version": completed.version,
            "marks": [
                MarkInput(
                    annotation_id=saved.annotations[0].id,
                    expected_version=1,
                    source_ranges=[batch.source_range],
                    note="修订说明",
                )
            ],
        }
    )
    with pytest.raises(ServiceError) as missing:
        preparation.batch(revision)
    assert missing.value.code == "JOB_STATE_CONFLICT"
    revision = revision.model_copy(update={"reopen_reason": "修正已完成标记的解释"})
    bad = revision.model_copy(
        update={"marks": [revision.marks[0].model_copy(update={"expected_version": 99})]}
    )
    with pytest.raises(ServiceError):
        preparation.batch(bad)
    status_request = PrepareStatus(work_id=book.work.id, job_id=book.job.id)
    assert preparation.status(status_request).job == completed
    changed = preparation.batch(revision)
    assert changed.version == completed.version + 1
    state = preparation.status(status_request)
    assert state.job.status == "running" and state.job.completion is None
    assert state.job.counts == completed.counts and not state.work_ready
    assert preparation.batch(revision).replayed
    drain(preparation, book, model)
    final = preparation.finish(
        finish.model_copy(update={"request_id": uuid4(), "expected_version": changed.version})
    )
    assert preparation.batch(revision).replayed
    assert preparation.status(status_request).job == final.job
    assert preparation.status(status_request).work_ready


def test_auto_index_finish_updates_and_append_reuses_source(
    preparation: PreparationService,
    tmp_path: Path,
    model: DeterministicModel,
) -> None:
    book = import_book(preparation, tmp_path)
    batch = batch_for(preparation, book)
    mark = MarkInput(
        source_ranges=[batch.source_range],
        note="用动作表现迟疑",
        tags=[TagInput(namespace="写法", name=str(uuid4()), description="动作线索")],
    )
    saved = preparation.batch(batch.model_copy(update={"marks": [mark]}))
    finish = PrepareFinish(
        request_id=uuid4(),
        work_id=book.work.id,
        job_id=book.job.id,
        expected_version=saved.version,
        recovery=Recovery(next_action="准备完成"),
        note="全部原文已核对",
    )
    with pytest.raises(ServiceError) as pending:
        preparation.finish(finish)
    assert pending.value.code == "PREPARATION_INDEX_PENDING"
    drain(preparation, book, model)
    assert preparation.finish(finish).job.status == "completed"
    assert preparation.status(PrepareStatus(work_id=book.work.id, job_id=book.job.id)).work_ready
    assert preparation.finish(finish).replayed
    assets = AssetService(preparation.database)
    tag = assets.get_tag(saved.annotations[0].tag_ids[0])
    before = len(model.calls)
    assets.update_tag(
        TagUpdate(
            request_id=uuid4(),
            tag_id=tag.id,
            expected_version=tag.version,
            name=tag.name + "修订",
            description=tag.description,
            aliases=[],
        )
    )
    assert not preparation.status(
        PrepareStatus(work_id=book.work.id, job_id=book.job.id)
    ).indexes.clues_ready
    drain(preparation, book, model)
    assert len(model.calls) == before + 1  # 只重算线索，原文向量保持。
    assets.set_annotation_status(
        AnnotationSetStatus(
            request_id=uuid4(),
            work_id=book.work.id,
            annotation_id=saved.annotations[0].id,
            expected_version=saved.annotations[0].version,
            status="withdrawn",
        )
    )
    drain(preparation, book, model)
    with preparation.database.engine.connect() as conn:
        assert (
            conn.execute(
                text("SELECT count(*) FROM reference_clues WHERE work_id=:w"), {"w": book.work.id}
            ).scalar()
            == 0
        )
    before = len(model.calls)
    path = tmp_path / "append.txt"
    path.write_text("分部：第二卷\n标题：第二章\n新段落", encoding="utf-8")
    appended = preparation.import_file(
        PrepareImport(request_id=uuid4(), work_id=book.work.id, file_path=str(path))
    )
    assert not preparation.status(
        PrepareStatus(work_id=book.work.id, job_id=book.job.id)
    ).work_ready
    drain(preparation, appended, model)
    assert len(model.calls) == before + 1
    assert (
        preparation.status(PrepareStatus(work_id=book.work.id, job_id=book.job.id)).job.status
        == "completed"
    )


def test_failed_and_stale_vector_batches_can_continue(
    preparation: PreparationService,
    tmp_path: Path,
    model: DeterministicModel,
) -> None:
    book = import_book(preparation, tmp_path)
    drain(preparation, book, model)
    batch = batch_for(preparation, book)
    saved = preparation.batch(
        batch.model_copy(
            update={
                "marks": [
                    MarkInput(source_ranges=[batch.source_range], note=f"线索 {n}")
                    for n in range(4)
                ]
            }
        )
    )
    model.fail = True
    worker = ReferenceIndexer(preparation.database, model)
    worker.tick()
    state = preparation.status(PrepareStatus(work_id=book.work.id, job_id=book.job.id))
    assert state.indexes.last_error == "EMBEDDING_UNAVAILABLE"
    assert state.job.counts.processed == 2
    model.fail = False
    with preparation.database.engine.begin() as conn:
        assert (
            conn.execute(
                text("SELECT count(*) FROM reference_clues WHERE work_id=:w"), {"w": book.work.id}
            ).scalar()
            == 0
        )
        conn.execute(
            text("UPDATE reference_queue SET retry_at=now() WHERE work_id=:w"), {"w": book.work.id}
        )

    def change_during_inference() -> None:
        model.during_embed = None
        preparation.batch(
            batch.model_copy(
                update={
                    "request_id": uuid4(),
                    "expected_version": saved.version,
                    "marks": [
                        MarkInput(
                            annotation_id=saved.annotations[0].id,
                            expected_version=saved.annotations[0].version,
                            source_ranges=[batch.source_range],
                            note="推理期间修订的线索",
                        )
                    ],
                }
            )
        )

    model.during_embed = change_during_inference
    ReferenceIndexer(preparation.database, model).tick()
    with preparation.database.engine.connect() as conn:
        assert (
            conn.execute(
                text("SELECT count(*) FROM reference_clues WHERE work_id=:w"), {"w": book.work.id}
            ).scalar()
            == 0
        )
    drain(preparation, book, model)
    with preparation.database.engine.connect() as conn:
        body = conn.execute(
            text("SELECT body FROM reference_clues WHERE annotation_id=:a"),
            {"a": saved.annotations[0].id},
        ).scalar_one()
    assert "推理期间修订" in body


def test_blocked_clue_repair_requeues(
    preparation: PreparationService, tmp_path: Path, model: DeterministicModel
) -> None:
    book = import_book(preparation, tmp_path)
    drain(preparation, book, model)
    batch = batch_for(preparation, book)
    saved = preparation.batch(
        batch.model_copy(
            update={"marks": [MarkInput(source_ranges=[batch.source_range], note="长" * 5000)]}
        )
    )
    ReferenceIndexer(preparation.database, model).tick()
    state = preparation.status(PrepareStatus(work_id=book.work.id, job_id=book.job.id))
    assert state.indexes.state == "blocked" and not state.work_ready
    preparation.batch(
        batch.model_copy(
            update={
                "request_id": uuid4(),
                "expected_version": saved.version,
                "marks": [
                    MarkInput(
                        annotation_id=saved.annotations[0].id,
                        expected_version=1,
                        source_ranges=[batch.source_range],
                        note="修正为简短且有原文依据的线索",
                    )
                ],
            }
        )
    )
    drain(preparation, book, model)


def test_cleanup_mid_failure_rolls_back_and_preserves_shared_tag(
    preparation: PreparationService,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import novel_lens.work_management as management

    book = import_book(preparation, tmp_path)
    other = import_book(preparation, tmp_path)
    tag = TagInput(namespace="写法", name=str(uuid4()), description="共享线索")
    for target in (book, other):
        batch = batch_for(preparation, target)
        preparation.batch(
            batch.model_copy(
                update={"marks": [MarkInput(source_ranges=[batch.source_range], tags=[tag])]}
            )
        )
    request = PrepareCleanup(work_id=book.work.id, confirm_work_name=book.work.name)
    with monkeypatch.context() as patch:
        patch.setattr(
            management,
            "DELETE_SCOPE",
            management.DELETE_SCOPE[:5] + (("missing_cleanup_table", "true"),),
        )
        with pytest.raises(SQLAlchemyError):
            preparation.cleanup(request)
    assert (
        preparation.status(
            PrepareStatus(work_id=book.work.id, job_id=book.job.id)
        ).job.counts.processed
        == 2
    )
    preparation.cleanup(request)
    with preparation.database.engine.connect() as conn:
        assert (
            conn.execute(text("SELECT count(*) FROM tags WHERE name=:n"), {"n": tag.name}).scalar()
            == 1
        )


def test_cleanup_scope_and_failure_rollback(
    preparation: PreparationService, tmp_path: Path
) -> None:
    book = import_book(preparation, tmp_path)
    other = import_book(preparation, tmp_path)
    before_files = list(tmp_path.glob("*.txt"))
    batch = batch_for(preparation, book)
    tag = TagInput(namespace="写法", name=str(uuid4()), description="清理范围验证")
    saved = preparation.batch(
        batch.model_copy(
            update={"marks": [MarkInput(source_ranges=[batch.source_range], tags=[tag])]}
        )
    )
    with pytest.raises(ServiceError, match="名称"):
        preparation.cleanup(PrepareCleanup(work_id=book.work.id, confirm_work_name="错误名称"))
    assert (
        preparation.status(PrepareStatus(work_id=book.work.id, job_id=book.job.id)).job.version
        == saved.version
    )
    request = PrepareCleanup(work_id=book.work.id, confirm_work_name=book.work.name)
    assert preparation.cleanup(request).deleted and preparation.cleanup(request).deleted
    assert (
        preparation.status(PrepareStatus(work_id=other.work.id, job_id=other.job.id)).job.id
        == other.job.id
    )
    assert all(path.exists() for path in before_files)
    with preparation.database.engine.connect() as conn:
        assert (
            conn.execute(text("SELECT count(*) FROM tags WHERE name=:n"), {"n": tag.name}).scalar()
            == 0
        )
