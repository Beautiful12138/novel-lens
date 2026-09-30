"""业务入口的段号寻址；稳定 UUID 仍是存储和引用的规范坐标。"""

from uuid import UUID

from sqlalchemy import Connection, select

from novel_lens.contracts import RangeInput, SourceRange
from novel_lens.errors import ServiceError
from novel_lens.reading import paragraph_at, section_at
from novel_lens.schema import paragraphs


def resolve_range(connection: Connection, work_id: UUID, value: RangeInput) -> SourceRange:
    """在已选数据库快照中校验归属和端点；不猜测章节、不截断越界范围。"""
    if value.work_id != work_id:
        raise ServiceError("INVALID_RANGE", "引用范围必须属于指定作品")
    section = section_at(connection, work_id, value.section_id)
    if isinstance(value, SourceRange):
        first = paragraph_at(connection, value.section_id, value.start_paragraph_id)
        last = paragraph_at(connection, value.section_id, value.end_paragraph_id)
        if first.ordinal > last.ordinal:
            raise ServiceError("INVALID_RANGE", "范围起点不能晚于终点")
        return value
    if value.start_ordinal > value.end_ordinal or value.end_ordinal > section.paragraph_count:
        raise ServiceError("INVALID_RANGE", "段号范围倒置或超出指定章节")
    ids = dict(
        connection.execute(
            select(paragraphs.c.ordinal, paragraphs.c.id).where(
                paragraphs.c.section_id == value.section_id,
                paragraphs.c.ordinal.in_([value.start_ordinal, value.end_ordinal]),
            )
        )
        .tuples()
        .all()
    )
    if value.start_ordinal not in ids or value.end_ordinal not in ids:
        raise ServiceError("PARAGRAPH_NOT_FOUND", "指定章节中不存在该段号", 404)
    return SourceRange(
        work_id=work_id,
        section_id=value.section_id,
        start_paragraph_id=ids[value.start_ordinal],
        end_paragraph_id=ids[value.end_ordinal],
    )
