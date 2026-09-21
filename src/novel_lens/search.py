"""PGroonga 关键词检索：直接索引业务字段，只返回一页定位与原文摘要。"""

import hashlib
import json
from typing import Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, ValidationError
from sqlalchemy import text

from novel_lens.asset_contracts import MAX_ASSET_RESULT_BYTES
from novel_lens.contracts import Page, RequestModel
from novel_lens.cursors import decode_cursor, encode_cursor
from novel_lens.database import Database
from novel_lens.errors import ServiceError
from novel_lens.reading import work_at
from novel_lens.search_contracts import AnnotationSearchHit, SearchRequest, SourceSearchHit


class SourceAfter(RequestModel):
    section_ordinal: int = Field(gt=0, strict=True)
    ordinal: int = Field(gt=0, strict=True)
    id: UUID


class AnnotationAfter(RequestModel):
    created_at: AwareDatetime
    id: UUID


class SearchService:
    """与原文和资产服务共用连接池；不安装扩展、不维护额外业务副本。"""

    def __init__(self, database: Database) -> None:
        self.database = database

    def source(self, request: SearchRequest) -> Page[SourceSearchHit]:
        """按章节、段落顺序返回原文候选，未标注的自然段同样参与检索。"""
        rows, cursor = self._search(request, "source")
        return self._bounded(
            Page[SourceSearchHit](
                items=[SourceSearchHit.model_validate(row) for row in rows], next_cursor=cursor
            )
        )

    def annotations(self, request: SearchRequest) -> Page[AnnotationSearchHit]:
        """仅检索当前 note，按创建时间和 ID 返回可回读完整资产的候选。"""
        rows, cursor = self._search(request, "annotation")
        return self._bounded(
            Page[AnnotationSearchHit](
                items=[AnnotationSearchHit.model_validate(row) for row in rows], next_cursor=cursor
            )
        )

    @staticmethod
    def _bounded[T](page: Page[T]) -> Page[T]:
        """REST 与 MCP 共用同一响应大小上限，超限不悄悄删除候选。"""
        if len(page.model_dump_json().encode("utf-8")) > MAX_ASSET_RESULT_BYTES:
            raise ServiceError("RESULT_TOO_LARGE", "搜索结果超过 1 MiB，请减小 limit")
        return page

    def _search(
        self, request: SearchRequest, kind: Literal["source", "annotation"]
    ) -> tuple[list[dict[str, Any]], str | None]:
        """键集分页不提供跨页快照；所有可变输入都通过绑定参数进入 SQL。

        两个固定查询只在字段投影和排序上不同。先物化有界候选，再定位匹配并截取
        200 个原始字符，避免将长段落传回 Python。无法映射命中时返回段首摘要并标记。
        """
        scope = hashlib.sha256(
            json.dumps(
                ["search-v1", kind, str(request.work_id), request.terms, request.match],
                ensure_ascii=True,
            ).encode()
        ).hexdigest()
        after = decode_cursor(request.cursor, scope)
        params: dict[str, Any] = {
            "work_id": request.work_id,
            "terms": request.terms,
            "limit": request.limit + 1,
        }
        if kind == "source":
            column, index = "p.text", "ix_paragraphs_text_search"
            table = "paragraphs p JOIN sections s ON s.id=p.section_id"
            owner, order = "s.work_id", "section_ordinal, ordinal, id"
            fields = "p.id, s.ordinal AS section_ordinal, p.ordinal, s.title AS section_title"
            fields += ", s.id AS section_id, s.work_id"
            seek = "(s.ordinal, p.ordinal, p.id) > (:section_ordinal, :ordinal, :id)"
            after_model: type[SourceAfter] | type[AnnotationAfter] = SourceAfter
        else:
            column, index = "a.note", "ix_annotations_note_search"
            table, owner, order = "annotations a", "a.work_id", "created_at, id"
            fields = "a.id, a.work_id, a.version, a.created_at"
            seek = "(a.created_at, a.id) > (:created_at, :id)"
            after_model = AnnotationAfter
        if after is not None:
            try:
                params.update(after_model.model_validate_json(after, strict=True).model_dump())
            except ValidationError:
                raise ServiceError("INVALID_CURSOR", "搜索游标无效") from None
        conditions = []
        for i, term in enumerate(request.terms):
            params[f"term{i}"] = term
            conditions.append(
                f"{column} &@ (:term{i}, NULL, '{index}')::pgroonga_full_text_search_condition"
            )
        # 指定索引让顺序扫描沿用索引分词。PGroonga 4.0.8 / PG18 的复合条件
        # OR 在规划时会报 type with OID 0；any 改用可独立走索引的 ID 集合并集。
        identifier = "p.id" if kind == "source" else "a.id"
        matches = " AND ".join(conditions)
        if request.match == "any":
            union = " UNION ".join(
                f"SELECT {identifier} FROM {table} WHERE {owner}=:work_id AND {condition}"
                for condition in conditions
            )
            matches = f"{identifier} IN ({union})"
        query = f"""
            WITH candidates AS MATERIALIZED (
                SELECT {fields}, {column} AS body FROM {table}
                WHERE {owner}=:work_id AND ({matches})
                {("AND " + seek) if after is not None else ""}
                ORDER BY {order} LIMIT :limit
            ), located AS MATERIALIZED (
                SELECT *, (pgroonga_match_positions_character(
                    body, CAST(:terms AS text[]), '{index}'))[1][1] AS position
                FROM candidates
            )
            SELECT {", ".join("c." + v.strip() for v in order.split(","))},
                c.work_id,
                {("c.section_id, c.section_title," if kind == "source" else "c.version,")}
                substr(body, greatest(coalesce(position, 0)-40, 0)+1, 200) AS excerpt_text,
                greatest(coalesce(position, 0)-40, 0) AS excerpt_start,
                char_length(body) AS body_length, position IS NOT NULL AS match_located
                {"" if kind == "source" else ", " + _ANNOTATION_RANGES}
            FROM located c ORDER BY {order}
        """
        with self.database.engine.begin() as connection:
            work_at(connection, request.work_id)
            ready = connection.execute(
                text("""
                SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname='pgroonga')
                  AND (SELECT count(*) FROM pg_index
                       WHERE indexrelid IN (to_regclass('ix_paragraphs_text_search'),
                                            to_regclass('ix_annotations_note_search'))
                         AND indisvalid AND indisready)=2
            """)
            ).scalar_one()
            if not ready:
                raise ServiceError("SEARCH_UNAVAILABLE", "全文检索需要 PGroonga 和迁移 0006", 503)
            # 缺省的零命中扩展会因查询计划不同而改变结果，必须在当前事务关闭。
            connection.execute(text("SET LOCAL pgroonga.match_escalation_threshold = -1"))
            connection.execute(text("SET LOCAL pgroonga.force_match_escalation = off"))
            rows = connection.execute(text(query), params).mappings().all()
        next_cursor = None
        if len(rows) > request.limit:
            tail = rows[request.limit - 1]
            point = after_model.model_validate({key: tail[key] for key in after_model.model_fields})
            next_cursor = encode_cursor(scope, point.model_dump_json())
        results = []
        for row in rows[: request.limit]:
            item = dict(row)
            start = item.pop("excerpt_start")
            length = item.pop("body_length")
            excerpt = item.pop("excerpt_text")
            item["excerpt"] = dict(
                text=excerpt,
                character_start=start,
                truncated_before=start > 0,
                truncated_after=start + len(excerpt) < length,
                match_located=item.pop("match_located"),
            )
            if kind == "source":
                item["paragraph_id"] = item.pop("id")
                item["source_range"] = dict(
                    work_id=item["work_id"],
                    section_id=item["section_id"],
                    start_paragraph_id=item["paragraph_id"],
                    end_paragraph_id=item["paragraph_id"],
                )
            else:
                item["annotation_id"] = item.pop("id")
            results.append(item)
        return results, next_cursor


# 只投影首个证据及数量；完整标注由 annotation_get 提供，不聚合无限长证据列表。
_ANNOTATION_RANGES = """
    (SELECT jsonb_build_object('work_id', r.work_id, 'section_id', r.section_id,
        'start_paragraph_id', r.start_paragraph_id, 'end_paragraph_id', r.end_paragraph_id)
     FROM annotation_ranges r WHERE r.annotation_id=c.id ORDER BY r.ordinal LIMIT 1)
        AS first_source_range,
    (SELECT count(*) FROM annotation_ranges r WHERE r.annotation_id=c.id) AS source_range_count
"""
