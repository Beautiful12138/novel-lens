"""业务错误只携带可公开的原因，禁止附加正文、连接串或数据库异常原文。"""

from typing import Any

from sqlalchemy.exc import OperationalError, SQLAlchemyError


class ServiceError(Exception):
    """供 HTTP 与后续 MCP 复用的稳定错误契约。"""

    def __init__(self, code: str, message: str, status: int = 422, details: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details

    def payload(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}


def database_error(exc: SQLAlchemyError) -> ServiceError:
    """REST 与 MCP 共用的异常分类；驱动异常可能含凭据或正文，不序列化它。"""
    if isinstance(exc, OperationalError):
        return ServiceError("DATABASE_UNAVAILABLE", "数据库暂时不可用", 503)
    return ServiceError("DATABASE_ERROR", "数据库操作失败", 500)
