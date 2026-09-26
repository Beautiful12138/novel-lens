"""持久搜索快照：固定候选及已发行游标，检查业务世代后才允许读取缓存预览。"""

import asyncio
import json
import logging
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Connection, text
from sqlalchemy.exc import SQLAlchemyError

from novel_lens.asset_contracts import MAX_ASSET_RESULT_BYTES
from novel_lens.contracts import ReadingFormat
from novel_lens.database import Database
from novel_lens.errors import ServiceError
from novel_lens.reference_contracts import CompactReferenceResult, ReferenceQuery, ReferenceResult

MAX_SEARCH_BYTES = 4 * 1024 * 1024
MAX_SEARCHES = 256
SEARCH_TTL = timedelta(minutes=30)


def revisions(conn: Connection, work_ids: list[UUID]) -> dict[str, list[Any]]:
    """内容 revision 与目录世代分开；索引发布和处理进度不影响快照有效性。"""
    rows = conn.execute(
        text("""
        SELECT w.id,w.reference_version,w.visibility,coalesce(q.revision,0) AS revision
        FROM works w LEFT JOIN reference_queue q ON q.work_id=w.id
        WHERE w.id=ANY(CAST(:works AS uuid[]))
    """),
        {"works": work_ids},
    ).mappings()
    return {str(r["id"]): [r["reference_version"], r["revision"], r["visibility"]] for r in rows}


def project_page(
    payload: dict[str, Any], offset: int, format: ReadingFormat
) -> CompactReferenceResult | ReferenceResult:
    """对同一固定页只做字段投影，不访问原文、模型或当前索引统计。"""
    limit = payload["limit"]
    values = {
        key: payload[key]
        for key in (
            "search_id",
            "snapshot_at",
            "expires_at",
            "candidate_window_limited",
            "warnings",
        )
    }
    end = offset + limit
    values["items"] = payload["items"][offset:end]
    values["next_cursor"] = (
        payload["cursors"][end // limit] if end < len(payload["items"]) else None
    )
    if format == "full":
        return ReferenceResult.model_validate(values | {"diagnostics": payload["diagnostics"]})
    return CompactReferenceResult.model_validate(values)


class ReferenceSessions:
    """快照表容量有数据库硬约束；调用者负责新查事务和槽位争用的事务重试。"""

    def __init__(self, database: Database) -> None:
        self.database = database

    @staticmethod
    def collect(conn: Connection) -> None:
        """表最多 256 行，清理天然有界；跳过正被其他进程处理的行。"""
        conn.execute(
            text("""
            DELETE FROM reference_searches WHERE id IN (
              SELECT id FROM reference_searches
              WHERE expires_at<=clock_timestamp() OR invalidated
              ORDER BY expires_at FOR UPDATE SKIP LOCKED LIMIT 256
            )
        """)
        )

    def save(
        self, conn: Connection, request: ReferenceQuery, data: dict[str, Any], contract_id: str
    ) -> CompactReferenceResult | ReferenceResult:
        """全页 full 大小校验与保存原子完成，任何失败均不能留下可读半份快照。"""
        assert request.scope is not None
        self.collect(conn)
        slot = conn.execute(
            text("""
            SELECT n FROM generate_series(1,:maximum) AS slots(n)
            WHERE NOT EXISTS(SELECT 1 FROM reference_searches s WHERE s.slot=n)
            ORDER BY n LIMIT 1
        """),
            {"maximum": MAX_SEARCHES},
        ).scalar()
        if slot is None:
            raise ServiceError("REFERENCE_SEARCH_CAPACITY", "搜索快照容量已满，请稍后重试", 503)
        now = conn.execute(text("SELECT clock_timestamp()")).scalar_one()
        payload = data | {
            "search_id": str(uuid4()),
            "snapshot_at": now.isoformat(),
            "expires_at": (now + SEARCH_TTL).isoformat(),
            "limit": request.limit,
            "contract_id": contract_id,
            "revisions": revisions(conn, [s.work_id for s in request.scope]),
            "cursors": [
                uuid4().hex
                for _ in range(max(1, (len(data["items"]) + request.limit - 1) // request.limit))
            ],
        }
        if len(payload["items"]) > 1200:
            raise ServiceError("REFERENCE_SEARCH_TOO_LARGE", "搜索候选过多，请缩小范围", 413)
        for offset in range(0, max(1, len(payload["items"])), request.limit):
            if (
                len(project_page(payload, offset, "full").model_dump_json().encode())
                > MAX_ASSET_RESULT_BYTES
            ):
                raise ServiceError(
                    "RESULT_TOO_LARGE", "详细页超过 1 MiB，请减小 limit 或查询范围", 413
                )
        encoded = json.dumps(payload, ensure_ascii=False)
        size = conn.execute(
            text("SELECT octet_length(CAST(:p AS jsonb)::text)"), {"p": encoded}
        ).scalar_one()
        if size > MAX_SEARCH_BYTES:
            raise ServiceError("REFERENCE_SEARCH_TOO_LARGE", "搜索快照过大，请缩小查询范围", 413)
        conn.execute(
            text("""
            INSERT INTO reference_searches(id,slot,snapshot_at,expires_at,payload)
            VALUES(:id,:slot,:created,:expires,CAST(:payload AS jsonb))
        """),
            {
                "id": payload["search_id"],
                "slot": slot,
                "created": now,
                "expires": now + SEARCH_TTL,
                "payload": encoded,
            },
        )
        return project_page(payload, 0, request.format)

    def read(
        self, request: ReferenceQuery, contract_id: str
    ) -> CompactReferenceResult | ReferenceResult:
        """同一快照核验和取页；失效后用独立短事务幂等标记，避免并发重放写冲突。"""
        with (
            self.database.engine.connect().execution_options(
                isolation_level="REPEATABLE READ"
            ) as conn,
            conn.begin(),
        ):
            row = (
                conn.execute(
                    text("""
                SELECT payload,invalidated FROM reference_searches
                WHERE id=:id AND expires_at>clock_timestamp()
            """),
                    {"id": request.search_id},
                )
                .mappings()
                .first()
            )
            if row is None:
                raise ServiceError(
                    "REFERENCE_SEARCH_UNAVAILABLE", "搜索不存在或已过期，请重新查询", 410
                )
            payload = row["payload"]
            current = revisions(conn, [UUID(w) for w in payload["revisions"]])
            stale = (
                row["invalidated"]
                or payload["contract_id"] != contract_id
                or current != payload["revisions"]
                or any(v[2] != "visible" for v in current.values())
            )
            if not stale:
                offset = 0
                if request.cursor is not None:
                    try:
                        page = payload["cursors"].index(request.cursor)
                    except ValueError:
                        raise ServiceError("INVALID_CURSOR", "游标不属于该搜索", 422) from None
                    if page == 0:
                        raise ServiceError("INVALID_CURSOR", "首页使用 search_id 重读", 422)
                    offset = page * payload["limit"]
                return project_page(payload, offset, request.format)
        # 只标记已经证实不可用的搜索；READ COMMITTED 下相同标记可并发重放。
        # 清理先删除了该行也无需重建。此事务不返回候选，不改变先前快照判定。
        with self.database.engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE reference_searches SET invalidated=true "
                    "WHERE id=:id AND NOT invalidated"
                ),
                {"id": request.search_id},
            )
        raise ServiceError("REFERENCE_SEARCH_STALE", "参考内容或模型契约已变化，请重新查询", 409)

    async def run(self, stop: asyncio.Event) -> None:
        """随服务生命周期清理过期快照，不依赖用户发起下一次调用。"""
        while not stop.is_set():
            try:
                await asyncio.to_thread(self.cleanup)
            except SQLAlchemyError:
                logging.getLogger(__name__).warning("搜索快照清理暂时失败")
            try:
                await asyncio.wait_for(stop.wait(), timeout=60)
            except TimeoutError:
                pass

    def cleanup(self) -> None:
        """独立短事务清理派生快照，不持有业务锁等待模型或 AI。"""
        with self.database.engine.begin() as conn:
            self.collect(conn)
