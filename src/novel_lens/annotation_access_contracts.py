"""AI 批量查读、可取得历史和有界导出的请求与结果。"""

from datetime import datetime
from typing import Any, Literal, Self
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from novel_lens.asset_contracts import AnnotationOut, AnnotationStatus, BrowsedAnnotationDetail
from novel_lens.contracts import RequestModel, SourceRange


class AnnotationMany(RequestModel):
    work_id: UUID
    annotation_ids: list[UUID] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def distinct(self) -> Self:
        if len(self.annotation_ids) != len(set(self.annotation_ids)):
            raise ValueError("批量详情中的标注 ID 不可重复")
        return self


class AnnotationDetails(BaseModel):
    work_id: UUID
    items: list[BrowsedAnnotationDetail]


class AnnotationHistory(RequestModel):
    work_id: UUID
    annotation_id: UUID
    version: int | None = Field(default=None, ge=1)
    limit: int = Field(default=20, ge=1, le=100)
    cursor: str | None = None

    @model_validator(mode="after")
    def single_version(self) -> Self:
        if self.version is not None and self.cursor is not None:
            raise ValueError("指定版本不接受分页游标")
        return self


class AnnotationRevision(BaseModel):
    version: int
    title: str
    status: AnnotationStatus
    updated_at: datetime
    request_id: UUID | None
    origin: Literal["receipt", "current"]
    snapshot: AnnotationOut | None = None


class AnnotationHistoryPage(BaseModel):
    work_id: UUID
    annotation_id: UUID
    current_version: int
    available_version_count: int
    history_complete: bool
    items: list[AnnotationRevision]
    next_cursor: str | None


class AnnotationDiff(RequestModel):
    work_id: UUID
    annotation_id: UUID
    from_version: int = Field(ge=1)
    to_version: int = Field(ge=1)


class FieldChange(BaseModel):
    field: str
    before: Any
    after: Any


class AnnotationDifference(BaseModel):
    annotation_id: UUID
    from_version: int
    to_version: int
    changes: list[FieldChange]


class AnnotationExport(RequestModel):
    """job_id 仅选该任务批次写过的标注当前版本，不推断引用归属。"""

    work_id: UUID
    job_id: UUID | None = None
    status: AnnotationStatus | None = None
    limit: int = Field(default=20, ge=1, le=50)
    cursor: str | None = None


class AnnotationExportPage(AnnotationDetails):
    snapshot: str
    total: int
    next_cursor: str | None


class PrepareBatches(RequestModel):
    work_id: UUID
    job_id: UUID
    limit: int = Field(default=20, ge=1, le=100)
    cursor: str | None = None


class WrittenAnnotation(BaseModel):
    annotation_id: UUID
    version: int


class PreparationBatchRecord(BaseModel):
    request_id: UUID
    previous_job_version: int
    job_version: int
    source_range: SourceRange | None
    outcome_note: str | None
    annotations: list[WrittenAnnotation]
    metadata_available: bool


class PreparationBatchPage(BaseModel):
    work_id: UUID
    job_id: UUID
    through_job_version: int
    items: list[PreparationBatchRecord]
    next_cursor: str | None
