"""业务目录的阅读投影；保留身份、状态、真实引用和下一步操作所需版本。"""

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, SerializerFunctionWrapHandler, model_serializer

from novel_lens.asset_contracts import (
    AnalysisTarget,
    AnnotationKind,
    AnnotationLocation,
    AnnotationStatus,
    CategorySummary,
    JobStatus,
    TagIdentity,
)
from novel_lens.contracts import (
    CompactParagraphPage,
    CompactReadOut,
    Page,
    ParagraphOut,
    ParagraphSpan,
    SourceRange,
)
from novel_lens.query_views import CompactAnnotationOut
from novel_lens.tag_categories import Categories


class NamedItem(BaseModel):
    id: UUID
    name: str


class CompactPart(NamedItem):
    ordinal: int


class CompactSection(BaseModel):
    id: UUID
    part_id: UUID
    part_name: str
    ordinal: int
    title: str


class CompactTag(NamedItem):
    namespace: str
    full_name: str
    description: str
    aliases: list[str]
    categories: Categories = Field(default_factory=list)
    annotation_count: int | None = None


class CompactBrowsedAnnotation(BaseModel):
    """跨作品结果每条保留归属和人类可读位置；不重复全文说明。"""

    work_id: UUID
    id: UUID
    version: int
    status: AnnotationStatus
    kind: AnnotationKind
    title: str
    scope_note: str
    source_range_count: int
    note_preview: str | None
    note_truncated: bool
    tags: list[TagIdentity]
    work_name: str
    part_name: str
    section_title: str
    first_source_range: SourceRange


class CompactJob(BaseModel):
    id: UUID
    title: str
    status: JobStatus
    version: int
    target: AnalysisTarget
    goal_preview: str
    goal_truncated: bool


class CompactLibraryPage(BaseModel):
    """只有当前视图有值，顶层未使用槽位不进入协议响应。"""

    view: str
    work: NamedItem | None = None
    works: Page[NamedItem] | None = None
    parts: Page[CompactPart] | None = None
    sections: Page[CompactSection] | None = None
    annotations: Page[CompactBrowsedAnnotation] | None = None
    annotation: CompactAnnotationOut | None = None
    tags: list[CompactTag] | None = None
    tag_page: Page[CompactTag] | None = None
    jobs: Page[CompactJob] | None = None
    categories: list[CategorySummary] | None = None
    locations: list[AnnotationLocation] | None = None

    @model_serializer(mode="wrap")
    def used_fields(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        return {key: value for key, value in handler(self).items() if value is not None}


class FullSourcePage(Page[ParagraphOut]):
    """业务 full 原文页保留 compact 的归属与真实页界，另附段落字节位置。"""

    work_id: UUID
    section_id: UUID
    actual_range: ParagraphSpan | None
    requested_range: ParagraphSpan | None = None
    next_request: dict[str, Any] | None = None

    @model_serializer(mode="wrap")
    def optional_request(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        result: dict[str, Any] = handler(self)
        if self.requested_range is None:
            result.pop("requested_range", None)
        return result


class ContinuedParagraphPage(CompactParagraphPage):
    """下一页参数可原样交给 source_read；最后一页为 null。"""

    next_request: dict[str, Any] | None


class ContinuedReadOut(CompactReadOut):
    next_request: dict[str, Any] | None
