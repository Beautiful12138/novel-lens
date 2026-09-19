"""标注、标签、实体与关系的共享契约；保留分析文本、有序范围及历史快照格式。"""

from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from novel_lens.contracts import ListRequest, RequestModel, SourceRange

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


class TagCreate(RequestModel):
    request_id: UUID
    namespace: Namespace
    name: Name
    description: Nonblank
    aliases: Aliases = Field(default_factory=list)


class TagGet(RequestModel):
    tag_id: UUID


class TagList(ListRequest):
    namespace: Namespace | None = None


class TagSearch(TagList):
    query: Nonblank


class TagOut(BaseModel):
    id: UUID
    namespace: str
    name: str
    full_name: str
    description: str
    aliases: list[str]
    created_at: datetime


class AnnotationCreate(RequestModel):
    request_id: UUID
    work_id: UUID
    source_ranges: list[SourceRange] = Field(
        min_length=1, description="同一作品的一处或多处原文范围，按输入顺序保存，单范围不能跨章节"
    )
    tag_ids: TagIds = Field(default_factory=list, description="复用已有标签 UUID，可为空")
    entity_ids: TagIds = Field(default_factory=list, description="同作品实体 UUID 集合")
    note: Nonblank | None = Field(
        default=None, description="可选写法说明，保留输入文本；无需套固定表单，无说明时传 null"
    )

    @model_validator(mode="after")
    def unique_ranges(self) -> Self:
        """拒绝同一标注中完全重复的引用，允许不同范围相交。"""
        values = [tuple(r.model_dump().values()) for r in self.source_ranges]
        if len(values) != len(set(values)):
            raise ValueError("请移除重复的原文范围")
        return self


class AnnotationUpdate(AnnotationCreate):
    annotation_id: UUID
    expected_version: int = Field(ge=1, description="此前读取的当前版本；冲突时重新读取再决定修改")
    tag_ids: TagIds = Field(description="完整替换标签集合，清空时传 []")
    note: Nonblank | None = Field(description="完整替换说明，清空时显式传 null")
    entity_ids: TagIds = Field(
        default_factory=list, description="显式提交时替换实体集合；省略时保留现有关联"
    )


class AnnotationGet(RequestModel):
    work_id: UUID
    annotation_id: UUID


class AnnotationList(ListRequest):
    work_id: UUID
    source_range: SourceRange | None = None
    tag_ids: TagIds = Field(default_factory=list)
    entity_ids: TagIds = Field(default_factory=list)


class LegacyAnnotationOut(BaseModel):
    """0002 已提交快照的原格式；拒绝新字段，避免新版结果退化为历史格式。"""

    model_config = ConfigDict(extra="forbid")
    id: UUID
    work_id: UUID
    source_ranges: list[SourceRange]
    tag_ids: list[UUID]
    note: str | None
    version: int
    created_at: datetime
    updated_at: datetime


class AnnotationOut(LegacyAnnotationOut):
    """当前标注必须返回实体集合；历史快照通过独立模型原样恢复。"""

    entity_ids: list[UUID]


class AnnotationSummary(BaseModel):
    """候选页不携带完整 Note 或所有引用；裁剪状态显式返回。"""

    id: UUID
    work_id: UUID
    version: int
    created_at: datetime
    updated_at: datetime
    first_source_range: SourceRange
    source_range_count: int
    tag_count: int
    entity_count: int
    note_preview: str | None
    note_truncated: bool


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


class AssetWriteOut(BaseModel):
    """提交时的结果快照；后续修改不改变该次请求的恢复结果。"""

    request_id: UUID
    operation: Literal[
        "tag_create",
        "annotation_create",
        "annotation_update",
        "entity_create",
        "entity_update",
        "relation_create",
        "relation_update",
        "relation_set_status",
    ]
    status: Literal["completed"] = "completed"
    replayed: bool = False
    result: TagOut | AnnotationOut | LegacyAnnotationOut | EntityOut | RelationSnapshot
