"""接口与业务服务共用的类型，未知请求字段不作为隐式开关。"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

Limit = Annotated[int, Field(ge=1, le=1000)]
ReadingFormat = Literal["full", "compact"]
WorkVisibility = Literal["visible", "hidden"]
WorkFilter = Literal["visible", "hidden", "all"]


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class Position(BaseModel):
    """上传文件的零基字节半开区间及一基行号，偏移包含 BOM 与换行宽度。"""

    start_byte: int
    end_byte: int
    line: int


class Issue(BaseModel):
    code: str
    message: str
    source_position: Position | None = None


class ValidationReport(BaseModel):
    """仅报告首个可靠定位的问题；valid 不证明上传前的整理正确。"""

    status: Literal["valid", "invalid"]
    source_sha256: str
    rule_version: str
    issue_count: int
    issues: list[Issue]
    next_cursor: str | None = None


class WorkOut(BaseModel):
    """作品元数据；character_count 只计算正文 Unicode 码点，包含正文空白。"""

    id: UUID
    name: str
    version: int
    part_count: int
    visibility: WorkVisibility = "visible"
    created_at: datetime
    section_count: int
    paragraph_count: int
    character_count: int
    source_sha256: str
    source_bytes: int


class PartOut(BaseModel):
    """分部的来源摘要；名称可修改，原文与追加顺序不可修改。"""

    id: UUID
    work_id: UUID
    name: str
    ordinal: int
    version: int
    created_at: datetime
    section_count: int
    paragraph_count: int
    character_count: int
    source_sha256: str
    source_bytes: int


class WorkCreate(RequestModel):
    request_id: UUID
    name: str = Field(min_length=1, max_length=256, pattern=r"^[^\s\x00](?:[^\x00]*[^\s\x00])?$")


class WorkUpdate(WorkCreate):
    work_id: UUID
    expected_version: int = Field(ge=1, strict=True)


class PartUpdate(WorkUpdate):
    part_id: UUID


class CatalogWrite(BaseModel):
    """目录写入回执；result 是提交时快照，当前信息另行 get。"""

    request_id: UUID
    replayed: bool = False
    result: WorkOut | PartOut


class WorkVisibilityUpdate(RequestModel):
    """绝对状态设置，可安全重复；不改变作品内容。"""

    visibility: WorkVisibility


class ImportOut(BaseModel):
    request_id: UUID
    status: Literal["completed"] = "completed"
    replayed: bool
    work: WorkOut
    part: PartOut


class SectionOut(BaseModel):
    id: UUID
    work_id: UUID
    part_id: UUID
    part_name: str
    ordinal: int
    title: str
    paragraph_count: int


class ParagraphOut(BaseModel):
    """不可变自然段；text 等于 source_position 对应字节的 UTF-8 解码。"""

    id: UUID
    section_id: UUID
    ordinal: int
    text: str
    source_position: Position


class Page[T](BaseModel):
    items: list[T]
    next_cursor: str | None


class SourceRange(RequestModel):
    """同作品、同 Section 内的含两端段落范围，归属和顺序由业务服务校验。"""

    work_id: UUID
    section_id: UUID
    start_paragraph_id: UUID
    end_paragraph_id: UUID


class ReadRequest(RequestModel):
    source_range: SourceRange
    limit: Limit = 100
    cursor: str | None = None
    format: ReadingFormat = "compact"


class ReadOut(Page[ParagraphOut]):
    requested_range: SourceRange
    actual_range: SourceRange


class ContextRequest(RequestModel):
    section_id: UUID
    paragraph_id: UUID
    before: int = Field(default=0, ge=0, le=100)
    after: int = Field(default=0, ge=0, le=100)
    format: ReadingFormat = "compact"


class ContextOut(BaseModel):
    items: list[ParagraphOut]
    actual_range: SourceRange
    at_section_start: bool
    at_section_end: bool


class ParagraphSpan(BaseModel):
    """精简输出中的含两端范围，作品和章节取自同一响应顶层。"""

    start_paragraph_id: UUID
    end_paragraph_id: UUID


class CompactParagraphOut(BaseModel):
    """阅读投影保留完整正文与稳定定位，省略字节位置和重复归属。"""

    id: UUID
    ordinal: int
    text: str


class CompactParagraphPage(Page[CompactParagraphOut]):
    """归属集中在顶层；空章节返回空 items 和 null 实际范围。"""

    work_id: UUID
    section_id: UUID
    actual_range: ParagraphSpan | None


class CompactReadOut(CompactParagraphPage):
    """请求范围与本页范围分离，端点需结合顶层归属用于后续读取。"""

    requested_range: ParagraphSpan
    actual_range: ParagraphSpan


class CompactContextOut(BaseModel):
    """精简上下文保留章节边界，边界标志不表示文学场景已完整。"""

    work_id: UUID
    section_id: UUID
    items: list[CompactParagraphOut]
    actual_range: ParagraphSpan
    at_section_start: bool
    at_section_end: bool


class ListRequest(RequestModel):
    """所有列表入口共用的分页边界。"""

    limit: Limit = 100
    cursor: str | None = None
