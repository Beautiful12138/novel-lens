"""标注、标签、实体与关系的共享契约；保留分析文本、有序范围及历史快照。"""

from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from novel_lens.contracts import ListRequest, ReadingFormat, RequestModel, SourceRange
from novel_lens.tag_categories import Categories, CategoryId

MAX_ASSET_RESULT_BYTES = 1024 * 1024
Nonblank = Annotated[str, Field(pattern=r"^[^\x00]*[^\s\x00][^\x00]*$")]
Namespace = Annotated[
    str, Field(min_length=1, max_length=64, pattern=r"^[^\s/\x00](?:[^/\x00]*[^\s/\x00])?$")
]
Name = Annotated[
    str, Field(min_length=1, max_length=256, pattern=r"^[^\s/\x00](?:[^/\x00]*[^\s/\x00])?$")
]
Alias = Annotated[
    str, Field(min_length=1, max_length=256, pattern=r"^[^\s\x00](?:[^\x00]*[^\s\x00])?$")
]


def sorted_ids(values: list[UUID]) -> list[UUID]:
    """标签和实体关联是集合，使顺序不同的重试具有同一指纹。"""
    return sorted(set(values))


def sorted_aliases(values: list[str]) -> list[str]:
    return sorted(set(values))


TagIds = Annotated[list[UUID], AfterValidator(sorted_ids)]
Aliases = Annotated[list[Alias], AfterValidator(sorted_aliases)]
AnnotationStatus = Literal["active", "withdrawn"]
AnnotationKind = Literal["observation", "comparison"]


class AnnotationReference(RequestModel):
    """证据与连续阅读各自定位；包含关系由业务在真实段号上核验。"""

    evidence_range: SourceRange
    reading_range: SourceRange | None = None
    role_note: Nonblank


class AnnotationContent(RequestModel):
    """创建、完整修订与准备批次共用的认识内容，不接受旧引用格式。"""

    kind: AnnotationKind
    title: Alias
    scope_note: Nonblank
    note: Nonblank
    references: list[AnnotationReference] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_evidence(self) -> Self:
        keys = [ref.evidence_range.model_dump_json() for ref in self.references]
        if len(keys) != len(set(keys)):
            raise ValueError("请移除重复的证据范围")
        return self


class TagCreate(RequestModel):
    request_id: UUID
    namespace: Namespace
    name: Name
    description: Nonblank
    aliases: Aliases = Field(default_factory=list)
    categories: Categories = Field(default_factory=list)


class TagGet(RequestModel):
    tag_id: UUID


class TagUpdate(TagGet):
    """修订共享标签的名称、定义与别名，命名空间及引用 ID 保持不变。"""

    request_id: UUID
    expected_version: int = Field(ge=1)
    name: Name
    description: Nonblank
    aliases: Aliases
    categories: Categories | None = Field(
        default=None, description="省略保留；提供时完整替换，[] 清空"
    )


class TagList(ListRequest):
    namespace: Namespace | None = None


class TagSearch(TagList):
    query: Nonblank


class TagOut(BaseModel):
    """共享标签的当前完整数据或提交快照。"""

    model_config = ConfigDict(extra="forbid")
    id: UUID
    namespace: str
    name: str
    full_name: str
    description: str
    aliases: list[str]
    categories: Categories = Field(default_factory=list)
    created_at: datetime

    version: int
    updated_at: datetime


class AnnotationCreate(AnnotationContent):
    request_id: UUID
    work_id: UUID
    tag_ids: TagIds = Field(default_factory=list, description="复用已有标签 UUID，可为空")
    entity_ids: TagIds = Field(default_factory=list, description="同作品实体 UUID 集合")


class AnnotationUpdate(AnnotationCreate):
    annotation_id: UUID
    expected_version: int = Field(ge=1, description="此前读取的当前版本；冲突时重新读取再决定修改")
    tag_ids: TagIds = Field(description="完整替换标签集合，清空时传 []")
    entity_ids: TagIds = Field(description="完整替换实体集合，清空时传 []")


class AnnotationGet(RequestModel):
    work_id: UUID
    annotation_id: UUID


class AnnotationSetStatus(AnnotationGet):
    """撤回或恢复整条标注；与内容修订共用版本，保留全部证据。"""

    request_id: UUID
    expected_version: int = Field(ge=1)
    status: AnnotationStatus


class AnnotationRead(AnnotationGet):
    """阅读入口单独声明格式，避免将投影选项带入状态写入与请求指纹。"""

    format: ReadingFormat = "compact"


class AnnotationList(ListRequest):
    kind: AnnotationKind | None = None
    work_id: UUID
    source_range: SourceRange | None = None
    tag_ids: TagIds = Field(default_factory=list)
    entity_ids: TagIds = Field(default_factory=list)
    status: AnnotationStatus | None = Field(default="active", description="null 查询全部状态")
    format: ReadingFormat = "compact"


class AnnotationOut(AnnotationContent):
    """含实体关联和撤回状态的完整标注。"""

    model_config = ConfigDict(extra="forbid")
    id: UUID
    work_id: UUID
    tag_ids: list[UUID]
    version: int
    created_at: datetime
    updated_at: datetime

    entity_ids: list[UUID]

    status: AnnotationStatus


class TagIdentity(BaseModel):
    """列表选择候选所需的标签身份，不在每条摘要重复完整定义。"""

    id: UUID
    full_name: str


class AnnotationSummary(BaseModel):
    """候选页不携带完整 Note 或所有引用；裁剪状态显式返回。"""

    id: UUID
    kind: AnnotationKind
    title: str
    scope_note: str
    work_id: UUID
    version: int
    status: AnnotationStatus
    created_at: datetime
    updated_at: datetime
    first_source_range: SourceRange
    source_range_count: int
    tag_count: int
    entity_count: int
    note_preview: str | None
    note_truncated: bool
    tags: list[TagIdentity]


class AnnotationBrowse(ListRequest):
    """用户资产目录：可跨可见作品，章节筛选始终绑定明确作品。"""

    kind: AnnotationKind | None = None
    work_id: UUID | None = None
    work_ids: TagIds | None = Field(default=None, min_length=1, max_length=20)
    part_id: UUID | None = None
    section_id: UUID | None = None
    source_range: SourceRange | None = None
    tag_ids: TagIds = Field(default_factory=list)
    tag_match: Literal["any", "all"] = "any"
    query: Nonblank | None = Field(default=None, max_length=300)
    status: AnnotationStatus | None = "active"

    @model_validator(mode="after")
    def chapter_scope(self) -> Self:
        if self.work_id is not None and self.work_ids is not None:
            raise ValueError("work_id 与 work_ids 互斥")
        if (self.section_id or self.part_id or self.source_range) and self.work_id is None:
            raise ValueError("分部、章节和原文范围筛选必须同时指定单作品")
        if self.section_id is not None and self.part_id is not None:
            raise ValueError("分部与章节筛选互斥")
        return self


class TagBrowse(TagList):
    """共享词表或作品内有效使用的标签；计数始终限定可见作品。"""

    query: Nonblank | None = None
    work_id: UUID | None = None
    work_ids: TagIds | None = Field(default=None, min_length=1, max_length=20)
    category: CategoryId | Literal["unclassified"] | None = None

    @model_validator(mode="after")
    def selected_scope(self) -> Self:
        if self.work_id is not None and self.work_ids is not None:
            raise ValueError("work_id 与 work_ids 互斥")
        return self


class BrowsedTag(TagOut):
    annotation_count: int


class CategorySummary(BaseModel):
    id: str
    name: str
    tag_count: int
    annotation_count: int


class BrowsedAnnotation(AnnotationSummary):
    """列表补充作品及第一处引用的目录名称，不代替完整引用详情。"""

    work_name: str
    part_name: str
    section_title: str


class AnnotationLocation(SourceRange):
    """原文的稳定范围和人类可读坐标；段号属于各自章节。"""

    part_name: str
    section_title: str
    start_ordinal: int
    end_ordinal: int
    reading_start_ordinal: int
    reading_end_ordinal: int


class BrowsedAnnotationDetail(BaseModel):
    """同一读取快照内的标注内容、标签定义和有序引用目录。"""

    annotation: AnnotationOut
    tags: list[TagOut]
    locations: list[AnnotationLocation]


class ParagraphAnnotationCount(BaseModel):
    paragraph_id: UUID
    ordinal: int
    annotation_count: int


class AnnotationCoverage(BaseModel):
    """有限正文页的完整有效标注计数；与标注候选分页互相独立。"""

    work_id: UUID
    section_id: UUID
    items: list[ParagraphAnnotationCount]


class AssetWriteGet(RequestModel):
    request_id: UUID


EntityType = Literal["character", "location", "item", "organization", "concept"]
RelationStatus = Literal["active", "withdrawn"]
Role = Annotated[
    str, Field(min_length=1, max_length=64, pattern=r"^[^\s\x00](?:[^\x00]*[^\s\x00])?$")
]


class EntityCreate(RequestModel):
    """创建作品内身份；名称和别名不构成身份唯一约束。"""

    request_id: UUID
    work_id: UUID
    type: EntityType
    canonical_name: Alias
    aliases: Aliases = Field(default_factory=list)
    note: Nonblank | None = None


class EntityUpdate(EntityCreate):
    entity_id: UUID
    expected_version: int = Field(ge=1)
    aliases: Aliases
    note: Nonblank | None


class EntityGet(RequestModel):
    work_id: UUID
    entity_id: UUID


class EntityList(ListRequest):
    work_id: UUID
    type: EntityType | None = None


class EntitySearch(EntityList):
    query: Nonblank


class EntityOut(BaseModel):
    id: UUID
    work_id: UUID
    type: EntityType
    canonical_name: str
    aliases: list[str]
    note: str | None
    version: int
    created_at: datetime
    updated_at: datetime


class RelationNode(RequestModel):
    source_range: SourceRange
    role: Role | None = None


class RelationNodeOut(RelationNode):
    ordinal: int


class RelationCreate(RequestModel):
    """有序证据节点和说明；节点顺序不等于时间或因果顺序。"""

    request_id: UUID
    work_id: UUID
    title: Alias
    relation_type: Role
    nodes: list[RelationNode] = Field(min_length=2)
    note: Nonblank
    tag_ids: TagIds = Field(default_factory=list)
    entity_ids: TagIds = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_nodes(self) -> Self:
        """范围可以相交，但角色不同不能使完全相同的原文成为两个节点。"""
        values = [tuple(n.source_range.model_dump().values()) for n in self.nodes]
        if len(values) != len(set(values)):
            raise ValueError("请移除重复的原文节点")
        return self


class RelationUpdate(RelationCreate):
    relation_id: UUID
    expected_version: int = Field(ge=1)
    tag_ids: TagIds
    entity_ids: TagIds


class RelationGet(RequestModel):
    work_id: UUID
    relation_id: UUID


class RelationSetStatus(RelationGet):
    """设置目标状态，内容不变；同键重放不再次增加版本。"""

    request_id: UUID
    expected_version: int = Field(ge=1)
    status: RelationStatus


class RelationSearch(ListRequest):
    work_id: UUID
    query: Nonblank | None = None
    relation_type: Role | None = None
    source_range: SourceRange | None = None
    tag_ids: TagIds = Field(default_factory=list)
    entity_ids: TagIds = Field(default_factory=list)
    status: RelationStatus | None = Field(default="active", description="null 查询全部状态")


class RelationExpand(ListRequest, RelationGet):
    expected_version: int = Field(ge=1)


class RelationOut(BaseModel):
    """当前关系详情，不包含节点列表或正文。"""

    id: UUID
    work_id: UUID
    title: str
    relation_type: str
    note: str
    status: RelationStatus
    tag_ids: list[UUID]
    entity_ids: list[UUID]
    node_count: int
    version: int
    created_at: datetime
    updated_at: datetime


class RelationSnapshot(RelationOut):
    """提交快照包含当时全部节点，恢复时不查询当前关系。"""

    nodes: list[RelationNodeOut]


class RelationSummary(BaseModel):
    id: UUID
    work_id: UUID
    title: str
    relation_type: str
    status: RelationStatus
    version: int
    created_at: datetime
    updated_at: datetime
    first_source_range: SourceRange
    node_count: int
    tag_count: int
    entity_count: int
    note_preview: str
    note_truncated: bool


class RelationNodePage(BaseModel):
    work_id: UUID
    relation_id: UUID
    version: int
    status: RelationStatus
    items: list[RelationNodeOut]
    next_cursor: str | None


# 任务与资产共用写入回执，类型集中定义以解析本模型提交的快照。
class AnalysisTarget(RequestModel):
    kind: Literal["whole_work", "part", "ranges"]
    part_id: UUID | None = None
    source_ranges: list[SourceRange] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_target(self) -> Self:
        if (self.kind == "ranges") != bool(self.source_ranges):
            raise ValueError("ranges 须提供范围，whole_work 不接受范围")
        if (self.kind == "part") != (self.part_id is not None):
            raise ValueError("仅 part 目标须提供 part_id")
        return self


class RecoveryFact(RequestModel):
    note: Nonblank
    source_ranges: list[SourceRange] = Field(min_length=1)


class RecoveryQuestion(RequestModel):
    observation: Nonblank
    question: Nonblank
    source_ranges: list[SourceRange] = Field(min_length=1)


class Recovery(RequestModel):
    """事实、未决问题与操作接续分开保存；坐标是证据，文本不替代进度。"""

    facts: list[RecoveryFact] = Field(default_factory=list)
    open_questions: list[RecoveryQuestion] = Field(default_factory=list)
    next_action: Nonblank
    next_range: SourceRange | None = None


JobStatus = Literal["running", "paused", "completed"]
CoverageStatus = Literal["unprocessed", "read", "processed", "needs_revisit"]


class CoverageCounts(BaseModel):
    unprocessed: int = 0
    read: int = 0
    processed: int = 0
    needs_revisit: int = 0


class Completion(BaseModel):
    """完成时的校准声明和导航版本，后续资产修订不回写历史依据。"""

    calibration_note: str
    limitations: str | None


class AnalysisJobOut(BaseModel):
    """完整任务详情；全书目标不展开章节范围，覆盖通过分页工具读取。"""

    id: UUID
    work_id: UUID
    title: str
    goal: str
    target: AnalysisTarget
    status: JobStatus
    recovery: Recovery
    completion: Completion | None
    target_paragraph_count: int
    counts: CoverageCounts
    version: int
    created_at: datetime
    updated_at: datetime


class AnalysisJobSummary(BaseModel):
    id: UUID
    work_id: UUID
    title: str
    goal_preview: str
    goal_truncated: bool
    status: JobStatus
    target_paragraph_count: int
    counts: CoverageCounts
    version: int
    created_at: datetime
    updated_at: datetime


class AnalysisJobCreate(RequestModel):
    request_id: UUID
    work_id: UUID
    title: Alias
    goal: Nonblank
    target: AnalysisTarget
    recovery: Recovery


class AnalysisJobGet(RequestModel):
    work_id: UUID
    job_id: UUID


class AnalysisJobList(ListRequest):
    work_id: UUID
    status: JobStatus | None = None


class AnalysisJobModify(AnalysisJobGet):
    request_id: UUID
    expected_version: int = Field(ge=1)


class AnalysisJobUpdate(AnalysisJobModify):
    status: Literal["running", "paused"]
    recovery: Recovery
    reopen_reason: Nonblank | None = None


class AnalysisJobComplete(AnalysisJobModify):
    recovery: Recovery
    calibration_note: Nonblank
    limitations: Nonblank | None


class CoverageMark(AnalysisJobModify):
    source_range: SourceRange
    status: Literal["read", "needs_revisit"]
    reason: Nonblank | None = None

    @model_validator(mode="after")
    def reason_matches(self) -> Self:
        if (self.status == "needs_revisit") != (self.reason is not None):
            raise ValueError("回看须说明原因；标记 read 不接受回看原因")
        return self


class CoverageGet(ListRequest, AnalysisJobGet):
    section_id: UUID | None = None
    status: CoverageStatus | None = None
    remaining_only: bool = False


class CoverageItem(BaseModel):
    source_range: SourceRange
    status: CoverageStatus
    reason: str | None


class CoveragePage(BaseModel):
    work_id: UUID
    job_id: UUID
    version: int
    items: list[CoverageItem]
    next_cursor: str | None


class TagCreateWrite(RequestModel):
    operation: Literal["tag_create"]
    input: TagCreate


class TagUpdateWrite(RequestModel):
    operation: Literal["tag_update"]
    input: TagUpdate


class AnnotationSetStatusWrite(RequestModel):
    operation: Literal["annotation_set_status"]
    input: AnnotationSetStatus


class AnnotationCreateWrite(RequestModel):
    operation: Literal["annotation_create"]
    input: AnnotationCreate


class AnnotationUpdateWrite(RequestModel):
    operation: Literal["annotation_update"]
    input: AnnotationUpdate


class EntityCreateWrite(RequestModel):
    operation: Literal["entity_create"]
    input: EntityCreate


class EntityUpdateWrite(RequestModel):
    operation: Literal["entity_update"]
    input: EntityUpdate


class RelationCreateWrite(RequestModel):
    operation: Literal["relation_create"]
    input: RelationCreate


class RelationUpdateWrite(RequestModel):
    operation: Literal["relation_update"]
    input: RelationUpdate


class RelationSetStatusWrite(RequestModel):
    operation: Literal["relation_set_status"]
    input: RelationSetStatus


CheckpointWrite = Annotated[
    TagCreateWrite
    | TagUpdateWrite
    | AnnotationSetStatusWrite
    | AnnotationCreateWrite
    | AnnotationUpdateWrite
    | EntityCreateWrite
    | EntityUpdateWrite
    | RelationCreateWrite
    | RelationUpdateWrite
    | RelationSetStatusWrite,
    Field(discriminator="operation"),
]


class AnalysisCheckpoint(AnalysisJobModify):
    """本批资产操作、接续信息与进度一起提交；不支持批内临时 ID。"""

    source_range: SourceRange
    writes: list[CheckpointWrite]
    recovery: Recovery
    outcome_note: Nonblank | None = None

    @model_validator(mode="after")
    def valid_batch(self) -> Self:
        keys = [self.request_id, *(w.input.request_id for w in self.writes)]
        if len(keys) != len(set(keys)):
            raise ValueError("外层与子请求键必须互不重复")
        if not self.writes and self.outcome_note is None:
            raise ValueError("无资产写入时须说明处理结论")
        return self


class CheckpointReceipt(BaseModel):
    operation: str
    request_id: UUID


class AnalysisCheckpointOut(BaseModel):
    work_id: UUID
    job_id: UUID
    version: int
    source_range: SourceRange
    writes: list[CheckpointReceipt]
    outcome_note: str | None


class AssetWriteOut(BaseModel):
    """提交时的结果快照；后续修改不改变该次请求的恢复结果。"""

    request_id: UUID
    operation: Literal[
        "tag_create",
        "tag_update",
        "annotation_set_status",
        "annotation_create",
        "annotation_update",
        "entity_create",
        "entity_update",
        "relation_create",
        "relation_update",
        "relation_set_status",
        "analysis_job_create",
        "analysis_job_update",
        "analysis_job_complete",
        "coverage_mark",
        "analysis_checkpoint",
    ]
    status: Literal["completed"] = "completed"
    replayed: bool = False
    result: (
        TagOut
        | AnnotationOut
        | EntityOut
        | RelationSnapshot
        | AnalysisJobOut
        | AnalysisCheckpointOut
    )
