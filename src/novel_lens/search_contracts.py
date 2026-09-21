"""作品内关键词搜索契约；关键词是普通文本，摘要始终来自原字段。"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from novel_lens.contracts import RequestModel, SourceRange

Term = Annotated[str, Field(strict=True, min_length=1, max_length=128)]


class SearchRequest(RequestModel):
    work_id: UUID
    terms: list[Term] = Field(min_length=1, max_length=8, strict=True)
    match: Literal["all", "any"] = "all"
    limit: int = Field(default=20, ge=1, le=100, strict=True)
    cursor: str | None = Field(default=None, max_length=2048, strict=True)

    @field_validator("terms")
    @classmethod
    def normalize_terms(cls, values: list[str]) -> list[str]:
        """规范化集合用于查询和游标绑定；不改变词内部空白或解释表达式。"""
        if any(not value.strip() or "\x00" in value for value in values):
            raise ValueError("关键词不得为空白或包含 NUL")
        return sorted({value.strip() for value in values})


class SearchExcerpt(BaseModel):
    text: str
    character_start: int
    truncated_before: bool
    truncated_after: bool
    match_located: bool


class SourceSearchHit(BaseModel):
    work_id: UUID
    section_id: UUID
    section_title: str
    section_ordinal: int
    paragraph_id: UUID
    ordinal: int
    source_range: SourceRange
    match_kind: Literal["source_text"] = "source_text"
    excerpt: SearchExcerpt


class AnnotationSearchHit(BaseModel):
    work_id: UUID
    annotation_id: UUID
    version: int
    created_at: datetime
    first_source_range: SourceRange
    source_range_count: int
    match_kind: Literal["annotation_note"] = "annotation_note"
    excerpt: SearchExcerpt
