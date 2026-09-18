"""接口与业务服务共用的类型，未知请求字段不作为隐式开关。"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

Limit = Annotated[int, Field(ge=1, le=1000)]


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
    created_at: datetime
    section_count: int
    paragraph_count: int
    character_count: int
    source_sha256: str
    source_bytes: int


class ImportOut(BaseModel):
    request_id: UUID
    status: Literal["completed"] = "completed"
    replayed: bool
    work: WorkOut


class SectionOut(BaseModel):
    id: UUID
    work_id: UUID
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


class ReadOut(Page[ParagraphOut]):
    requested_range: SourceRange
    actual_range: SourceRange


class ContextRequest(RequestModel):
    section_id: UUID
    paragraph_id: UUID
    before: int = Field(default=0, ge=0, le=100)
    after: int = Field(default=0, ge=0, le=100)


class ContextOut(BaseModel):
    items: list[ParagraphOut]
    actual_range: SourceRange
    at_section_start: bool
    at_section_end: bool


class ListRequest(RequestModel):
    """所有列表入口共用的分页边界。"""

    limit: Limit = 100
    cursor: str | None = None
