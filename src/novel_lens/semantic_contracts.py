"""全文语义索引的显式批次、状态和有界 Top-K 契约。"""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from novel_lens.contracts import RequestModel, SourceRange

GenerationStatus = Literal["building", "ready", "partial", "failed", "superseded"]


class SemanticCreate(RequestModel):
    work_id: UUID
    kind: Literal["fulltext"] = "fulltext"
    request_id: UUID


class SemanticBuild(RequestModel):
    work_id: UUID
    index_id: UUID
    request_id: UUID
    max_items: int = Field(default=4, ge=1, le=4, strict=True)


class SemanticGet(RequestModel):
    work_id: UUID
    kind: Literal["fulltext"] = "fulltext"
    index_id: UUID | None = None
    limit: int = Field(default=100, ge=1, le=100, strict=True)
    cursor: str | None = Field(default=None, max_length=2048, strict=True)


class SemanticSearch(RequestModel):
    work_id: UUID
    kind: Literal["fulltext"] = "fulltext"
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


class SemanticSummary(BaseModel):
    index_id: UUID
    contract_id: str
    generation_status: GenerationStatus
    coverage: SemanticCoverage
    last_error: str | None = None


class SemanticBlocked(BaseModel):
    source_range: SourceRange
    reason: Literal["INPUT_TOO_LONG", "TOKENIZATION_INPUT_TOO_LARGE"]
    tokens: int | None
    bytes: int


class SemanticStatus(BaseModel):
    work_id: UUID
    kind: Literal["fulltext"] = "fulltext"
    state: Literal["missing", "present"]
    active: SemanticSummary | None = None
    target: SemanticSummary | None = None
    index: SemanticSummary | None = None
    blocked: list[SemanticBlocked] = Field(default_factory=list)
    next_cursor: str | None = None


class SemanticWrite(BaseModel):
    index_id: UUID
    contract_id: str
    generation_status: GenerationStatus
    coverage: SemanticCoverage
    active_index_id: UUID | None
    target_index_id: UUID | None
    batch_ready: int = 0
    batch_blocked: int = 0
    batch_tokens: int = 0
    replayed: bool = False


class SemanticHit(BaseModel):
    kind: Literal["fulltext"] = "fulltext"
    source_range: SourceRange
    excerpt: str
    excerpt_truncated: bool
    score: float


class SemanticResults(BaseModel):
    index_id: UUID
    contract_id: str
    generation_status: GenerationStatus
    coverage: SemanticCoverage
    partial: bool
    candidate_window_limited: bool
    items: list[SemanticHit]
