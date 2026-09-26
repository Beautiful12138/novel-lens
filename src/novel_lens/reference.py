"""在一致性快照内完成范围过滤、四路统一排名及融合，再保存稳定的候选分页。"""

import json
from typing import Any

from sqlalchemy import Connection, text
from sqlalchemy.exc import DBAPIError

from novel_lens.assets import range_bounds
from novel_lens.catalog import part_at
from novel_lens.contracts import SourceRange
from novel_lens.database import Database
from novel_lens.embedding import MAX_TOKENS, QUERY_PREFIX, EmbeddingClient
from novel_lens.errors import ServiceError
from novel_lens.reading import searchable_work, section_at
from novel_lens.reference_contracts import (
    CompactReferenceResult,
    ReferenceHit,
    ReferenceQuery,
    ReferenceResult,
    ReferenceScope,
)
from novel_lens.reference_index import CLUES_SQL, index_state
from novel_lens.reference_sessions import ReferenceSessions

CHANNEL_WINDOW = 300


def fuse(channels: dict[str, list[dict[str, Any]]], limit: int) -> list[dict[str, Any]]:
    """RRF 按通道最佳名次计分；相交范围合并，不因重复标记抬高同一路得分。"""
    exact: dict[tuple[Any, ...], dict[str, Any]] = {}
    for channel, rows in channels.items():
        for rank, row in enumerate(rows, 1):
            key = (row["section_id"], row["start_ordinal"], row["end_ordinal"])
            candidate = exact.setdefault(key, dict(row) | {"ranks": {}, "annotations": set()})
            candidate["ranks"].setdefault(channel, rank)
            if row.get("annotation_id") is not None:
                candidate["annotations"].add(row["annotation_id"])

    def order(row: dict[str, Any]) -> tuple[Any, ...]:
        score = sum(1 / (60 + rank) for rank in row["ranks"].values())
        return (
            -score,
            str(row.get("work_id", "")),
            row["section_ordinal"],
            str(row["section_id"]),
            row["start_ordinal"],
            row["end_ordinal"],
            str(row["start_paragraph_id"]),
            str(row["end_paragraph_id"]),
        )

    merged: list[dict[str, Any]] = []
    for row in sorted(exact.values(), key=order):
        position = 0
        while position < len(merged):
            prior = merged[position]
            if row["section_id"] != prior["section_id"]:
                position += 1
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
            if overlap * 5 < shorter * 4:
                position += 1
                continue
            for channel, rank in prior["ranks"].items():
                row["ranks"][channel] = min(row["ranks"].get(channel, rank), rank)
            row["annotations"].update(prior["annotations"])
            if prior["start_ordinal"] < row["start_ordinal"]:
                row.update(
                    start_ordinal=prior["start_ordinal"],
                    start_paragraph_id=prior["start_paragraph_id"],
                )
            if prior["end_ordinal"] > row["end_ordinal"]:
                row.update(
                    end_ordinal=prior["end_ordinal"], end_paragraph_id=prior["end_paragraph_id"]
                )
            merged.pop(position)
            # 桥接候选扩大范围后，可能覆盖先前未达到阈值的范围，须重新检查。
            position = 0
        merged.append(row)
    return sorted(merged, key=order)[:limit]


def scope_filter(scope: list[ReferenceScope]) -> tuple[str, dict[str, Any]]:
    """构造仅含参数占位的 s 章节过滤，各通道与覆盖检查共享同一范围。"""
    clauses: list[str] = []
    params: dict[str, Any] = {}
    for n, entry in enumerate(sorted(scope, key=lambda s: str(s.work_id))):
        clause = f"s.work_id=:work{n}"
        params[f"work{n}"] = entry.work_id
        if entry.part_ids is not None:
            clause += f" AND s.part_id=ANY(CAST(:parts{n} AS uuid[]))"
            params[f"parts{n}"] = entry.part_ids
        if entry.section_ids is not None:
            clause += f" AND s.id=ANY(CAST(:sections{n} AS uuid[]))"
            params[f"sections{n}"] = entry.section_ids
        clauses.append(f"({clause})")
    return "(" + " OR ".join(clauses) + ")", params


def validate_scope(conn: Connection, request: ReferenceQuery) -> list[dict[str, Any]]:
    """校验全部归属并合并已读区间；范围外已读记录也必须真实、可读。"""
    assert request.scope is not None
    for scope in request.scope:
        searchable_work(conn, scope.work_id)
        for part in scope.part_ids or []:
            part_at(conn, scope.work_id, part)
        for section in scope.section_ids or []:
            section_at(conn, scope.work_id, section)
    intervals: dict[str, list[tuple[int, int]]] = {}
    for ref in request.exclude_ranges:
        searchable_work(conn, ref.work_id)
        first, last = range_bounds(conn, ref.work_id, ref)
        intervals.setdefault(str(ref.section_id), []).append((first, last))
    result: list[dict[str, Any]] = []
    for section_key, values in sorted(intervals.items()):
        merged: list[list[int]] = []
        for first, last in sorted(values):
            if merged and first <= merged[-1][1] + 1:
                merged[-1][1] = max(merged[-1][1], last)
            else:
                merged.append([first, last])
        result.extend(
            {"section": section_key, "first": first, "last": last} for first, last in merged
        )
    return result


class ReferenceService:
    """新查推理在事务外；快照写入冲突仅重试数据库阶段，不重复推理。"""

    def __init__(self, database: Database, model: EmbeddingClient) -> None:
        self.database = database
        self.model = model
        self.sessions = ReferenceSessions(database)

    def query(self, request: ReferenceQuery) -> CompactReferenceResult | ReferenceResult:
        if request.search_id is not None:
            return self.sessions.read(request, self.model.contract_id)
        assert request.query is not None and request.scope is not None
        with self.database.engine.connect() as conn:
            validate_scope(conn, request)
        self.model.verify()
        tokens = self.model.tokenize(QUERY_PREFIX + request.query)
        if len(tokens) > MAX_TOKENS:
            raise ServiceError("QUERY_TOO_LONG", "查询含指令后超过 4095 token", 400)
        vector = self.model.embed([tokens])[0]
        for attempt in range(5):
            try:
                with (
                    self.database.engine.connect().execution_options(
                        isolation_level="REPEATABLE READ"
                    ) as conn,
                    conn.begin(),
                ):
                    return self._create(conn, request, vector)
            except DBAPIError as exc:
                # 有界唯一槽位保证上限；竞争或序列化冲突必须换快照再试。
                code = getattr(exc.orig, "sqlstate", None)
                constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
                retry = code in {"40001", "40P01"} or (
                    code == "23505" and constraint == "reference_searches_slot_key"
                )
                if not retry or attempt == 4:
                    raise
        raise AssertionError("事务重试循环必须返回或抛出异常")

    def _create(
        self, conn: Connection, request: ReferenceQuery, vector: list[float]
    ) -> CompactReferenceResult | ReferenceResult:
        """同一快照内选择每作品可用索引，所有作品共用四路全局排名。"""
        assert request.scope is not None
        excluded = validate_scope(conn, request)
        scope_sql, params = scope_filter(request.scope)
        diagnostics, failures, index_ids = [], [], []
        for scope in sorted(request.scope, key=lambda s: str(s.work_id)):
            state = index_state(conn, scope.work_id, self.model.contract_id)
            candidates = (
                conn.execute(
                    text("""
                SELECT i.id FROM semantic_index_heads h JOIN semantic_indexes i
                  ON i.id=h.active_index_id OR i.id=h.target_index_id
                WHERE h.work_id=:work AND h.kind='fulltext' AND i.contract_id=:contract
                  AND i.status IN ('ready','building','partial','failed')
                ORDER BY (i.id=h.active_index_id) DESC,i.id
            """),
                    {"work": scope.work_id, "contract": self.model.contract_id},
                )
                .scalars()
                .all()
            )
            chosen, coverage = None, {}
            local_sql, local_params = scope_filter([scope])
            for identifier in candidates:
                coverage_row = (
                    conn.execute(
                        text(f"""
                    SELECT count(*) AS total,count(*) FILTER (WHERE EXISTS(
                      SELECT 1 FROM semantic_index_items i WHERE i.index_id=:index
                        AND i.section_id=p.section_id
                        AND p.ordinal BETWEEN i.start_ordinal AND i.end_ordinal
                        AND i.embedding IS NOT NULL)) AS covered
                    FROM paragraphs p JOIN sections s ON s.id=p.section_id WHERE {local_sql}
                """),
                        local_params | {"index": identifier},
                    )
                    .mappings()
                    .one()
                )
                coverage = dict(coverage_row) | {
                    "complete": coverage_row["total"] > 0
                    and coverage_row["total"] == coverage_row["covered"]
                }
                if coverage["complete"]:
                    chosen = identifier
                    break
            detail = {
                "scope": scope.model_dump(mode="json", exclude_none=True),
                "index_id": str(chosen) if chosen else None,
                "work_indexes": state.model_dump(mode="json"),
                "scope_coverage": coverage,
            }
            if chosen is None:
                failures.append(detail)
            else:
                diagnostics.append(detail)
                index_ids.append(chosen)
        if failures:
            raise ServiceError(
                "REFERENCE_NOT_READY", "指定范围的原文索引尚未完整就绪", 409, {"scopes": failures}
            )
        conn.execute(text("SET LOCAL pgroonga.match_escalation_threshold = -1"))
        conn.execute(text("SET LOCAL pgroonga.force_match_escalation = off"))
        params.update(
            works=[s.work_id for s in request.scope],
            indices=index_ids,
            contract=self.model.contract_id,
            vector=json.dumps(vector),
            window=CHANNEL_WINDOW + 1,
            excluded=json.dumps(excluded),
        )
        channels = self._candidates(conn, request, scope_sql, params)
        limited = any(len(rows) > CHANNEL_WINDOW for rows in channels.values())
        fused = fuse({name: rows[:CHANNEL_WINDOW] for name, rows in channels.items()}, 1200)
        hits = []
        for row in fused:
            preview = conn.execute(
                text("SELECT left(text,200),length(text)>200 FROM paragraphs WHERE id=:p"),
                {"p": row["start_paragraph_id"]},
            ).one()
            hits.append(
                ReferenceHit(
                    work_name=row["work_name"],
                    source_range=SourceRange(
                        work_id=row["work_id"],
                        section_id=row["section_id"],
                        start_paragraph_id=row["start_paragraph_id"],
                        end_paragraph_id=row["end_paragraph_id"],
                    ),
                    part_id=row["part_id"],
                    part_name=row["part_name"],
                    section_title=row["section_title"],
                    excerpt=preview[0],
                    excerpt_truncated=preview[1] or row["start_ordinal"] != row["end_ordinal"],
                    score=sum(1 / (60 + rank) for rank in row["ranks"].values()),
                    channels=sorted(row["ranks"]),
                    annotation_ids=sorted(row["annotations"]),
                ).model_dump(mode="json")
            )
        warnings = self._warnings(conn, scope_sql, params)
        normalized = [
            s.model_dump(mode="json", exclude_none=True)
            for s in sorted(request.scope, key=lambda s: str(s.work_id))
        ]
        for entry in normalized:
            for key in ("part_ids", "section_ids"):
                if key in entry:
                    entry[key].sort()
        return self.sessions.save(
            conn,
            request,
            {
                "items": hits,
                "candidate_window_limited": limited,
                "warnings": warnings,
                "diagnostics": {
                    "scope": normalized,
                    "query": request.query,
                    "terms": sorted(set(request.terms)),
                    "exclude_intervals": excluded,
                    "works": diagnostics,
                },
            },
            self.model.contract_id,
        )

    @staticmethod
    def _clues_sql() -> str:
        """复用线索指纹的唯一表示，只把单作品谓词扩大为所选作品集合。"""
        return CLUES_SQL.replace("a.work_id=:work", "a.work_id=ANY(CAST(:works AS uuid[]))")

    def _warnings(
        self, conn: Connection, scope: str, params: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """仅报告所选范围对应线索的缺口，不用作品级统计误报其他分部。"""
        rows = conn.execute(
            text(
                self._clues_sql()
                + f"""
            SELECT a.work_id,
              count(*) FILTER (WHERE c.annotation_id IS NULL) AS pending,
              count(*) FILTER (WHERE c.blocked_reason IS NOT NULL) AS blocked
            FROM current_clues a LEFT JOIN reference_clues c ON c.annotation_id=a.id
              AND c.fingerprint=a.fingerprint AND c.contract_id=:contract
            WHERE EXISTS(SELECT 1 FROM annotation_ranges r JOIN sections s ON s.id=r.section_id
              WHERE r.annotation_id=a.id AND {scope})
            GROUP BY a.work_id ORDER BY a.work_id
        """
            ),
            params,
        ).mappings()
        result = []
        for row in rows:
            for key, code, message in (
                ("pending", "CLUES_PENDING", "选定范围部分标记线索尚未同步"),
                ("blocked", "CLUES_BLOCKED", "选定范围部分标记线索无法建立向量"),
            ):
                if row[key]:
                    result.append(
                        {"code": code, "work_id": str(row["work_id"]), "message": message}
                    )
        return result

    def _candidates(
        self, conn: Connection, request: ReferenceQuery, scope: str, params: dict[str, Any]
    ) -> dict[str, list[dict[str, Any]]]:
        """排除已读在各路 LIMIT 前执行；四路分别对所有作品统一排序。"""
        location = (
            "s.work_id,w.name AS work_name,s.id AS section_id,"
            "s.ordinal AS section_ordinal,s.title AS section_title,"
            "s.part_id,pt.name AS part_name"
        )
        owners = "JOIN parts pt ON pt.id=s.part_id JOIN works w ON w.id=s.work_id"
        order = "s.work_id,s.ordinal,s.id"

        def unread(first: str, last: str) -> str:
            return f"""NOT EXISTS(SELECT 1 FROM jsonb_to_recordset(CAST(:excluded AS jsonb))
              AS e(section uuid,first integer,last integer)
              WHERE e.section=s.id AND e.first<={first} AND e.last>={last})"""

        vectors = f"""
            SELECT {location},i.start_paragraph_id,i.end_paragraph_id,i.start_ordinal,i.end_ordinal,
              i.embedding <=> CAST(:vector AS vector) AS distance
            FROM semantic_index_items i JOIN sections s ON s.id=i.section_id {owners}
            WHERE {scope} AND i.index_id=ANY(CAST(:indices AS uuid[])) AND i.embedding IS NOT NULL
              AND {unread("i.start_ordinal", "i.end_ordinal")}
            ORDER BY distance,{order},i.start_ordinal,i.end_ordinal,i.id LIMIT :window
        """
        refs = f"""JOIN annotation_ranges r ON r.annotation_id=a.id
            JOIN sections s ON s.id=r.section_id
            JOIN paragraphs first ON first.id=r.start_paragraph_id
            JOIN paragraphs last ON last.id=r.end_paragraph_id {owners}"""
        ref_location = (
            "r.start_paragraph_id,r.end_paragraph_id,first.ordinal AS start_ordinal,"
            "last.ordinal AS end_ordinal,a.id AS annotation_id"
        )
        ref_order = f"{order},first.ordinal,last.ordinal,a.id,r.ordinal"
        clues = (
            self._clues_sql()
            + f"""
            SELECT {location},{ref_location},c.embedding <=> CAST(:vector AS vector) AS distance
            FROM current_clues a JOIN reference_clues c ON c.annotation_id=a.id
              AND c.fingerprint=a.fingerprint AND c.contract_id=:contract
              AND c.embedding IS NOT NULL
            {refs} WHERE {scope} AND {unread("first.ordinal", "last.ordinal")}
            ORDER BY distance,{ref_order} LIMIT :window
        """
        )
        source_unions, clue_unions = [], []
        for n, term in enumerate(dict.fromkeys([request.query, *request.terms])):
            params[f"term{n}"] = term
            source_unions.append(f"""SELECT p.id FROM paragraphs p
                JOIN sections s ON s.id=p.section_id
                WHERE {scope} AND p.text &@ (:term{n},NULL,'ix_paragraphs_text_search')
                ::pgroonga_full_text_search_condition""")
            clue_unions.append(f"""SELECT a.id FROM current_clues a
                WHERE a.body &@ (:term{n},NULL,'ix_reference_clues_body')
                ::pgroonga_full_text_search_condition""")
        source_words = f"""
            WITH matches AS ({" UNION ALL ".join(source_unions)}),
            ranked AS (SELECT id,count(*) AS hits FROM matches GROUP BY id)
            SELECT {location},p.id AS start_paragraph_id,p.id AS end_paragraph_id,
              p.ordinal AS start_ordinal,p.ordinal AS end_ordinal
            FROM ranked k JOIN paragraphs p ON p.id=k.id
            JOIN sections s ON s.id=p.section_id {owners}
            WHERE {unread("p.ordinal", "p.ordinal")}
            ORDER BY k.hits DESC,{order},p.ordinal,p.id LIMIT :window
        """
        clue_words = (
            self._clues_sql()
            + f""",
            matches AS ({" UNION ALL ".join(clue_unions)}),
            ranked AS (SELECT id,count(*) AS hits FROM matches GROUP BY id)
            SELECT {location},{ref_location}
            FROM ranked k JOIN current_clues a ON a.id=k.id {refs}
            WHERE {scope} AND {unread("first.ordinal", "last.ordinal")}
            ORDER BY k.hits DESC,{ref_order} LIMIT :window
        """
        )
        return {
            name: [dict(row) for row in conn.execute(text(sql), params).mappings()]
            for name, sql in (
                ("source_semantic", vectors),
                ("source_keyword", source_words),
                ("clue_semantic", clues),
                ("clue_keyword", clue_words),
            )
        }
