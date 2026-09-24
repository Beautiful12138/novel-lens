"""按作品和稳定坐标读取，不隐式扩大到其他作品或 Section。"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import Connection, literal, select, tuple_

from novel_lens.contracts import (
    CompactContextOut,
    CompactParagraphOut,
    CompactParagraphPage,
    CompactReadOut,
    ContextOut,
    ContextRequest,
    Page,
    ParagraphOut,
    ParagraphSpan,
    ReadingFormat,
    ReadOut,
    ReadRequest,
    SectionOut,
    SourceRange,
    WorkOut,
)
from novel_lens.cursors import decode_cursor, encode_cursor, ordinal_cursor
from novel_lens.database import Database
from novel_lens.errors import ServiceError
from novel_lens.schema import paragraphs, sections, sources, works


def work_at(connection: Connection, work_id: UUID) -> WorkOut:
    row = connection.execute(select(works).where(works.c.id == work_id)).mappings().first()
    if row is None:
        raise ServiceError("WORK_NOT_FOUND", "作品不存在", 404)
    return WorkOut.model_validate(row)


def section_at(connection: Connection, work_id: UUID, section_id: UUID) -> SectionOut:
    row = (
        connection.execute(
            select(sections).where(sections.c.id == section_id, sections.c.work_id == work_id)
        )
        .mappings()
        .first()
    )
    if row is None:
        raise ServiceError("SECTION_NOT_FOUND", "指定作品中不存在该 Section", 404)
    return SectionOut.model_validate(row)


def paragraph_at(connection: Connection, section_id: UUID, paragraph_id: UUID) -> ParagraphOut:
    row = (
        connection.execute(
            select(paragraphs).where(
                paragraphs.c.id == paragraph_id, paragraphs.c.section_id == section_id
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise ServiceError("PARAGRAPH_NOT_FOUND", "指定 Section 中不存在该段落", 404)
    return ParagraphOut.model_validate(row)


def bounds(work_id: UUID, items: list[ParagraphOut]) -> SourceRange:
    return SourceRange(
        work_id=work_id,
        section_id=items[0].section_id,
        start_paragraph_id=items[0].id,
        end_paragraph_id=items[-1].id,
    )


def compact_items(items: list[ParagraphOut]) -> list[CompactParagraphOut]:
    """仅投影已校验的完整段落，不改变正文或稳定 ID。"""
    return [CompactParagraphOut(id=p.id, ordinal=p.ordinal, text=p.text) for p in items]


def compact_span(source_range: SourceRange) -> ParagraphSpan:
    """归属由精简响应顶层保留，范围只携带真实端点。"""
    return ParagraphSpan(
        start_paragraph_id=source_range.start_paragraph_id,
        end_paragraph_id=source_range.end_paragraph_id,
    )


class ReadingService:
    """原文不可变，使用确定性顺序游标读取完整段落。"""

    def __init__(self, database: Database) -> None:
        self.database = database

    def get_work(self, work_id: UUID) -> WorkOut:
        with self.database.engine.connect() as connection:
            return work_at(connection, work_id)

    def list_works(self, limit: int, cursor: str | None) -> Page[WorkOut]:
        query = select(works).order_by(works.c.created_at, works.c.id).limit(limit + 1)
        value = decode_cursor(cursor, "works")
        if value is not None:
            try:
                time_text, id_text = value.split("|")
                after = datetime.fromisoformat(time_text)
                if after.tzinfo is None:
                    raise ValueError
                query = query.where(
                    tuple_(works.c.created_at, works.c.id)
                    > tuple_(literal(after), literal(UUID(id_text)))
                )
            except ValueError:
                raise ServiceError("INVALID_CURSOR", "作品列表游标无效") from None
        with self.database.engine.connect() as connection:
            rows = connection.execute(query).mappings().all()
        items = [WorkOut.model_validate(row) for row in rows[:limit]]
        next_cursor = None
        if len(rows) > limit:
            last = items[-1]
            next_cursor = encode_cursor("works", f"{last.created_at.isoformat()}|{last.id}")
        return Page(items=items, next_cursor=next_cursor)

    def list_sections(self, work_id: UUID, limit: int, cursor: str | None) -> Page[SectionOut]:
        scope = f"sections:{work_id}"
        with self.database.engine.connect() as connection:
            work = work_at(connection, work_id)
            after = ordinal_cursor(cursor, scope, work.section_count)
            rows = (
                connection.execute(
                    select(sections)
                    .where(sections.c.work_id == work_id, sections.c.ordinal > after)
                    .order_by(sections.c.ordinal)
                    .limit(limit + 1)
                )
                .mappings()
                .all()
            )
        items = [SectionOut.model_validate(row) for row in rows[:limit]]
        return Page(
            items=items,
            next_cursor=encode_cursor(scope, str(items[-1].ordinal)) if len(rows) > limit else None,
        )

    def list_paragraphs(
        self,
        work_id: UUID,
        section_id: UUID,
        limit: int,
        cursor: str | None,
        format: ReadingFormat = "compact",
    ) -> Page[ParagraphOut] | CompactParagraphPage:
        scope = f"paragraphs:{work_id}:{section_id}"
        with self.database.engine.connect() as connection:
            section = section_at(connection, work_id, section_id)
            after = ordinal_cursor(cursor, scope, section.paragraph_count)
            page = self._paragraph_page(
                connection, section_id, after + 1, section.paragraph_count, limit, scope
            )
            if format == "compact":
                return CompactParagraphPage(
                    work_id=work_id,
                    section_id=section_id,
                    items=compact_items(page.items),
                    actual_range=compact_span(bounds(work_id, page.items)) if page.items else None,
                    next_cursor=page.next_cursor,
                )
            return page

    def _paragraph_page(
        self, connection: Connection, section_id: UUID, start: int, end: int, limit: int, scope: str
    ) -> Page[ParagraphOut]:
        rows = (
            connection.execute(
                select(paragraphs)
                .where(
                    paragraphs.c.section_id == section_id,
                    paragraphs.c.ordinal >= start,
                    paragraphs.c.ordinal <= end,
                )
                .order_by(paragraphs.c.ordinal)
                .limit(limit + 1)
            )
            .mappings()
            .all()
        )
        items = [ParagraphOut.model_validate(row) for row in rows[:limit]]
        return Page(
            items=items,
            next_cursor=encode_cursor(scope, str(items[-1].ordinal)) if len(rows) > limit else None,
        )

    def read(self, work_id: UUID, request: ReadRequest) -> ReadOut | CompactReadOut:
        source_range = request.source_range
        if source_range.work_id != work_id:
            raise ServiceError("INVALID_RANGE", "路径作品与范围作品不一致")
        with self.database.engine.connect() as connection:
            section_at(connection, work_id, source_range.section_id)
            first = paragraph_at(
                connection, source_range.section_id, source_range.start_paragraph_id
            )
            last = paragraph_at(connection, source_range.section_id, source_range.end_paragraph_id)
            if first.ordinal > last.ordinal:
                raise ServiceError("INVALID_RANGE", "范围起点不能晚于终点")
            scope = f"range:{work_id}:{source_range.section_id}:{first.id}:{last.id}"
            after = ordinal_cursor(request.cursor, scope, last.ordinal)
            if request.cursor is not None and after < first.ordinal:
                raise ServiceError("INVALID_CURSOR", "游标不在请求范围内")
            page = self._paragraph_page(
                connection,
                source_range.section_id,
                max(first.ordinal, after + 1),
                last.ordinal,
                request.limit,
                scope,
            )
            actual_range = bounds(work_id, page.items)
            if request.format == "compact":
                return CompactReadOut(
                    work_id=work_id,
                    section_id=source_range.section_id,
                    items=compact_items(page.items),
                    next_cursor=page.next_cursor,
                    requested_range=compact_span(source_range),
                    actual_range=compact_span(actual_range),
                )
            return ReadOut(
                items=page.items,
                next_cursor=page.next_cursor,
                requested_range=source_range,
                actual_range=actual_range,
            )

    def context(self, work_id: UUID, request: ContextRequest) -> ContextOut | CompactContextOut:
        with self.database.engine.connect() as connection:
            section = section_at(connection, work_id, request.section_id)
            anchor = paragraph_at(connection, request.section_id, request.paragraph_id)
            start, end = (
                max(1, anchor.ordinal - request.before),
                min(section.paragraph_count, anchor.ordinal + request.after),
            )
            page = self._paragraph_page(connection, request.section_id, start, end, 201, "context")
            if request.format == "compact":
                return CompactContextOut(
                    work_id=work_id,
                    section_id=request.section_id,
                    items=compact_items(page.items),
                    actual_range=compact_span(bounds(work_id, page.items)),
                    at_section_start=start == 1,
                    at_section_end=end == section.paragraph_count,
                )
            return ContextOut(
                items=page.items,
                actual_range=bounds(work_id, page.items),
                at_section_start=start == 1,
                at_section_end=end == section.paragraph_count,
            )

    def file(self, work_id: UUID) -> bytes:
        with self.database.engine.connect() as connection:
            result = connection.execute(
                select(sources.c.content).where(sources.c.work_id == work_id)
            ).scalar_one_or_none()
            if result is None:
                raise ServiceError("WORK_NOT_FOUND", "作品不存在", 404)
            return bytes(result)
