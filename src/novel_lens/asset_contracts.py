"""标注与标签的共享契约；规范化集合，不改写分析文本或原文范围顺序。"""

from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import AfterValidator, BaseModel, Field, model_validator

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
    """标签关联是集合，使顺序不同的重试具有同一指纹。"""
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


class AnnotationGet(RequestModel):
    work_id: UUID
    annotation_id: UUID


class AnnotationList(ListRequest):
    work_id: UUID
    source_range: SourceRange | None = None
    tag_ids: TagIds = Field(default_factory=list)


class AnnotationOut(BaseModel):
    id: UUID
    work_id: UUID
    source_ranges: list[SourceRange]
    tag_ids: list[UUID]
    note: str | None
    version: int
    created_at: datetime
    updated_at: datetime


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
    note_preview: str | None
    note_truncated: bool


class AssetWriteGet(RequestModel):
    request_id: UUID


class AssetWriteOut(BaseModel):
    """提交时的结果快照；后续修改不改变该次请求的恢复结果。"""

    request_id: UUID
    operation: Literal["tag_create", "annotation_create", "annotation_update"]
    status: Literal["completed"] = "completed"
    replayed: bool = False
    result: TagOut | AnnotationOut
