"""全文与标注原文语义索引的显式批次、动态覆盖及有界 Top-K 契约。"""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from novel_lens.contracts import ReadingFormat, RequestModel, SourceRange

GenerationStatus = Literal["building", "ready", "partial", "failed", "superseded"]
SemanticKind = Literal["fulltext", "annotation"]


class SemanticCreate(RequestModel):
    work_id: UUID
    kind: SemanticKind = "fulltext"
    request_id: UUID


class SemanticBuild(RequestModel):
    work_id: UUID
    index_id: UUID
    request_id: UUID
    max_items: int = Field(default=4, ge=1, le=4, strict=True)


class SemanticGet(RequestModel):
    work_id: UUID
    kind: SemanticKind = "fulltext"
    index_id: UUID | None = None
    limit: int = Field(default=100, ge=1, le=100, strict=True)
    cursor: str | None = Field(default=None, max_length=2048, strict=True)


class SemanticSearch(RequestModel):
    part_id: UUID | None = None
    format: ReadingFormat = "compact"
    work_id: UUID
    kind: SemanticKind = "fulltext"
    query: str = Field(min_length=1, max_length=8192, strict=True)
    limit: int = Field(default=10, ge=1, le=50, strict=True)
    index_id: UUID | None = None
    allow_partial: bool = Field(default=False, strict=True)

    @field_validator("query")
    @classmethod
    def valid_query(cls, value: str) -> str:
        """只校验，不改变查询内部空白或 Unicode 表达。"""
        if not value.strip() or "\x00" in value:
            raise ValueError("查询不得全为空白或包含 NUL")
        return value


class SemanticCoverage(BaseModel):
    total: int
    covered: int
    blocked: int
    pending: int
    complete: bool


class AnnotationSemanticCoverage(SemanticCoverage):
    """标注计数；未纳入、过期、完整、阻塞与待处理互斥。"""

    stale: int
    not_indexed: int


class SemanticSummary(BaseModel):
    index_id: UUID
    contract_id: str
    generation_status: GenerationStatus
    coverage: AnnotationSemanticCoverage | SemanticCoverage
    last_error: str | None = None
    source_stale: bool = False


class SemanticBlocked(BaseModel):
    source_range: SourceRange
    reason: Literal["INPUT_TOO_LONG", "TOKENIZATION_INPUT_TOO_LARGE"]
    tokens: int | None
    bytes: int


class AnnotationSemanticBlocked(SemanticBlocked):
    annotation_id: UUID
    annotation_version: int
    range_ordinal: int


class SemanticStatus(BaseModel):
    work_id: UUID
    kind: SemanticKind = "fulltext"
    state: Literal["missing", "present"]
    active: SemanticSummary | None = None
    target: SemanticSummary | None = None
    index: SemanticSummary | None = None
    blocked: list[AnnotationSemanticBlocked | SemanticBlocked] = Field(default_factory=list)
    next_cursor: str | None = None


class SemanticWrite(BaseModel):
    index_id: UUID
    contract_id: str
    generation_status: GenerationStatus
    coverage: AnnotationSemanticCoverage | SemanticCoverage
    active_index_id: UUID | None
    target_index_id: UUID | None
    batch_ready: int = 0
    batch_blocked: int = 0
    batch_tokens: int = 0
    batch_discarded: int = 0
    replayed: bool = False


class SemanticPassage(BaseModel):
    """两层共用的原文命中，不含文学质量判断。"""

    part_id: UUID
    part_name: str
    section_title: str
    source_range: SourceRange
    excerpt: str
    excerpt_truncated: bool
    score: float


class SemanticHit(SemanticPassage):
    kind: Literal["fulltext"] = "fulltext"


class AnnotationSemanticHit(SemanticPassage):
    kind: Literal["annotation"] = "annotation"
    annotation_id: UUID
    annotation_version: int
    range_ordinal: int


class SemanticResults(BaseModel):
    index_id: UUID
    contract_id: str
    generation_status: GenerationStatus
    coverage: AnnotationSemanticCoverage | SemanticCoverage
    partial: bool
    candidate_window_limited: bool
    items: list[AnnotationSemanticHit | SemanticHit]
