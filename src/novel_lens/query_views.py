"""HTTP / MCP 共用的阅读投影；业务服务保留完整对象，写入与历史回执不投影。"""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from novel_lens.asset_contracts import (
    MAX_ASSET_RESULT_BYTES,
    AnnotationOut,
    AnnotationStatus,
    AnnotationSummary,
)
from novel_lens.contracts import Page, ReadingFormat, SourceRange
from novel_lens.errors import ServiceError
from novel_lens.search_contracts import AnnotationSearchHit, SearchExcerpt, SourceSearchHit
from novel_lens.semantic_contracts import (
    AnnotationSemanticCoverage,
    AnnotationSemanticHit,
    SemanticCoverage,
    SemanticKind,
    SemanticResults,
)


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


class CompactAnnotationSummary(BaseModel):
    id: UUID
    version: int
    status: AnnotationStatus
    first_source_range: CompactSourceRange
    source_range_count: int
    note_preview: str | None
    note_truncated: bool


class CompactAnnotationPage(Page[CompactAnnotationSummary]):
    work_id: UUID


class CompactAnnotationOut(BaseModel):
    id: UUID
    work_id: UUID
    version: int
    status: AnnotationStatus
    note: str | None
    tag_ids: list[UUID]
    entity_ids: list[UUID]
    source_ranges: list[CompactSourceRange]


class CompactSemanticHit(BaseModel):
    part_id: UUID
    part_name: str
    section_title: str
    source_range: CompactSourceRange
    excerpt: str
    excerpt_truncated: bool
    score: float


class CompactAnnotationSemanticHit(CompactSemanticHit):
    annotation_id: UUID
    annotation_version: int
    range_ordinal: int


class CompactSemanticResults(BaseModel):
    work_id: UUID
    kind: SemanticKind
    coverage: AnnotationSemanticCoverage | SemanticCoverage
    partial: bool
    candidate_window_limited: bool
    items: list[CompactAnnotationSemanticHit | CompactSemanticHit]


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
            value.model_dump(exclude={"created_at", "updated_at", "source_ranges"})
            | {"source_ranges": [compact_range(v) for v in value.source_ranges]}
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


def semantic_search_view(
    value: SemanticResults, work_id: UUID, kind: SemanticKind, format: ReadingFormat
) -> SemanticResults | CompactSemanticResults:
    """省略索引管理字段，保留所有覆盖缺口和原文命中的分数、版本及截断信息。"""
    if format == "full":
        return bounded_view(value)
    items: list[CompactAnnotationSemanticHit | CompactSemanticHit] = []
    for hit in value.items:
        payload = hit.model_dump(exclude={"kind", "source_range"}) | {
            "source_range": compact_range(hit.source_range)
        }
        model = (
            CompactAnnotationSemanticHit
            if isinstance(hit, AnnotationSemanticHit)
            else CompactSemanticHit
        )
        items.append(model.model_validate(payload))
    return bounded_view(
        CompactSemanticResults(
            work_id=work_id,
            kind=kind,
            coverage=value.coverage,
            partial=value.partial,
            candidate_window_limited=value.candidate_window_limited,
            items=items,
        )
    )
