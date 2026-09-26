"""显式两层原文索引：会话锁保护代切换，短事务发布批次，精确余弦查询。"""

import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel
from sqlalchemy import Connection, text
from sqlalchemy.exc import SQLAlchemyError

from novel_lens import semantic_annotations as annotation_index
from novel_lens.asset_contracts import MAX_ASSET_RESULT_BYTES
from novel_lens.catalog import part_at
from novel_lens.cursors import decode_cursor, encode_cursor
from novel_lens.database import Database
from novel_lens.embedding import MAX_TOKENS, QUERY_PREFIX, EmbeddingClient, digest
from novel_lens.errors import ServiceError
from novel_lens.reading import searchable_work, work_at
from novel_lens.semantic_chunks import Chunk, next_chunk
from novel_lens.semantic_contracts import (
    SemanticBuild,
    SemanticCoverage,
    SemanticCreate,
    SemanticGet,
    SemanticResults,
    SemanticSearch,
    SemanticStatus,
    SemanticSummary,
    SemanticWrite,
)


def bounded[T: BaseModel](result: T) -> T:
    """HTTP 与 MCP 在业务层共享 1 MiB 限额，超限不提交不可回放的回执。"""
    if len(result.model_dump_json().encode()) > MAX_ASSET_RESULT_BYTES:
        raise ServiceError("RESULT_TOO_LARGE", "语义结果超过 1 MiB，请减小 limit")
    return result


def lock_key(work_id: UUID, kind: str = "fulltext") -> int:
    """业务命名空间参与哈希，避免与原有资产锁共享整数键。"""
    raw = hashlib.sha256(f"novel-lens:semantic:{kind}:{work_id}".encode()).digest()[:8]
    return int.from_bytes(raw, "big", signed=True)


class SemanticService:
    """只维护原文派生数据；原文视为不可变，发布前仍核对来源身份与端点归属。"""

    def __init__(self, database: Database, model: EmbeddingClient) -> None:
        self.database = database
        self.model = model

    @staticmethod
    def _schema(conn: Connection) -> None:
        ready = conn.execute(
            text("""
            SELECT (SELECT extversion='0.8.6' FROM pg_extension WHERE extname='vector')
                AND to_regclass('parts') IS NOT NULL
                AND to_regclass('semantic_indexes') IS NOT NULL
                AND to_regclass('semantic_index_heads') IS NOT NULL
                AND to_regclass('semantic_index_items') IS NOT NULL
                AND to_regclass('semantic_build_receipts') IS NOT NULL
                AND to_regclass('semantic_annotation_snapshots') IS NOT NULL
                AND EXISTS (SELECT 1 FROM pg_attribute
                    WHERE attrelid=to_regclass('annotations') AND attname='status'
                    AND NOT attisdropped)
        """)
        ).scalar()
        if not ready:
            raise ServiceError(
                "SEMANTIC_SCHEMA_NOT_READY", "请先安装 vector 并使用 0011 空库结构", 503
            )

    @contextmanager
    def _locked(self, work_id: UUID, kind: str = "fulltext") -> Iterator[Connection]:
        """独占一个池连接直到解锁；网络期间无事务，断线后绝不重新连接发布。"""
        with self.database.engine.connect() as conn:
            acquired = False
            try:
                with conn.begin():
                    work_at(conn, work_id)
                    self._schema(conn)
                    acquired = bool(
                        conn.execute(
                            text("SELECT pg_try_advisory_lock(:key)"),
                            {"key": lock_key(work_id, kind)},
                        ).scalar()
                    )
                if not acquired:
                    raise ServiceError("INDEX_BUSY", "该作品的索引正在创建或构建，请稍后重试", 409)
                yield conn
            finally:
                if acquired and not conn.invalidated:
                    try:
                        conn.rollback()
                        conn.execute(
                            text("SELECT pg_advisory_unlock(:key)"),
                            {"key": lock_key(work_id, kind)},
                        )
                        conn.commit()
                    except SQLAlchemyError:
                        conn.invalidate()

    @staticmethod
    def _head(conn: Connection, work_id: UUID, kind: str = "fulltext") -> dict[str, Any]:
        row = (
            conn.execute(
                text("""
            SELECT active_index_id, target_index_id FROM semantic_index_heads
            WHERE work_id=:work AND kind=:kind
        """),
                {"work": work_id, "kind": kind},
            )
            .mappings()
            .first()
        )
        return dict(row) if row else {"active_index_id": None, "target_index_id": None}

    @staticmethod
    def _index(
        conn: Connection, work_id: UUID, index_id: UUID, kind: str | None = None
    ) -> dict[str, Any]:
        row = (
            conn.execute(
                text("""
            SELECT * FROM semantic_indexes WHERE id=:id AND work_id=:work
                AND (CAST(:kind AS text) IS NULL OR kind=:kind)
        """),
                {"id": index_id, "work": work_id, "kind": kind},
            )
            .mappings()
            .first()
        )
        if row is None:
            raise ServiceError("INDEX_NOT_FOUND", "指定作品中不存在该索引代", 404)
        return dict(row)

    @staticmethod
    def _coverage(conn: Connection, index: dict[str, Any]) -> SemanticCoverage:
        if index["kind"] == "annotation":
            return annotation_index.coverage(conn, index)
        # EXISTS 按原始自然段分类，重叠切片不会重复增加覆盖数。
        row = (
            conn.execute(
                text("""
            SELECT count(*) AS total,
                count(*) FILTER (WHERE covered) AS covered,
                count(*) FILTER (WHERE NOT covered AND blocked) AS blocked
            FROM (
                SELECT EXISTS (SELECT 1 FROM semantic_index_items i
                    WHERE i.index_id=:id AND i.section_id=p.section_id
                      AND p.ordinal BETWEEN i.start_ordinal AND i.end_ordinal
                      AND i.embedding IS NOT NULL) AS covered,
                    EXISTS (SELECT 1 FROM semantic_index_items i
                    WHERE i.index_id=:id AND i.section_id=p.section_id
                      AND p.ordinal BETWEEN i.start_ordinal AND i.end_ordinal
                      AND i.blocked_reason IS NOT NULL) AS blocked
                FROM paragraphs p JOIN sections s ON s.id=p.section_id WHERE s.work_id=:work
            ) coverage
        """),
                {"id": index["id"], "work": index["work_id"]},
            )
            .mappings()
            .one()
        )
        pending = row["total"] - row["covered"] - row["blocked"]
        return SemanticCoverage(**row, pending=pending, complete=row["covered"] == row["total"])

    def _summary(self, conn: Connection, index: dict[str, Any]) -> SemanticSummary:
        return SemanticSummary(
            index_id=index["id"],
            contract_id=index["contract_id"],
            generation_status=index["status"],
            coverage=self._coverage(conn, index),
            last_error=index["last_error"],
            source_stale=work_at(conn, index["work_id"]).source_sha256 != index["source_sha256"],
        )

    def _write_result(self, conn: Connection, index: dict[str, Any], **batch: Any) -> SemanticWrite:
        summary = self._summary(conn, index)
        return bounded(
            SemanticWrite(
                **summary.model_dump(exclude={"last_error"}),
                **self._head(conn, index["work_id"], index["kind"]),
                **batch,
            )
        )

    @staticmethod
    def _replay(row: dict[str, Any], fingerprint: str, key: str) -> SemanticWrite:
        if row["fingerprint"] != fingerprint:
            raise ServiceError("IDEMPOTENCY_CONFLICT", "同一请求 ID 的参数不一致", 409)
        return SemanticWrite.model_validate(row[key]).model_copy(update={"replayed": True})

    def create(self, request: SemanticCreate) -> SemanticWrite:
        """创建独立目标代；已有请求可离线重放，新的目标不会覆盖旧完整代。"""
        fingerprint = digest(request.model_dump(mode="json"))
        with self._locked(request.work_id, request.kind) as conn:
            with conn.begin():
                old = (
                    conn.execute(
                        text("""
                    SELECT * FROM semantic_indexes
                    WHERE work_id=:work AND kind=:kind AND request_id=:request
                """),
                        {
                            "work": request.work_id,
                            "kind": request.kind,
                            "request": request.request_id,
                        },
                    )
                    .mappings()
                    .first()
                )
                if old:
                    return self._replay(dict(old), fingerprint, "create_response")
            self.model.verify()
            with conn.begin():
                work = work_at(conn, request.work_id)
                head = self._head(conn, request.work_id, request.kind)
                if head["target_index_id"]:
                    conn.execute(
                        text("""
                        UPDATE semantic_indexes SET status='superseded'
                        WHERE id=:id AND status IN ('building','partial','failed')
                    """),
                        {"id": head["target_index_id"]},
                    )
                identifier = uuid4()
                conn.execute(
                    text("""
                    INSERT INTO semantic_indexes
                    (id, work_id, kind, request_id, fingerprint, contract_id, source_sha256,
                     status, cursor, create_response)
                    VALUES (:id,:work,:kind,:request,:fingerprint,:contract,:source,
                            'building', CAST(:cursor AS jsonb), '{}')
                """),
                    {
                        "id": identifier,
                        "work": request.work_id,
                        "request": request.request_id,
                        "fingerprint": fingerprint,
                        "kind": request.kind,
                        "contract": self.model.contract_id,
                        "source": work.source_sha256,
                        "cursor": json.dumps(
                            {
                                "section": 0,
                                "paragraph": 0,
                                "overlap_start": None,
                                **(
                                    {"annotation": str(UUID(int=0)), "range": 0}
                                    if request.kind == "annotation"
                                    else {}
                                ),
                            }
                        ),
                    },
                )
                conn.execute(
                    text("""
                    INSERT INTO semantic_index_heads (work_id, kind, target_index_id)
                    VALUES (:work,:kind,:id) ON CONFLICT (work_id,kind)
                    DO UPDATE SET target_index_id=excluded.target_index_id
                """),
                    {"work": request.work_id, "id": identifier, "kind": request.kind},
                )
                index = self._index(conn, request.work_id, identifier)
                # 原文只追加且已有分部不可修改；同契约完整代的切片可以直接复用。
                # 仅自动增量路径启用，原有显式重建仍可要求重新计算。
                if (
                    request.kind == "fulltext"
                    and request.reuse_existing
                    and head["active_index_id"]
                ):
                    old_index = self._index(conn, request.work_id, head["active_index_id"])
                    if (
                        old_index["contract_id"] == self.model.contract_id
                        and old_index["status"] == "ready"
                    ):
                        conn.execute(
                            text("""
                          INSERT INTO semantic_index_items
                          (id,index_id,work_id,kind,source_key,section_id,start_paragraph_id,
                           end_paragraph_id,start_ordinal,end_ordinal,text_sha256,tokens,bytes,
                           embedding,blocked_reason)
                          SELECT gen_random_uuid(),:new,work_id,kind,source_key,section_id,
                            start_paragraph_id,end_paragraph_id,start_ordinal,end_ordinal,
                            text_sha256,tokens,bytes,embedding,blocked_reason
                          FROM semantic_index_items WHERE index_id=:old AND kind='fulltext'
                        """),
                            {"new": identifier, "old": old_index["id"]},
                        )
                        conn.execute(
                            text(
                                "UPDATE semantic_indexes SET cursor=CAST(:cursor AS jsonb) "
                                "WHERE id=:id"
                            ),
                            {"cursor": json.dumps(old_index["cursor"]), "id": identifier},
                        )
                        index = self._index(conn, request.work_id, identifier)
                if request.kind == "annotation":
                    annotation_index.snapshot(conn, index)
                result = self._write_result(conn, index)
                conn.execute(
                    text("""
                    UPDATE semantic_indexes SET create_response=CAST(:response AS jsonb)
                    WHERE id=:id
                """),
                    {"response": result.model_dump_json(), "id": identifier},
                )
                return result

    @staticmethod
    def _paragraphs(
        conn: Connection,
        work_id: UUID,
        cursor: dict[str, Any],
        *,
        overlap: bool = False,
        reference: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """只取当前需要的行；超资源上限时由数据库返回摘要而非大段正文。"""
        condition = (
            "s.ordinal=:section AND p.ordinal BETWEEN :overlap AND :paragraph"
            if overlap
            else "(s.ordinal,p.ordinal) > (:section,:paragraph)"
        )
        bounds = "AND s.id=:ref_section AND p.ordinal BETWEEN :ref_lo AND :ref_hi"
        with conn.begin():
            rows = conn.execute(
                text(f"""
                SELECT p.id, p.section_id, p.ordinal, s.ordinal AS section_ordinal,
                    octet_length(p.text) AS bytes,
                    CASE WHEN octet_length(p.text)<=1048576 THEN p.text ELSE '' END AS text,
                    CASE WHEN octet_length(p.text)>1048576
                        THEN encode(sha256(convert_to(p.text,'UTF8')),'hex') END AS text_sha256
                FROM paragraphs p JOIN sections s ON s.id=p.section_id
                WHERE s.work_id=:work AND {condition}
                {bounds if reference else ""}
                ORDER BY s.ordinal,p.ordinal {"" if overlap else "LIMIT 1"}
            """),
                {
                    "work": work_id,
                    "section": cursor["section"],
                    "paragraph": cursor["paragraph"],
                    "overlap": cursor.get("overlap_start"),
                    **(
                        {
                            "ref_section": reference["section_id"],
                            "ref_lo": reference["start_ordinal"],
                            "ref_hi": reference["end_ordinal"],
                        }
                        if reference
                        else {}
                    ),
                },
            )
            return [dict(row) for row in rows.mappings()]

    def build(self, request: SemanticBuild) -> SemanticWrite:
        """执行至多四项，网络失败不发布半批；回执重放不再次执行推理。"""
        fingerprint = digest(request.model_dump(mode="json"))
        with self.database.engine.begin() as lookup:
            work_at(lookup, request.work_id)
            self._schema(lookup)
            kind = self._index(lookup, request.work_id, request.index_id)["kind"]
        with self._locked(request.work_id, kind) as conn:
            with conn.begin():
                index = self._index(conn, request.work_id, request.index_id)
                receipt = (
                    conn.execute(
                        text("""
                    SELECT * FROM semantic_build_receipts WHERE index_id=:id AND request_id=:request
                """),
                        {"id": request.index_id, "request": request.request_id},
                    )
                    .mappings()
                    .first()
                )
                if receipt:
                    return self._replay(dict(receipt), fingerprint, "response")
                if work_at(conn, request.work_id).source_sha256 != index["source_sha256"]:
                    raise ServiceError(
                        "INDEX_SOURCE_CHANGED", "作品已追加分部，请显式创建新索引代", 409
                    )
                if (
                    index["status"] == "superseded"
                    or self._head(conn, request.work_id, kind)["target_index_id"]
                    != request.index_id
                ):
                    raise ServiceError("INDEX_SUPERSEDED", "该代已不再是构建目标", 409)
            try:
                self.model.verify()
                if index["contract_id"] != self.model.contract_id:
                    raise ServiceError(
                        "EMBEDDING_CONTRACT_MISMATCH", "索引契约与当前模型不匹配", 409
                    )
                chunks: list[Chunk] = []
                inputs: list[list[int]] = []
                cursor = index["cursor"]
                scanned = index["scanned"]

                def read_next(after: dict[str, Any]) -> dict[str, Any] | None:
                    rows = self._paragraphs(conn, request.work_id, after)
                    return rows[0] if rows else None

                while not scanned and len(chunks) < request.max_items:
                    if kind == "annotation":
                        chunk, after = self._annotation_chunk(conn, index, cursor)
                    else:
                        chunk, after = next_chunk(
                            cursor,
                            read_next,
                            lambda c: self._paragraphs(conn, request.work_id, c, overlap=True),
                            self.model,
                        )
                    if chunk is None:
                        scanned = True
                        break
                    if chunk.blocked_reason is None:
                        assert chunk.tokens is not None
                        if sum(map(len, inputs)) + len(chunk.tokens) > MAX_TOKENS:
                            break
                        inputs.append(chunk.tokens)
                    chunks.append(chunk)
                    cursor = after
                # 即使最后一块恰好用尽批次容量，也能在本批正确发布完整代。
                if not scanned and (
                    annotation_index.position(conn, index, cursor) is None
                    if kind == "annotation"
                    else read_next(cursor) is None
                ):
                    scanned = True
                batch_tokens = sum(map(len, inputs))
                vectors = iter(self.model.embed(inputs) if inputs else [])
                # invalidated 连接不得在 begin 时透明重连；丢弃本批计算结果。
                if conn.invalidated:
                    raise ServiceError("DATABASE_UNAVAILABLE", "索引锁连接已断开，请重试本批", 503)
                with conn.begin():
                    work = work_at(conn, request.work_id)
                    if work.source_sha256 != index["source_sha256"]:
                        raise ServiceError(
                            "INDEX_SOURCE_CHANGED", "作品已追加分部，请显式创建新索引代", 409
                        )
                    discarded = 0
                    identities: dict[UUID, str] = {}
                    if kind == "annotation":
                        for chunk in chunks:
                            assert chunk.annotation_id is not None and chunk.evidence_id is not None
                            identities[chunk.annotation_id] = chunk.evidence_id
                    if kind == "annotation" and not annotation_index.validate_batch(
                        conn, index, identities
                    ):
                        # 整批丢弃并保留原游标；下一请求过滤过期快照后重新规划。
                        discarded = len(chunks)
                        chunks, inputs = [], []
                        cursor, scanned = index["cursor"], False
                    for chunk in chunks:
                        self._insert_chunk(
                            conn,
                            index,
                            chunk,
                            next(vectors) if chunk.blocked_reason is None else None,
                        )
                    coverage = self._coverage(conn, index)
                    status = (
                        ("ready" if coverage.complete else "partial") if scanned else "building"
                    )
                    conn.execute(
                        text("""
                        UPDATE semantic_indexes SET cursor=CAST(:cursor AS jsonb), scanned=:scanned,
                            status=:status, last_error=NULL WHERE id=:id
                    """),
                        {
                            "cursor": json.dumps(cursor),
                            "scanned": scanned,
                            "status": status,
                            "id": request.index_id,
                        },
                    )
                    if status == "ready":
                        updated = conn.execute(
                            text("""
                            UPDATE semantic_index_heads SET active_index_id=:id
                            WHERE work_id=:work AND kind=:kind AND target_index_id=:id
                        """),
                            {"id": request.index_id, "work": request.work_id, "kind": kind},
                        )
                        if updated.rowcount != 1:
                            raise ServiceError("INDEX_SUPERSEDED", "目标代已被替代", 409)
                    index["status"] = status
                    result = self._write_result(
                        conn,
                        index,
                        batch_ready=len(inputs),
                        batch_blocked=len(chunks) - len(inputs),
                        batch_tokens=batch_tokens,
                        batch_discarded=discarded,
                    )
                    conn.execute(
                        text("""
                        INSERT INTO semantic_build_receipts
                            (index_id,request_id,fingerprint,response)
                        VALUES (:id,:request,:fingerprint,CAST(:response AS jsonb))
                    """),
                        {
                            "id": request.index_id,
                            "request": request.request_id,
                            "fingerprint": fingerprint,
                            "response": result.model_dump_json(),
                        },
                    )
                    return result
            except (ServiceError, SQLAlchemyError) as exc:
                # 已完整发布的目标不因无新增工作的健康失败退化；断线时不能换连接写状态。
                if not conn.invalidated and index["status"] not in ("ready", "partial"):
                    try:
                        conn.rollback()
                        with conn.begin():
                            conn.execute(
                                text("""
                                UPDATE semantic_indexes SET status='failed',last_error=:error
                                WHERE id=:id AND status<>'superseded'
                            """),
                                {
                                    "id": request.index_id,
                                    "error": exc.code
                                    if isinstance(exc, ServiceError)
                                    else "DATABASE_ERROR",
                                },
                            )
                    except SQLAlchemyError:
                        conn.invalidate()
                raise

    @staticmethod
    def _insert_chunk(
        conn: Connection, index: dict[str, Any], chunk: Chunk, vector: list[float] | None
    ) -> None:
        first, last = chunk.paragraphs[0], chunk.paragraphs[-1]
        # 写事务中再次验证端点所属作品、章节、顺序；不得只信规划阶段的内存对象。
        count = conn.execute(
            text("""
            SELECT count(*) FROM paragraphs p JOIN sections s ON s.id=p.section_id
            WHERE s.work_id=:work AND s.id=:section AND
                ((p.id=:start AND p.ordinal=:lo) OR (p.id=:end AND p.ordinal=:hi))
        """),
            {
                "work": index["work_id"],
                "section": first["section_id"],
                "start": first["id"],
                "end": last["id"],
                "lo": first["ordinal"],
                "hi": last["ordinal"],
            },
        ).scalar()
        if count != (1 if first["id"] == last["id"] else 2):
            raise ServiceError("INVALID_SOURCE_RANGE", "切片原文范围不再有效", 409)
        key_parts: list[Any] = [
            index["kind"],
            str(first["section_id"]),
            str(first["id"]),
            str(last["id"]),
        ]
        if chunk.annotation_id is not None:
            key_parts.extend([str(chunk.annotation_id), chunk.range_ordinal])
        key = digest(key_parts)
        conn.execute(
            text("""
            INSERT INTO semantic_index_items
            (id,index_id,work_id,kind,source_key,section_id,start_paragraph_id,end_paragraph_id,
             start_ordinal,end_ordinal,text_sha256,tokens,bytes,embedding,blocked_reason,
             annotation_id,evidence_id,range_ordinal)
            VALUES (:id,:index,:work,:kind,:key,:section,:start,:end,:lo,:hi,:hash,:tokens,
                    :bytes,CAST(:vector AS vector),:reason,:annotation,:evidence,:range_ordinal)
        """),
            {
                "id": uuid4(),
                "index": index["id"],
                "kind": index["kind"],
                "annotation": chunk.annotation_id,
                "evidence": chunk.evidence_id,
                "range_ordinal": chunk.range_ordinal,
                "work": index["work_id"],
                "key": key,
                "section": first["section_id"],
                "start": first["id"],
                "end": last["id"],
                "lo": first["ordinal"],
                "hi": last["ordinal"],
                "hash": chunk.text_sha256,
                "tokens": len(chunk.tokens) if chunk.tokens is not None else None,
                "bytes": chunk.bytes,
                "vector": json.dumps(vector) if vector is not None else None,
                "reason": chunk.blocked_reason,
            },
        )

    def get(self, request: SemanticGet) -> SemanticStatus:
        """无需模型在线即可发现 active/target；阻塞分页绑定作品、层、代。"""
        with self.database.engine.connect().execution_options(
            isolation_level="REPEATABLE READ"
        ) as c:
            with c.begin():
                work_at(c, request.work_id)
                self._schema(c)
                head = self._head(c, request.work_id, request.kind)
                summaries = {}
                for label in ("active", "target"):
                    identifier = head[label + "_index_id"]
                    summaries[label] = (
                        self._summary(c, self._index(c, request.work_id, identifier, request.kind))
                        if identifier
                        else None
                    )
                result = SemanticStatus(
                    work_id=request.work_id,
                    kind=request.kind,
                    state="present" if any(head.values()) else "missing",
                    active=summaries["active"],
                    target=summaries["target"],
                )
                if request.index_id is None:
                    if request.cursor:
                        raise ServiceError("INVALID_CURSOR", "阻塞范围分页须指定 index_id")
                    return bounded(result)
                result.index = self._summary(
                    c, self._index(c, request.work_id, request.index_id, request.kind)
                )
                scope = f"semantic-blocked:{request.work_id}:{request.kind}:{request.index_id}"
                after = decode_cursor(request.cursor, scope)
                try:
                    after_id = UUID(after) if after else UUID(int=0)
                except ValueError:
                    raise ServiceError("INVALID_CURSOR", "阻塞范围游标无效") from None
                rows = (
                    c.execute(
                        text(
                            (
                                annotation_index.CURRENT_EVIDENCE
                                if request.kind == "annotation"
                                else ""
                            )
                            + f"""
                    SELECT i.* {", e.annotation_version" if request.kind == "annotation" else ""}
                    FROM semantic_index_items i
                    {annotation_index.VALID_ITEMS_JOIN if request.kind == "annotation" else ""}
                    WHERE i.index_id=:id AND i.blocked_reason IS NOT NULL
                    AND i.id>:after ORDER BY i.id LIMIT :limit
                """
                        ),
                        {
                            "work": request.work_id,
                            "id": request.index_id,
                            "after": after_id,
                            "limit": request.limit + 1,
                        },
                    )
                    .mappings()
                    .all()
                )
                payload = result.model_dump()
                payload["blocked"] = [
                    dict(
                        source_range=self._range(row),
                        reason=row["blocked_reason"],
                        tokens=row["tokens"],
                        bytes=row["bytes"],
                        **self._annotation_fields(row, request.kind),
                    )
                    for row in rows[: request.limit]
                ]
                if len(rows) > request.limit:
                    payload["next_cursor"] = encode_cursor(
                        scope, str(rows[request.limit - 1]["id"])
                    )
                return bounded(SemanticStatus.model_validate(payload))

    @staticmethod
    def _annotation_fields(row: Any, kind: str) -> dict[str, Any]:
        """仅标注层增加来源字段，保持全文查询与阻塞结果的结构兼容。"""
        return (
            {key: row[key] for key in ("annotation_id", "annotation_version", "range_ordinal")}
            if kind == "annotation"
            else {}
        )

    def _annotation_chunk(
        self, conn: Connection, index: dict[str, Any], cursor: dict[str, Any]
    ) -> tuple[Chunk | None, dict[str, Any]]:
        """逐一处理快照范围；范围之间清空重叠，不拼接断开的原文。"""
        pos = annotation_index.position(conn, index, cursor)
        if pos is None:
            return None, cursor
        ref, before = pos

        def read(after: dict[str, Any]) -> dict[str, Any] | None:
            rows = self._paragraphs(conn, index["work_id"], after, reference=ref)
            return rows[0] if rows else None

        chunk, after = next_chunk(
            before,
            read,
            lambda c: self._paragraphs(conn, index["work_id"], c, overlap=True, reference=ref),
            self.model,
        )
        assert chunk is not None
        chunk.annotation_id, chunk.evidence_id, chunk.range_ordinal = (
            ref["annotation_id"],
            ref["evidence_id"],
            ref["range_ordinal"],
        )
        after = {**after, "annotation": before["annotation"], "range": before["range"]}
        if read(after) is None:
            after.update(range=before["range"] + 1, section=0, paragraph=0, overlap_start=None)
        return chunk, after

    @staticmethod
    def _range(row: Any) -> dict[str, Any]:
        return {
            key: row[key]
            for key in ("work_id", "section_id", "start_paragraph_id", "end_paragraph_id")
        }

    def search(self, request: SemanticSearch) -> SemanticResults:
        """先生成查询向量，再以同一个读快照选择单代并精确排序，不混代补结果。"""
        with self.database.engine.connect() as connection:
            searchable_work(connection, request.work_id)
            if request.part_id is not None:
                part_at(connection, request.work_id, request.part_id)
        self.model.verify()
        tokens = self.model.tokenize(QUERY_PREFIX + request.query)
        if len(tokens) > MAX_TOKENS:
            raise ServiceError("QUERY_TOO_LONG", "查询含指令后超过 4095 token", 400)
        vector = self.model.embed([tokens])[0]
        with self.database.engine.connect().execution_options(
            isolation_level="REPEATABLE READ"
        ) as c:
            with c.begin():
                work = searchable_work(c, request.work_id)
                self._schema(c)
                head = self._head(c, request.work_id, request.kind)
                identifier = request.index_id or head["active_index_id"]
                if identifier is None and request.allow_partial:
                    identifier = head["target_index_id"]
                if identifier is None:
                    raise ServiceError("INDEX_NOT_READY", "尚无完整索引，请读取索引状态并构建", 409)
                index = self._index(c, request.work_id, identifier, request.kind)
                if index["contract_id"] != self.model.contract_id:
                    raise ServiceError(
                        "EMBEDDING_CONTRACT_MISMATCH", "索引与当前模型或来源不匹配", 409
                    )
                if index["source_sha256"] != work.source_sha256:
                    raise ServiceError(
                        "INDEX_SOURCE_CHANGED", "作品已追加分部，请显式创建新索引代", 409
                    )
                coverage = self._coverage(c, index)
                if not coverage.complete and not request.allow_partial:
                    raise ServiceError(
                        "INDEX_INCOMPLETE", "索引存在缺口，请读取 semantic_index_get", 409
                    )
                rows = (
                    c.execute(
                        text(
                            (
                                annotation_index.CURRENT_EVIDENCE
                                if request.kind == "annotation"
                                else ""
                            )
                            + f"""
                    SELECT i.*, s.ordinal AS section_ordinal, s.part_id,
                        pt.name AS part_name, s.title AS section_title,
                        {"e.annotation_version," if request.kind == "annotation" else ""}
                        i.embedding <=> CAST(:vector AS vector) AS distance,
                        left(p.text,200) AS excerpt,
                        (length(p.text)>200 OR i.start_ordinal<>i.end_ordinal) AS excerpt_truncated
                    FROM semantic_index_items i JOIN sections s ON s.id=i.section_id
                        JOIN parts pt ON pt.id=s.part_id
                        JOIN paragraphs p ON p.id=i.start_paragraph_id
                    {annotation_index.VALID_ITEMS_JOIN if request.kind == "annotation" else ""}
                    WHERE i.work_id=:work AND i.index_id=:id AND i.embedding IS NOT NULL
                    AND (CAST(:part AS uuid) IS NULL OR s.part_id=:part)
                    ORDER BY distance,s.ordinal,i.start_ordinal,i.end_ordinal,i.id LIMIT :limit
                """
                        ),
                        {
                            "vector": json.dumps(vector),
                            "work": request.work_id,
                            "id": identifier,
                            "limit": 10 * request.limit + 1,
                            "part": request.part_id,
                        },
                    )
                    .mappings()
                    .all()
                )
                kept: list[Any] = []
                for row in rows[: 10 * request.limit]:
                    duplicate = False
                    for prior in kept:
                        if request.kind == "annotation":
                            if prior["annotation_id"] == row["annotation_id"]:
                                duplicate = True
                                break
                            continue
                        if prior["section_id"] != row["section_id"]:
                            continue
                        overlap = max(
                            0,
                            min(prior["end_ordinal"], row["end_ordinal"])
                            - max(prior["start_ordinal"], row["start_ordinal"])
                            + 1,
                        )
                        shorter = min(
                            prior["end_ordinal"] - prior["start_ordinal"] + 1,
                            row["end_ordinal"] - row["start_ordinal"] + 1,
                        )
                        if overlap * 5 >= shorter * 4:
                            duplicate = True
                            break
                    if not duplicate:
                        kept.append(row)
                    if len(kept) == request.limit:
                        break
                return bounded(
                    SemanticResults.model_validate(
                        dict(
                            index_id=identifier,
                            contract_id=index["contract_id"],
                            generation_status=index["status"],
                            coverage=coverage,
                            partial=not coverage.complete,
                            candidate_window_limited=len(rows) > 10 * request.limit,
                            items=[
                                dict(
                                    part_id=row["part_id"],
                                    part_name=row["part_name"],
                                    section_title=row["section_title"],
                                    source_range=self._range(row),
                                    excerpt=row["excerpt"],
                                    excerpt_truncated=row["excerpt_truncated"],
                                    score=1 - row["distance"],
                                    kind=request.kind,
                                    **self._annotation_fields(row, request.kind),
                                )
                                for row in kept
                            ],
                        )
                    )
                )
