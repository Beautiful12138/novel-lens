"""安全的参数错误定位；只输出已声明字段、数组下标和固定错误类别。"""

from typing import Any

from fastapi.exceptions import RequestValidationError
from pydantic import AliasChoices, ValidationError

from novel_lens.contracts import RequestModel
from novel_lens.errors import ServiceError


def validation_issues(error: ValidationError | RequestValidationError) -> list[dict[str, Any]]:
    """未知键可能包含正文，不能原样作为路径；输入值和异常上下文始终丢弃。"""
    allowed = {"body", "query", "path"}
    pending = list(RequestModel.__subclasses__())
    seen: set[type[RequestModel]] = set()
    while pending:
        model = pending.pop()
        if model in seen:
            continue
        seen.add(model)
        pending.extend(model.__subclasses__())
        allowed.add(model.__name__)
        allowed.update(model.model_fields)
        for field in model.model_fields.values():
            if isinstance(field.validation_alias, str):
                allowed.add(field.validation_alias)
            elif isinstance(field.validation_alias, AliasChoices):
                allowed.update(
                    alias for alias in field.validation_alias.choices if isinstance(alias, str)
                )
    messages = {
        "missing": "缺少必填字段",
        "extra_forbidden": "不支持的字段",
        "uuid_parsing": "须为完整 UUID",
        "uuid_type": "须为完整 UUID",
        "int_type": "须为整数",
        "literal_error": "值不在允许范围内",
        "value_error": "字段或字段组合不满足约束",
    }
    return [
        {
            "path": [p if isinstance(p, int) or p in allowed else "?" for p in item["loc"]],
            "code": item["type"],
            "message": messages.get(item["type"], "字段类型、长度或范围不符合接口定义"),
        }
        for item in error.errors()
    ]


def input_error(error: ValidationError | RequestValidationError) -> ServiceError:
    return ServiceError(
        "INVALID_INPUT",
        "输入参数无效，请按 details.issues 定位修正",
        422,
        {"issues": validation_issues(error)},
    )
