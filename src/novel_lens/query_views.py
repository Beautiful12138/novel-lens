"""HTTP / MCP 共用的阅读投影；业务服务保留完整对象，写入与历史回执不投影。"""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from novel_lens.asset_contracts import (
    MAX_ASSET_RESULT_BYTES,
    AnnotationKind,
    AnnotationOut,
    AnnotationStatus,
    AnnotationSummary,
    TagIdentity,
)
from novel_lens.contracts import Page, ReadingFormat, SourceRange
from novel_lens.errors import ServiceError
from novel_lens.search_contracts import AnnotationSearchHit, SearchExcerpt, SourceSearchHit


class CompactSourceRange(BaseModel):
    """与响应顶层 work_id 合成完整 SourceRange，跨章节范围各自保留 section_id。"""

    section_id: UUID
    start_paragraph_id: UUID
    end_paragraph_id: UUID


class CompactExcerpt(BaseModel):
    text: str
    truncated_before: bool
    truncated_after: bool
    match_located: bool


class CompactSourceHit(BaseModel):
    part_id: UUID
    part_name: str
    section_title: str
    ordinal: int
    source_range: CompactSourceRange
    excerpt: CompactExcerpt


class CompactSourceResults(Page[CompactSourceHit]):
    work_id: UUID
    match_kind: Literal["source_text"] = "source_text"


class CompactAnnotationHit(BaseModel):
    part_id: UUID
    part_name: str
    section_title: str
    annotation_id: UUID
    version: int
    status: AnnotationStatus
    first_source_range: CompactSourceRange
    source_range_count: int
    excerpt: CompactExcerpt


class CompactAnnotationResults(Page[CompactAnnotationHit]):
    work_id: UUID
    match_kind: Literal["annotation_note"] = "annotation_note"


class CompactAnnotationReference(BaseModel):
    """压缩证据元数据，阅读目标保持可直接传给 source_read 的完整参数。"""

    evidence_range: CompactSourceRange
    read_target: SourceRange
    role_note: str


class CompactAnnotationSummary(BaseModel):
    id: UUID
    version: int
    kind: AnnotationKind
    title: str
    scope_note: str
    status: AnnotationStatus
    first_source_range: CompactSourceRange
    source_range_count: int
    note_preview: str | None
    note_truncated: bool
    tags: list[TagIdentity]


class CompactAnnotationPage(Page[CompactAnnotationSummary]):
    work_id: UUID


class CompactAnnotationOut(BaseModel):
    id: UUID
    work_id: UUID
    version: int
    kind: AnnotationKind
    title: str
    scope_note: str
    status: AnnotationStatus
    note: str | None
    tag_ids: list[UUID]
    entity_ids: list[UUID]
    references: list[CompactAnnotationReference]


def bounded_view[T: BaseModel](value: T) -> T:
    """投影后检查响应容量；完整内容超限时拒绝，不静默删减正文或候选。"""
    if len(value.model_dump_json().encode("utf-8")) > MAX_ASSET_RESULT_BYTES:
        raise ServiceError("RESULT_TOO_LARGE", "结果超过 1 MiB，请减小 limit 或读取范围")
    return value


def compact_range(value: SourceRange) -> CompactSourceRange:
    return CompactSourceRange.model_validate(value.model_dump(exclude={"work_id"}))


def compact_excerpt(value: SearchExcerpt) -> CompactExcerpt:
    return CompactExcerpt.model_validate(value.model_dump(exclude={"character_start"}))


def source_search_view(
    page: Page[SourceSearchHit], work_id: UUID, format: ReadingFormat
) -> Page[SourceSearchHit] | CompactSourceResults:
    """搜索结果只瘦身元数据，保留原摘录和可回读坐标。"""
    if format == "full":
        return bounded_view(page)
    return bounded_view(
        CompactSourceResults(
            work_id=work_id,
            next_cursor=page.next_cursor,
            items=[
                CompactSourceHit(
                    part_id=v.part_id,
                    part_name=v.part_name,
                    section_title=v.section_title,
                    ordinal=v.ordinal,
                    source_range=compact_range(v.source_range),
                    excerpt=compact_excerpt(v.excerpt),
                )
                for v in page.items
            ],
        )
    )


def annotation_search_view(
    page: Page[AnnotationSearchHit], work_id: UUID, format: ReadingFormat
) -> Page[AnnotationSearchHit] | CompactAnnotationResults:
    """说明摘录保持原样，重复作品归属集中在页顶层。"""
    if format == "full":
        return bounded_view(page)
    return bounded_view(
        CompactAnnotationResults(
            work_id=work_id,
            next_cursor=page.next_cursor,
            items=[
                CompactAnnotationHit(
                    part_id=v.part_id,
                    part_name=v.part_name,
                    section_title=v.section_title,
                    annotation_id=v.annotation_id,
                    version=v.version,
                    status=v.status,
                    first_source_range=compact_range(v.first_source_range),
                    source_range_count=v.source_range_count,
                    excerpt=compact_excerpt(v.excerpt),
                )
                for v in page.items
            ],
        )
    )


def annotation_view(
    value: AnnotationOut, format: ReadingFormat
) -> AnnotationOut | CompactAnnotationOut:
    """保留全部解释、证据和关联；纠错写入仍应显式请求完整详情。"""
    if format == "full":
        return bounded_view(value)
    return bounded_view(
        CompactAnnotationOut.model_validate(
            value.model_dump(exclude={"created_at", "updated_at", "references"})
            | {
                "references": [
                    CompactAnnotationReference(
                        evidence_range=compact_range(v.evidence_range),
                        read_target=v.reading_range or v.evidence_range,
                        role_note=v.role_note,
                    )
                    for v in value.references
                ]
            }
        )
    )


def annotation_list_view(
    page: Page[AnnotationSummary], work_id: UUID, format: ReadingFormat
) -> Page[AnnotationSummary] | CompactAnnotationPage:
    """列表保留选择候选和续页所需信息，不将预览扩充为完整说明。"""
    if format == "full":
        return bounded_view(page)
    return bounded_view(
        CompactAnnotationPage(
            work_id=work_id,
            next_cursor=page.next_cursor,
            items=[
                CompactAnnotationSummary.model_validate(
                    v.model_dump(
                        exclude={"work_id", "created_at", "updated_at", "tag_count", "entity_count"}
                    )
                    | {"first_source_range": compact_range(v.first_source_range)}
                )
                for v in page.items
            ],
        )
    )
