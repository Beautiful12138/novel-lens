"""作品准备的共享输入输出；导入、标记、已读进度、预检和完成各有明确边界。"""

from typing import Literal, Self
from uuid import UUID

from pydantic import (
    AliasChoices,
    BaseModel,
    Field,
    model_validator,
)

from novel_lens.asset_contracts import (
    Alias,
    Aliases,
    AnalysisJobOut,
    AnalysisJobSummary,
    AnnotationKind,
    AnnotationLocation,
    AnnotationOut,
    AnnotationStatus,
    BrowsedAnnotation,
    BrowsedTag,
    CategorySummary,
    CoveragePage,
    Name,
    Namespace,
    Nonblank,
    Recovery,
    TagIds,
    TagOut,
)
from novel_lens.contracts import (
    Page,
    PartOut,
    RangeInput,
    ReadingFormat,
    RequestModel,
    SectionOut,
    WorkOut,
)
from novel_lens.tag_categories import Categories, CategoryId


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
    categories: Categories = Field(default_factory=list)
    aliases: Aliases = Field(default_factory=list)


class ReferenceInput(RequestModel):
    """业务引用允许 UUID 或段号，输出和存储统一为完整 UUID 范围。"""

    evidence_range: RangeInput
    reading_range: RangeInput | None = None
    role_note: Nonblank


class MarkInput(RequestModel):
    """完整标记内容；更新与撤回须提供当前版本，不能盲写覆盖。"""

    kind: AnnotationKind
    title: Alias
    scope_note: Nonblank
    note: Nonblank
    references: list[ReferenceInput] = Field(min_length=1, max_length=100)
    annotation_id: UUID | None = None
    expected_version: int | None = Field(
        default=None,
        ge=1,
        validation_alias=AliasChoices("expected_annotation_version", "expected_version"),
        description="被修订标注的当前版本，与批次的任务版本不同",
    )
    tags: list[TagInput] = Field(default_factory=list, max_length=40)
    status: AnnotationStatus = "active"

    @model_validator(mode="after")
    def identity(self) -> Self:
        if (self.annotation_id is None) != (self.expected_version is None):
            raise ValueError("修订标记须同时提供 annotation_id 和 expected_version")
        if self.annotation_id is None and self.status != "active":
            raise ValueError("不能创建已撤回标记")
        return self


class MarkPatch(RequestModel):
    """显式局部修订；省略字段保留现值，空数组只用于明确清空标签或引用增量。"""

    operation: Literal["patch"]
    annotation_id: UUID
    expected_version: int = Field(
        ge=1,
        validation_alias=AliasChoices("expected_annotation_version", "expected_version"),
        description="被修订标注的当前版本",
    )
    kind: AnnotationKind | None = None
    title: Alias | None = None
    scope_note: Nonblank | None = None
    note: Nonblank | None = None
    references: list[ReferenceInput] | None = Field(default=None, min_length=1, max_length=100)
    add_references: list[ReferenceInput] = Field(default_factory=list, max_length=100)
    remove_references: list[RangeInput] = Field(default_factory=list, max_length=100)
    tags: list[TagInput] | None = Field(default=None, max_length=40)
    status: AnnotationStatus | None = None

    @model_validator(mode="after")
    def explicit_changes(self) -> Self:
        fields = self.model_fields_set - {"operation", "annotation_id", "expected_version"}
        if not fields:
            raise ValueError("局部修订须提供至少一个修改字段")
        if any(getattr(self, name) is None for name in fields):
            raise ValueError("省略字段表示保留，不接受显式 null")
        if self.references is not None and (self.add_references or self.remove_references):
            raise ValueError("完整引用替换与引用增删互斥")
        return self


class PrepareBatch(RequestModel):
    request_id: UUID
    work_id: UUID
    job_id: UUID
    expected_version: int = Field(
        ge=1,
        validation_alias=AliasChoices("expected_job_version", "expected_version"),
        description="当前分析任务版本，不是标注版本",
    )
    source_range: RangeInput
    marks: list[MarkInput | MarkPatch] = Field(default_factory=list, max_length=50)
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


class PrepareStatus(RequestModel):
    work_id: UUID
    job_id: UUID
    limit: int = Field(default=20, ge=1, le=100)
    cursor: str | None = None


class PreparedStatus(BaseModel):
    job: AnalysisJobOut
    remaining: CoveragePage
    work_ready: bool


class PreparedRead(PreparedStatus):
    """显式已读写入的提交快照，可按请求键恢复。"""

    request_id: UUID
    replayed: bool = False


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


class LibraryBrowse(RequestModel):
    """分类、标签、标注共享作品范围；单条详情须明确作品身份。"""

    kind: AnnotationKind | None = None
    view: Literal["works", "parts", "sections", "annotations", "jobs", "tags", "categories"] = (
        "works"
    )
    work_id: UUID | None = None
    work_ids: TagIds | None = Field(default=None, min_length=1, max_length=20)
    part_id: UUID | None = None
    section_id: UUID | None = None
    annotation_id: UUID | None = None
    namespace: Namespace | None = None
    category: CategoryId | Literal["unclassified"] | None = None
    query: Nonblank | None = None
    tag_ids: TagIds = Field(default_factory=list)
    tag_match: Literal["any", "all"] = "any"
    source_range: RangeInput | None = None
    status: AnnotationStatus | None = "active"
    limit: int = Field(default=20, ge=1, le=100)
    cursor: str | None = None
    format: ReadingFormat = "compact"

    @model_validator(mode="after")
    def scope(self) -> Self:
        if self.work_id is not None and self.work_ids is not None:
            raise ValueError("work_id 与 work_ids 互斥")
        if self.view == "works" and (self.work_id is not None or self.work_ids is not None):
            raise ValueError("作品目录不接受作品过滤")
        if self.view in {"parts", "sections", "jobs"} and self.work_id is None:
            raise ValueError("分部、章节与任务目录须指定单作品")
        if self.work_ids is not None and self.view not in {"tags", "categories", "annotations"}:
            raise ValueError("当前视图不接受多作品")
        if self.annotation_id is not None and (
            self.view != "annotations" or self.work_id is None or self.cursor is not None
        ):
            raise ValueError("详情须指定 work_id 与 annotation_id，不接受游标")
        if self.view != "tags" and self.model_fields_set & {"namespace", "category"}:
            raise ValueError("namespace、category 只用于标签目录")
        if "query" in self.model_fields_set and (
            self.view not in {"tags", "annotations"} or self.annotation_id is not None
        ):
            raise ValueError("query 只用于标签或标注列表")
        if (
            self.view != "annotations" or self.annotation_id is not None
        ) and self.model_fields_set & {
            "kind",
            "tag_ids",
            "tag_match",
            "source_range",
            "status",
            "section_id",
        }:
            raise ValueError("标注过滤只用于标注列表")
        if self.part_id is not None and (
            self.view not in {"sections", "annotations"} or self.annotation_id is not None
        ):
            raise ValueError("part_id 只用于章节或标注列表")
        if (self.part_id or self.section_id or self.source_range) and self.work_id is None:
            raise ValueError("分部、章节和原文范围须指定单作品")
        if self.part_id is not None and self.section_id is not None:
            raise ValueError("分部和章节筛选互斥")
        if self.view == "categories" and self.cursor is not None:
            raise ValueError("固定分类不分页")
        return self


class LibraryPage(BaseModel):
    view: str
    work: WorkOut | None = None
    works: Page[WorkOut] | None = None
    parts: Page[PartOut] | None = None
    sections: Page[SectionOut] | None = None
    annotations: Page[BrowsedAnnotation] | None = None
    annotation: AnnotationOut | None = None
    locations: list[AnnotationLocation] | None = None
    tags: list[TagOut] | None = None
    tag_page: Page[BrowsedTag] | None = None
    categories: list[CategorySummary] | None = None
    jobs: Page[AnalysisJobSummary] | None = None


class SourceRead(RequestModel):
    """无端点读取整章；指定端点时按范围读取，可扩展上下文。"""

    work_id: UUID
    section_id: UUID
    start_paragraph_id: UUID | None = None
    end_paragraph_id: UUID | None = None
    start_ordinal: int | None = Field(default=None, ge=1)
    end_ordinal: int | None = Field(default=None, ge=1)
    before: int = Field(default=0, ge=0, le=100)
    after: int = Field(default=0, ge=0, le=100)
    limit: int = Field(default=60, ge=1, le=200)
    cursor: str | None = None
    format: ReadingFormat = "compact"

    @model_validator(mode="after")
    def endpoints(self) -> Self:
        if (self.start_paragraph_id is None) != (self.end_paragraph_id is None):
            raise ValueError("范围读取须同时提供起止段落")
        if (self.start_ordinal is None) != (self.end_ordinal is None):
            raise ValueError("段号范围须同时提供起止段号")
        if self.start_paragraph_id is not None and self.start_ordinal is not None:
            raise ValueError("UUID 与段号寻址互斥")
        if (
            self.start_paragraph_id is None
            and self.start_ordinal is None
            and (self.before or self.after)
        ):
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


class BatchIssue(BaseModel):
    path: list[str | int]
    code: str
    message: str
    current_version: int | None = None


class BatchValidation(BaseModel):
    valid: bool
    already_committed: bool = False
    issues: list[BatchIssue]
    checked_job_version: int | None = None


class PrepareRead(RequestModel):
    """显式保存已读范围和接续信息，不把 read 视为 processed。"""

    request_id: UUID
    work_id: UUID
    job_id: UUID
    expected_version: int = Field(
        ge=1, validation_alias=AliasChoices("expected_job_version", "expected_version")
    )
    source_range: RangeInput
    recovery: Recovery
