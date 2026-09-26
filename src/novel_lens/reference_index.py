"""自动派生索引执行；每次发布一个完整批次，不在网络推理期间持有写事务。"""

import asyncio
import json
import logging
from typing import Any, Literal
from uuid import UUID, uuid4

from sqlalchemy import Connection, text
from sqlalchemy.exc import SQLAlchemyError

from novel_lens.database import Database
from novel_lens.embedding import MAX_PARAGRAPH_BYTES, MAX_TOKENS, EmbeddingClient
from novel_lens.errors import ServiceError
from novel_lens.reading import work_at
from novel_lens.reference_contracts import ReferenceState
from novel_lens.semantic import SemanticService, lock_key
from novel_lens.semantic_contracts import SemanticBuild, SemanticCreate, SemanticGet

# 标签名称、定义与别名作为独立检索线索，保持输入原义，不拼入原文向量。
# 引用身份也参与摘要，避免同样说明被重新指向另一位置后仍使用旧候选。
CLUES_SQL = """
WITH clue_bodies AS (
 SELECT a.id,a.work_id,a.version,
   coalesce(a.note,'') || E'\\n' || coalesce((
     SELECT string_agg(t.namespace || '/' || t.name || E'\\n' || t.description || E'\\n'
       || coalesce((SELECT string_agg(alias,E'\\n' ORDER BY alias)
           FROM jsonb_array_elements_text(t.aliases) AS names(alias)),''),E'\\n' ORDER BY t.id)
     FROM annotation_tags at JOIN tags t ON t.id=at.tag_id WHERE at.annotation_id=a.id
   ),'') AS body,
   coalesce((SELECT string_agg(r.section_id::text || ':' || r.start_paragraph_id::text
      || ':' || r.end_paragraph_id::text,',' ORDER BY r.ordinal)
     FROM annotation_ranges r WHERE r.annotation_id=a.id),'') AS refs
 FROM annotations a WHERE a.work_id=:work AND a.status='active'
), current_clues AS (
 SELECT *,encode(sha256(convert_to(version::text || ':' || body || ':' || refs,'UTF8')),'hex')
   AS fingerprint FROM clue_bodies WHERE btrim(body,E' \\t\\n\\r')<>''
)
"""


def index_state(conn: Connection, work_id: UUID, contract_id: str) -> ReferenceState:
    """同事务内读取当前覆盖，不依赖旧的构建回执，也不发起模型调用。"""
    work_at(conn, work_id)
    queue = (
        conn.execute(
            text(
                "SELECT revision,indexed_revision,last_error FROM reference_queue WHERE work_id=:w"
            ),
            {"w": work_id},
        )
        .mappings()
        .first()
    )
    source = (
        conn.execute(
            text("""
      SELECT i.status,i.last_error,i.source_sha256=w.source_sha256 AS current,
             i.contract_id=:contract AS compatible
      FROM semantic_index_heads h JOIN semantic_indexes i ON i.id=h.active_index_id
      JOIN works w ON w.id=h.work_id WHERE h.work_id=:w AND h.kind='fulltext'
    """),
            {"w": work_id, "contract": contract_id},
        )
        .mappings()
        .first()
    )
    ready = bool(
        source and source["status"] == "ready" and source["current"] and source["compatible"]
    )
    clues = (
        conn.execute(
            text(
                CLUES_SQL
                + """
      SELECT count(*) AS total,
        count(*) FILTER (WHERE c.embedding IS NOT NULL AND c.fingerprint=a.fingerprint
          AND c.contract_id=:contract) AS covered,
        count(*) FILTER (WHERE c.blocked_reason IS NOT NULL AND c.fingerprint=a.fingerprint
          AND c.contract_id=:contract) AS blocked
      FROM current_clues a LEFT JOIN reference_clues c ON c.annotation_id=a.id
    """
            ),
            {"work": work_id, "contract": contract_id},
        )
        .mappings()
        .one()
    )
    clue_ready = clues["total"] == clues["covered"]
    error = queue["last_error"] if queue else None
    blocked = error in {"INDEX_BLOCKED", "INPUT_TOO_LONG", "TOKENIZATION_INPUT_TOO_LARGE"}
    state: Literal["pending", "ready", "failed", "blocked"] = (
        "blocked"
        if blocked
        else "failed"
        if error
        else "ready"
        if ready and clue_ready and queue and queue["indexed_revision"] == queue["revision"]
        else "pending"
    )
    return ReferenceState(
        source_ready=ready,
        clues_ready=clue_ready,
        state=state,
        last_error=error,
        revision=queue["revision"] if queue else 0,
        indexed_revision=queue["indexed_revision"] if queue else 0,
        source_coverage={"ready": ready, "clues": dict(clues)},
    )


class ReferenceIndexer:
    """服务内有界执行器；数据库会话锁保证多个服务不会同时处理同一作品。"""

    def __init__(self, database: Database, model: EmbeddingClient) -> None:
        self.database = database
        self.model = model
        self.semantic = SemanticService(database, model)

    def tick(self) -> bool:
        """处理一个作品的一个批次；无任务返回 False，失败保留待办并延后重试。"""
        with self.database.engine.connect() as conn:
            with conn.begin():
                row = (
                    conn.execute(
                        text("""
                  SELECT work_id,revision FROM reference_queue
                  WHERE indexed_revision<>revision AND retry_at<=now()
                  ORDER BY retry_at,work_id LIMIT 1
                """)
                    )
                    .mappings()
                    .first()
                )
                if row is None:
                    return False
                work_id, revision = row["work_id"], row["revision"]
                key = lock_key(work_id, "reference")
                acquired = conn.execute(
                    text("SELECT pg_try_advisory_lock(:k)"), {"k": key}
                ).scalar()
            if not acquired:
                return False
            try:
                self._process(conn, work_id, revision)
            except ServiceError as exc:
                conn.rollback()
                if not conn.invalidated:
                    with conn.begin():
                        conn.execute(
                            text("""
                         UPDATE reference_queue SET last_error=:error,
                           retry_at=CASE WHEN :blocked THEN 'infinity'::timestamptz
                             ELSE now()+interval '30 seconds' END
                         WHERE work_id=:work AND revision=:revision
                        """),
                            {
                                "error": exc.code,
                                "work": work_id,
                                "revision": revision,
                                "blocked": exc.code
                                in {
                                    "INDEX_BLOCKED",
                                    "INPUT_TOO_LONG",
                                    "TOKENIZATION_INPUT_TOO_LARGE",
                                },
                            },
                        )
            finally:
                if not conn.invalidated:
                    try:
                        conn.rollback()
                        conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})
                        conn.commit()
                    except SQLAlchemyError:
                        conn.invalidate()
        return True

    def _process(self, conn: Connection, work_id: UUID, revision: int) -> None:
        """先补原文，再补独立说明向量；每步都有持久结果，进程退出不丢已完成批次。"""
        status = self.semantic.get(SemanticGet(work_id=work_id))
        active = status.active
        if not (
            active
            and active.coverage.complete
            and not active.source_stale
            and active.contract_id == self.model.contract_id
        ):
            target = status.target
            if (
                target is None
                or target.source_stale
                or target.contract_id != self.model.contract_id
            ):
                result = self.semantic.create(
                    SemanticCreate(work_id=work_id, request_id=uuid4(), reuse_existing=True)
                )
                index_id = result.index_id
            else:
                index_id = target.index_id
            result = self.semantic.build(
                SemanticBuild(work_id=work_id, index_id=index_id, request_id=uuid4())
            )
            if result.generation_status == "partial":
                raise ServiceError("INDEX_BLOCKED", "存在不能建立索引的原文范围", 409)
            if not result.coverage.complete:
                return
        self.model.verify()
        with conn.begin():
            rows = [
                dict(r)
                for r in conn.execute(
                    text(
                        CLUES_SQL
                        + """
              SELECT a.* FROM current_clues a LEFT JOIN reference_clues c ON c.annotation_id=a.id
              WHERE c.annotation_id IS NULL OR c.fingerprint<>a.fingerprint
                 OR c.contract_id<>:contract
              ORDER BY a.id LIMIT 4
            """
                    ),
                    {"work": work_id, "contract": self.model.contract_id},
                ).mappings()
            ]
        planned: list[tuple[dict[str, Any], list[int] | None, str | None]] = []
        budget = 0
        for row in rows:
            if len(row["body"].encode()) > MAX_PARAGRAPH_BYTES:
                planned.append((row, None, "TOKENIZATION_INPUT_TOO_LARGE"))
                continue
            tokens = self.model.tokenize(row["body"])
            if len(tokens) > MAX_TOKENS:
                planned.append((row, None, "INPUT_TOO_LONG"))
                continue
            if budget + len(tokens) > MAX_TOKENS:
                break
            budget += len(tokens)
            planned.append((row, tokens, None))
        inputs = [tokens for _, tokens, reason in planned if tokens is not None]
        vectors = iter(self.model.embed(inputs) if inputs else [])
        # 推理失败没有写入；成功后重新核验本次目标，不能在另一连接丢失锁后发布。
        if conn.invalidated:
            raise ServiceError("INDEX_BUSY", "索引锁连接已中断", 409)
        with conn.begin():
            current = conn.execute(
                text("SELECT revision FROM reference_queue WHERE work_id=:w FOR UPDATE"),
                {"w": work_id},
            ).scalar_one_or_none()
            if current != revision:
                return
            for row, planned_tokens, reason in planned:
                vector = json.dumps(next(vectors)) if planned_tokens is not None else None
                conn.execute(
                    text("""
                  INSERT INTO reference_clues
                    (annotation_id,work_id,fingerprint,contract_id,body,embedding,blocked_reason)
                  VALUES(:id,:work,:fingerprint,:contract,:body,CAST(:vector AS vector),:reason)
                  ON CONFLICT(annotation_id) DO UPDATE SET fingerprint=excluded.fingerprint,
                    contract_id=excluded.contract_id,body=excluded.body,embedding=excluded.embedding,
                    blocked_reason=excluded.blocked_reason
                """),
                    {
                        **row,
                        "work": work_id,
                        "contract": self.model.contract_id,
                        "vector": vector,
                        "reason": reason,
                    },
                )
            conn.execute(
                text(
                    CLUES_SQL
                    + """
              DELETE FROM reference_clues c WHERE c.work_id=:work
              AND NOT EXISTS(SELECT 1 FROM current_clues a WHERE a.id=c.annotation_id)
            """
                ),
                {"work": work_id},
            )
            state = index_state(conn, work_id, self.model.contract_id)
            if state.clues_ready and state.source_ready:
                conn.execute(
                    text(
                        "UPDATE reference_queue SET indexed_revision=revision,last_error=NULL "
                        "WHERE work_id=:w"
                    ),
                    {"w": work_id},
                )
            elif state.source_coverage["clues"]["blocked"]:
                conn.execute(
                    text(
                        "UPDATE reference_queue SET last_error='INDEX_BLOCKED',"
                        "retry_at='infinity'::timestamptz WHERE work_id=:w"
                    ),
                    {"w": work_id},
                )

    async def run(self, stop: asyncio.Event) -> None:
        """与服务生命周期一致；退出等待当前有界批次结束，不产生独立常驻 AI。"""
        while not stop.is_set():
            try:
                busy = await asyncio.to_thread(self.tick)
            except (ServiceError, SQLAlchemyError):
                logging.getLogger(__name__).warning("自动索引暂时不可用，待办保持未完成")
                busy = False
            try:
                await asyncio.wait_for(stop.wait(), timeout=0.1 if busy else 2.0)
            except TimeoutError:
                pass
