"""业务目录的阅读投影；保留身份、状态、真实引用和下一步操作所需版本。"""

from typing import Any
from uuid import UUID

from pydantic import BaseModel, SerializerFunctionWrapHandler, model_serializer

from novel_lens.asset_contracts import AnalysisTarget, JobStatus
from novel_lens.contracts import Page, ParagraphOut, ParagraphSpan
from novel_lens.query_views import CompactAnnotationOut, CompactAnnotationPage


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
    description: str
    aliases: list[str]


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
    annotations: CompactAnnotationPage | None = None
    annotation: CompactAnnotationOut | None = None
    tags: list[CompactTag] | None = None
    jobs: Page[CompactJob] | None = None

    @model_serializer(mode="wrap")
    def used_fields(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        return {key: value for key, value in handler(self).items() if value is not None}


class FullSourcePage(Page[ParagraphOut]):
    """业务 full 原文页保留 compact 的归属与真实页界，另附段落字节位置。"""

    work_id: UUID
    section_id: UUID
    actual_range: ParagraphSpan | None
    requested_range: ParagraphSpan | None = None

    @model_serializer(mode="wrap")
    def optional_request(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        result: dict[str, Any] = handler(self)
        if self.requested_range is None:
            result.pop("requested_range", None)
        return result
