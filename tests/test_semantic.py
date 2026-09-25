"""在真实 PostgreSQL 上验证索引事务、接续、作品隔离与完整段落覆盖。"""

import json
from collections.abc import Callable
from typing import Any
from uuid import UUID, uuid4

import pytest
from part_fixtures import import_work
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from novel_lens.database import Database
from novel_lens.embedding import EmbeddingClient
from novel_lens.errors import ServiceError
from novel_lens.importing import ImportService
from novel_lens.semantic import SemanticService, lock_key
from novel_lens.semantic_contracts import (
    SemanticBuild,
    SemanticCreate,
    SemanticGet,
    SemanticSearch,
)


class DeterministicModel(EmbeddingClient):
    """只用于机械边界与故障注入；真实向量另由可选部署验收覆盖。"""

    def __init__(self) -> None:
        super().__init__(None)
        self.fail = False
        self.calls: list[list[int]] = []
        self.during_embed: Callable[[], None] | None = None

    def verify(self) -> None:
        if self.fail:
            raise ServiceError("EMBEDDING_UNAVAILABLE", "测试端点不可用", 503)

    def tokenize(self, value: str) -> list[int]:
        return [1] + list(map(ord, value))

    def embed(self, inputs: list[list[int]]) -> list[list[float]]:
        self.calls.append([len(row) for row in inputs])
        if self.during_embed:
            self.during_embed()
        self.verify()
        return [[1.0] + [0.0] * 1023 for _ in inputs]


def imported(database: Database, body: str) -> UUID:
    data = f"分部：{uuid4()}\n{body}".encode()
    return import_work(ImportService(database, 64 * 1024 * 1024), data, uuid4()).work.id


def new_index(service: SemanticService, work: UUID) -> UUID:
    return service.create(SemanticCreate(work_id=work, request_id=uuid4())).index_id


def build(service: SemanticService, work: UUID, index: UUID, maximum: int = 4) -> Any:
    return service.build(
        SemanticBuild(work_id=work, index_id=index, request_id=uuid4(), max_items=maximum)
    )


def test_generation_replay_failure_switch_and_scope(database: Database) -> None:
    model = DeterministicModel()
    service = SemanticService(database, model)
    work = imported(database, "标题：甲\n原样 雨\n标题：乙\n另一个段落")
    other = imported(database, "标题：甲\n其他作品")
    assert service.get(SemanticGet(work_id=work)).state == "missing"
    creation = SemanticCreate(work_id=work, request_id=uuid4())
    initial = service.create(creation)
    request = SemanticBuild(
        work_id=work, index_id=initial.index_id, request_id=uuid4(), max_items=1
    )
    first = service.build(request)
    assert first.generation_status == "building" and first.coverage.covered == 1
    assert service.build(request).model_dump(exclude={"replayed"}) == first.model_dump(
        exclude={"replayed"}
    )
    assert len(model.calls) == 1
    with pytest.raises(ServiceError, match="参数不一致"):
        service.build(request.model_copy(update={"max_items": 2}))
    model.fail = True
    retry = request.model_copy(update={"request_id": uuid4()})
    with pytest.raises(ServiceError) as failure:
        service.build(retry)
    assert failure.value.code == "EMBEDDING_UNAVAILABLE"
    status = service.get(SemanticGet(work_id=work))
    assert status.target and status.target.generation_status == "failed"
    assert status.target.coverage.covered == 1
    assert service.create(creation).replayed  # 已提交回执不依赖模型在线。
    model.fail = False
    ready = service.build(retry)
    assert ready.generation_status == "ready" and ready.coverage.complete
    assert service.create(creation).generation_status == "building"  # 原快照，不冒充最新状态。
    query = SemanticSearch(work_id=work, query="雨")
    result = service.search(query)
    assert len(result.items) == 2 and all(hit.source_range.work_id == work for hit in result.items)
    with pytest.raises(ServiceError) as wrong:
        service.get(SemanticGet(work_id=other, index_id=initial.index_id))
    assert wrong.value.code == "INDEX_NOT_FOUND"
    rebuilding = new_index(service, work)
    assert service.search(query).index_id == initial.index_id
    assert build(service, work, rebuilding).generation_status == "ready"
    assert service.search(query).index_id == rebuilding
    superseded = new_index(service, work)
    replacement = new_index(service, work)
    with pytest.raises(ServiceError) as stale:
        build(service, work, superseded)
    assert stale.value.code == "INDEX_SUPERSEDED"
    target = service.get(SemanticGet(work_id=work)).target
    assert target is not None and target.index_id == replacement
    assert service.search(query).index_id == rebuilding


def test_chunks_overlap_gaps_long_paragraphs_and_pagination(database: Database) -> None:
    model = DeterministicModel()
    service = SemanticService(database, model)
    body = "标题：甲\n" + "\n".join(["短" * 50] * 40 + ["长" * 4095, "续" * 1200, "末尾"])
    work = imported(database, body + "\n标题：乙\n" + "巨" * 350000 + "\n终点")
    index = new_index(service, work)
    for _ in range(20):
        result = build(service, work, index, 2)
        if result.generation_status == "partial":
            break
    assert result.generation_status == "partial"
    assert result.coverage.total == 45 and result.coverage.covered == 43
    assert result.coverage.blocked == 2 and result.coverage.pending == 0
    assert all(sum(batch) <= 4095 and len(batch) <= 2 for batch in model.calls)
    blocked = service.get(SemanticGet(work_id=work, index_id=index, limit=1))
    assert blocked.next_cursor and len(blocked.blocked) == 1
    next_page = service.get(
        SemanticGet(work_id=work, index_id=index, limit=1, cursor=blocked.next_cursor)
    )
    all_blocks = blocked.blocked + next_page.blocked
    assert {item.reason for item in all_blocks} == {
        "INPUT_TOO_LONG",
        "TOKENIZATION_INPUT_TOO_LARGE",
    }
    assert {item.tokens for item in all_blocks} == {None, 4096}
    with pytest.raises(ServiceError) as incomplete:
        service.search(SemanticSearch(work_id=work, index_id=index, query="终点"))
    assert incomplete.value.code == "INDEX_INCOMPLETE"
    hits = service.search(SemanticSearch(work_id=work, query="终点", allow_partial=True))
    assert hits.partial and hits.items
    with database.engine.connect() as conn:
        ranges = (
            conn.execute(
                text("""
            SELECT i.*, s.ordinal AS section_ordinal FROM semantic_index_items i
            JOIN sections s ON s.id=i.section_id WHERE index_id=:id
            ORDER BY s.ordinal,i.start_ordinal
        """),
                {"id": index},
            )
            .mappings()
            .all()
        )
        good = [r for r in ranges if r["blocked_reason"] is None]
        assert good[0]["end_ordinal"] >= good[1]["start_ordinal"]  # 完整段落重叠存在。
        assert all(
            not (r["start_ordinal"] < 41 < r["end_ordinal"])
            for r in ranges
            if r["section_ordinal"] == 1
        )
        long = next(r for r in ranges if r["tokens"] == 1201)
        assert long["start_ordinal"] == long["end_ordinal"] == 42
        assert all(r["embedding"] is None for r in ranges if r["blocked_reason"])


def test_batch_token_budget_and_lock_release(database: Database) -> None:
    model = DeterministicModel()
    service = SemanticService(database, model)
    work = imported(database, "标题：甲\n" + "甲" * 3000 + "\n" + "乙" * 2000)
    with database.engine.connect() as holder:
        holder.execute(text("SELECT pg_advisory_lock(:key)"), {"key": lock_key(work)})
        holder.commit()
        try:
            with pytest.raises(ServiceError) as busy:
                new_index(service, work)
            assert busy.value.code == "INDEX_BUSY"
        finally:
            holder.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": lock_key(work)})
            holder.commit()
    index = new_index(service, work)

    def competing() -> None:
        with pytest.raises(ServiceError) as busy:
            new_index(service, work)
        assert busy.value.code == "INDEX_BUSY"
        with database.engine.connect() as conn:
            assert (
                conn.execute(
                    text("""
                SELECT count(*) FROM pg_stat_activity WHERE datname=current_database()
                AND pid<>pg_backend_pid() AND state='idle in transaction'
            """)
                ).scalar()
                == 0
            )

    model.during_embed = competing
    first = build(service, work, index)
    assert first.batch_ready == 1 and first.batch_tokens == 3001
    assert first.coverage.covered == 1
    assert build(service, work, index).generation_status == "ready"
    assert model.calls == [[3001], [2001]]
    new_index(service, work)  # 锁已在归还池前释放。


def test_storage_failure_rolls_back_whole_batch(database: Database) -> None:
    model = DeterministicModel()
    service = SemanticService(database, model)
    work = imported(database, "标题：甲\n第一段\n标题：乙\n第二段")
    index = new_index(service, work)
    function = "reject_semantic_" + uuid4().hex
    with database.engine.begin() as conn:
        conn.execute(
            text(f"""
            CREATE FUNCTION {function}() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
                IF NEW.index_id='{index}' AND EXISTS (
                    SELECT 1 FROM semantic_index_items WHERE index_id=NEW.index_id
                ) THEN RAISE EXCEPTION 'test batch failure'; END IF;
                RETURN NEW;
            END $$;
            CREATE TRIGGER {function} BEFORE INSERT ON semantic_index_items
                FOR EACH ROW EXECUTE FUNCTION {function}();
        """)
        )
    request = SemanticBuild(work_id=work, index_id=index, request_id=uuid4())
    try:
        with pytest.raises(SQLAlchemyError):
            service.build(request)
        with database.engine.connect() as conn:
            assert (
                conn.execute(
                    text("SELECT count(*) FROM semantic_index_items WHERE index_id=:id"),
                    {"id": index},
                ).scalar()
                == 0
            )
            assert (
                conn.execute(
                    text("SELECT count(*) FROM semantic_build_receipts WHERE index_id=:id"),
                    {"id": index},
                ).scalar()
                == 0
            )
    finally:
        with database.engine.begin() as conn:
            conn.execute(
                text(f"DROP TRIGGER {function} ON semantic_index_items; DROP FUNCTION {function}()")
            )
    assert service.build(request).generation_status == "ready"


def test_contract_rejection_and_schema_constraints(database: Database) -> None:
    model = DeterministicModel()
    service = SemanticService(database, model)
    work = imported(database, "标题：甲\n原文")
    index = new_index(service, work)
    build(service, work, index)
    model.contract_id = "different"
    with pytest.raises(ServiceError) as mismatch:
        service.search(SemanticSearch(work_id=work, query="原文"))
    assert mismatch.value.code == "EMBEDDING_CONTRACT_MISMATCH"
    for invalid in ([0.0] * 1024, [1.0] * 3, [float("nan")] + [0.0] * 1023):
        with pytest.raises(SQLAlchemyError), database.engine.begin() as conn:
            conn.execute(
                text("""
                UPDATE semantic_index_items SET embedding=CAST(:v AS vector) WHERE index_id=:id
            """),
                {"v": json.dumps(invalid), "id": index},
            )
    with pytest.raises(SQLAlchemyError), database.engine.begin() as conn:
        conn.execute(
            text("UPDATE semantic_index_items SET tokens=NULL WHERE index_id=:id"), {"id": index}
        )


def test_lock_disconnect_discards_model_result(database: Database) -> None:
    model = DeterministicModel()
    service = SemanticService(database, model)
    work = imported(database, "标题：甲\n连接中断前的原文")
    index = new_index(service, work)

    def disconnect() -> None:
        key = lock_key(work) & ((1 << 64) - 1)
        with database.engine.begin() as conn:
            killed = (
                conn.execute(
                    text("""
                SELECT pg_terminate_backend(pid) FROM pg_locks
                WHERE locktype='advisory' AND classid=:high AND objid=:low
                  AND objsubid=1 AND database=(SELECT oid FROM pg_database
                    WHERE datname=current_database())
            """),
                    {"high": key >> 32, "low": key & ((1 << 32) - 1)},
                )
                .scalars()
                .all()
            )
            assert killed == [True]

    model.during_embed = disconnect
    request = SemanticBuild(work_id=work, index_id=index, request_id=uuid4())
    with pytest.raises(SQLAlchemyError):
        service.build(request)
    state = service.get(SemanticGet(work_id=work))
    assert state.target and state.target.coverage.covered == 0
    model.during_embed = None
    assert service.build(request).generation_status == "ready"


def test_deduplicate_overlapping_ranges_without_merging_sections(database: Database) -> None:
    service = SemanticService(database, DeterministicModel())
    # 前块末尾八个短段被后块复用，交集占较短范围 8/9，应只保留一个候选。
    body = "标题：甲\n" + "\n".join(["大" * 900] + ["短" * 10] * 8 + ["新" * 150])
    work = imported(database, body + "\n标题：乙\n另一章")
    index = new_index(service, work)
    assert build(service, work, index).generation_status == "ready"
    result = service.search(SemanticSearch(work_id=work, query="处境"))
    assert len(result.items) == 2
    assert len({item.source_range.section_id for item in result.items}) == 2
    assert result.coverage.covered == 11
    assert not result.candidate_window_limited
