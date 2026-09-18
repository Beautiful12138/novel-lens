"""HTTP 传输校验；文学结构确认由调用方完成，业务校验由服务完成。"""

from typing import Any
from uuid import UUID

from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from novel_lens.errors import ServiceError


def upload_schema(*, importing: bool) -> dict[str, Any]:
    """手工校验 multipart 字段时仍向 OpenAPI 调用方提供完整上传契约。"""
    properties: dict[str, Any] = {"file": {"type": "string", "format": "binary"}}
    if importing:
        properties["request_id"] = {"type": "string", "format": "uuid"}
    return {
        "requestBody": {
            "required": True,
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "properties": properties,
                        "required": list(properties),
                        "additionalProperties": False,
                    }
                }
            },
        }
    }


class RequestSizeLimit:
    """逐块计数，即使没有 Content-Length 也限流；解析器负责关闭上传临时文件。"""

    def __init__(self, app: ASGIApp, maximum: int) -> None:
        self.app, self.maximum = app, maximum

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if scope["path"] == "/mcp" or scope["path"].startswith("/mcp/"):
            # MCP 的 SDK 接收器按相同配置逐块限额并返回 HTTP 413，避免把此处
            # 的 REST HTTPException 捕获成协议内部错误；由集成测试验证该边界。
            await self.app(scope, receive, send)
            return
        count = 0

        async def bounded_receive() -> Message:
            nonlocal count
            message = await receive()
            if message["type"] == "http.request":
                count += len(message.get("body", b""))
                if count > self.maximum:
                    raise HTTPException(413, "请求体超过允许大小")
            return message

        await self.app(scope, bounded_receive, send)


async def upload(request: Request, maximum: int, *, importing: bool) -> tuple[bytes, UUID | None]:
    """只读取契约字段，不使用上传文件名作为路径；成功和失败均关闭临时文件。"""
    if (
        request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        != "multipart/form-data"
    ):
        raise ServiceError("INVALID_INPUT", "需要 multipart/form-data 上传")
    try:
        async with request.form(
            max_files=1, max_fields=1 if importing else 0, max_part_size=1024
        ) as form:
            expected = {"file", "request_id"} if importing else {"file"}
            keys = [key for key, _ in form.multi_items()]
            if set(keys) != expected or len(keys) != len(expected):
                raise ServiceError("INVALID_INPUT", "上传字段缺失、重复或不支持")
            file = form["file"]
            if not isinstance(file, UploadFile):
                raise ServiceError("INVALID_INPUT", "file 必须是上传文件")
            request_id = None
            if importing:
                value = form["request_id"]
                if not isinstance(value, str):
                    raise ServiceError("INVALID_INPUT", "request_id 必须为 UUID 文本")
                try:
                    request_id = UUID(value)
                except ValueError:
                    raise ServiceError("INVALID_INPUT", "request_id 必须为 UUID 文本") from None
            if file.size is not None and file.size > maximum:
                raise ServiceError("FILE_TOO_LARGE", "文件超过允许大小", 413)
            data = await file.read(maximum + 1)
            if len(data) > maximum:
                raise ServiceError("FILE_TOO_LARGE", "文件超过允许大小", 413)
            return data, request_id
    except ValueError:
        # multipart 解析异常可能包含原始头部，不能作为响应或日志直接输出。
        raise ServiceError("INVALID_INPUT", "multipart 内容无效") from None
