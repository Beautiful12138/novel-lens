"""作品准备的共享输入输出；导入、标记、索引状态和完成各有明确边界。"""

from datetime import datetime
from typing import Any, Literal, Self
from uuid import UUID

from pydantic import (
    BaseModel,
    Field,
    SerializerFunctionWrapHandler,
    model_serializer,
    model_validator,
)

from novel_lens.asset_contracts import (
    Alias,
    AnalysisJobOut,
    AnalysisJobSummary,
    AnnotationOut,
    AnnotationStatus,
    AnnotationSummary,
    CoveragePage,
    Name,
    Namespace,
    Nonblank,
    Recovery,
    TagOut,
)
from novel_lens.contracts import (
    Page,
    PartOut,
    ReadingFormat,
    RequestModel,
    SectionOut,
    SourceRange,
    WorkOut,
)


class PrepareImport(RequestModel):
    request_id: UUID
    file_path: str = Field(min_length=1)
    name: Alias | None = None
    work_id: UUID | None = None

    @model_validator(mode="after")
    def destination(self) -> Self:
        if (self.name is None) == (self.work_id is None):
            raise ValueError("新作品提供 name，追加分部提供 work_id，二者选一")
        return self


class PreparedImport(BaseModel):
    request_id: UUID
    replayed: bool = False
    work: WorkOut
    part: PartOut
    job: AnalysisJobOut


class TagInput(RequestModel):
    namespace: Namespace
    name: Name
    description: Nonblank


class MarkInput(RequestModel):
    """完整标记内容；更新与撤回须提供当前版本，不能盲写覆盖。"""

    annotation_id: UUID | None = None
    expected_version: int | None = Field(default=None, ge=1)
    source_ranges: list[SourceRange] = Field(min_length=1, max_length=100)
    tags: list[TagInput] = Field(default_factory=list, max_length=40)
    note: Nonblank | None = None
    status: AnnotationStatus = "active"

    @model_validator(mode="after")
    def identity(self) -> Self:
        if (self.annotation_id is None) != (self.expected_version is None):
            raise ValueError("修订标记须同时提供 annotation_id 和 expected_version")
        if self.annotation_id is None and self.status != "active":
            raise ValueError("不能创建已撤回标记")
        return self


class PrepareBatch(RequestModel):
    request_id: UUID
    work_id: UUID
    job_id: UUID
    expected_version: int = Field(ge=1)
    source_range: SourceRange
    marks: list[MarkInput] = Field(default_factory=list, max_length=50)
    recovery: Recovery
    outcome_note: Nonblank
    reopen_reason: Nonblank | None = None

    @model_validator(mode="after")
    def mark_ids(self) -> Self:
        if self.reopen_reason is not None and not self.marks:
            raise ValueError("重开准备任务须同时提交至少一条标记")
        ids = [m.annotation_id for m in self.marks if m.annotation_id is not None]
        if len(ids) != len(set(ids)):
            raise ValueError("同一批不能重复修订同一标记")
        return self


class PreparedBatch(BaseModel):
    request_id: UUID
    replayed: bool = False
    work_id: UUID
    job_id: UUID
    version: int
    annotations: list[AnnotationOut]


class ReferenceState(BaseModel):
    source_ready: bool
    clues_ready: bool
    state: Literal["pending", "ready", "failed", "blocked"]
    last_error: str | None = None
    revision: int
    indexed_revision: int
    source_coverage: dict[str, Any]


class PrepareStatus(RequestModel):
    work_id: UUID
    job_id: UUID
    limit: int = Field(default=20, ge=1, le=100)
    cursor: str | None = None


class PreparedStatus(BaseModel):
    job: AnalysisJobOut
    remaining: CoveragePage
    indexes: ReferenceState
    work_ready: bool


class PrepareFinish(RequestModel):
    request_id: UUID
    work_id: UUID
    job_id: UUID
    expected_version: int = Field(ge=1)
    recovery: Recovery
    note: Nonblank


class PreparedFinish(BaseModel):
    request_id: UUID
    replayed: bool = False
    job: AnalysisJobOut


class PreparationReceipt(RequestModel):
    request_id: UUID


class PrepareCleanup(RequestModel):
    work_id: UUID
    confirm_work_name: Alias


class CleanupResult(BaseModel):
    work_id: UUID
    deleted: bool


class ReferenceScope(RequestModel):
    """每部作品选择全部、分部或章节；拒绝隐式空范围及重复选择。"""

    work_id: UUID
    part_ids: list[UUID] | None = Field(default=None, min_length=1, max_length=200)
    section_ids: list[UUID] | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def selection(self) -> Self:
        if self.part_ids is not None and self.section_ids is not None:
            raise ValueError("分部与章节只能选择一种")
        for name in ("part_ids", "section_ids"):
            values = getattr(self, name)
            if name in self.model_fields_set and values is None:
                raise ValueError("显式范围不得为空")
            if values is not None and len(values) != len(set(values)):
                raise ValueError("范围 ID 不得重复")
        return self


class ReferenceQuery(RequestModel):
    """新查与快照续查互斥；显式提供默认值也不能绕过续查参数约束。"""

    query: Nonblank | None = Field(default=None, max_length=8192)
    scope: list[ReferenceScope] | None = Field(default=None, min_length=1, max_length=20)
    terms: list[Nonblank] = Field(default_factory=list, max_length=8)
    exclude_ranges: list[SourceRange] = Field(default_factory=list, max_length=200)
    limit: int = Field(default=8, ge=1, le=30)
    format: ReadingFormat = "compact"
    search_id: UUID | None = None
    cursor: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def operation(self) -> Self:
        if self.search_id is not None:
            if self.model_fields_set & {"query", "scope", "terms", "exclude_ranges", "limit"}:
                raise ValueError("续查只接受 search_id、cursor 和 format")
        elif (
            self.query is None
            or self.scope is None
            or self.model_fields_set & {"search_id", "cursor"}
        ):
            raise ValueError("新查询须提供 query 和 scope，不接受续查字段")
        if self.scope is not None:
            if len({s.work_id for s in self.scope}) != len(self.scope):
                raise ValueError("同一作品只能选择一次")
            if sum(len(s.part_ids or s.section_ids or []) for s in self.scope) > 200:
                raise ValueError("范围选择最多 200 个 ID")
        if any(len(term) > 128 for term in self.terms):
            raise ValueError("每个字面线索最多 128 字符")
        return self


class CompactReferenceHit(BaseModel):
    work_name: str
    source_range: SourceRange
    part_id: UUID
    part_name: str
    section_title: str
    excerpt: str
    excerpt_truncated: bool
    annotation_ids: list[UUID]


class ReferenceHit(CompactReferenceHit):
    score: float
    channels: list[str]


class ReferenceWarning(BaseModel):
    code: Literal["CLUES_PENDING", "CLUES_BLOCKED"]
    work_id: UUID
    message: str


class ReferencePage[T: CompactReferenceHit](BaseModel):
    """两种投影共享页身份和警告，候选类型明确区分是否包含诊断字段。"""

    search_id: UUID
    snapshot_at: datetime
    expires_at: datetime
    items: list[T]
    next_cursor: str | None
    candidate_window_limited: bool
    warnings: list[ReferenceWarning] = Field(default_factory=list)

    @model_serializer(mode="wrap")
    def reading_payload(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        """正常阅读省略空警告，仍保留具有流程意义的 null 下一页。"""
        result: dict[str, Any] = handler(self)
        if not self.warnings:
            result.pop("warnings", None)
        return result


class CompactReferenceResult(ReferencePage[CompactReferenceHit]):
    """默认阅读页，不输出得分或索引统计。"""


class ReferenceResult(ReferencePage[ReferenceHit]):
    diagnostics: dict[str, Any]


class LibraryBrowse(RequestModel):
    """按一种目录投影分页；不把写入操作混入导航。"""

    view: Literal["works", "parts", "sections", "annotations", "jobs"] = "works"
    work_id: UUID | None = None
    part_id: UUID | None = None
    annotation_id: UUID | None = None
    limit: int = Field(default=20, ge=1, le=100)
    cursor: str | None = None
    format: ReadingFormat = "compact"

    @model_validator(mode="after")
    def scope(self) -> Self:
        if (self.view != "works") != (self.work_id is not None):
            raise ValueError("作品列表不接受 work_id，其他视图必须指定 work_id")
        if self.part_id is not None and self.view != "sections":
            raise ValueError("part_id 只用于筛选章节")
        if self.annotation_id is not None and (
            self.view != "annotations" or self.cursor is not None
        ):
            raise ValueError("annotation_id 只用于单条标记详情，不接受游标")
        return self


class LibraryPage(BaseModel):
    view: str
    work: WorkOut | None = None
    works: Page[WorkOut] | None = None
    parts: Page[PartOut] | None = None
    sections: Page[SectionOut] | None = None
    annotations: Page[AnnotationSummary] | None = None
    annotation: AnnotationOut | None = None
    tags: list[TagOut] | None = None
    jobs: Page[AnalysisJobSummary] | None = None
    indexes: ReferenceState | None = None


class SourceRead(RequestModel):
    """无端点读取整章；指定端点时按范围读取，可扩展上下文。"""

    work_id: UUID
    section_id: UUID
    start_paragraph_id: UUID | None = None
    end_paragraph_id: UUID | None = None
    before: int = Field(default=0, ge=0, le=100)
    after: int = Field(default=0, ge=0, le=100)
    limit: int = Field(default=60, ge=1, le=200)
    cursor: str | None = None
    format: ReadingFormat = "compact"

    @model_validator(mode="after")
    def endpoints(self) -> Self:
        if (self.start_paragraph_id is None) != (self.end_paragraph_id is None):
            raise ValueError("范围读取须同时提供起止段落")
        if self.start_paragraph_id is None and (self.before or self.after):
            raise ValueError("上下文扩展须提供明确范围")
        return self


class PreparationInspect(RequestModel):
    """当前任务状态与已提交回执二选一，不改变任何准备数据。"""

    request_id: UUID | None = None
    work_id: UUID | None = None
    job_id: UUID | None = None
    limit: int = Field(default=20, ge=1, le=100)
    cursor: str | None = None

    @model_validator(mode="after")
    def identity(self) -> Self:
        if self.request_id is not None:
            if self.work_id is not None or self.job_id is not None or self.cursor is not None:
                raise ValueError("回执查询只提供 request_id")
        elif self.work_id is None or self.job_id is None:
            raise ValueError("状态查询须提供 work_id 和 job_id")
        return self
