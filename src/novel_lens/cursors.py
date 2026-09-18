"""绑定查询范围的轻量游标；实际作品归属仍由数据库查询校验。"""

import base64
import binascii
import json

from novel_lens.errors import ServiceError


def encode_cursor(scope: str, after: str) -> str:
    payload = json.dumps([scope, after], ensure_ascii=True, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(payload).decode()


def decode_cursor(cursor: str | None, scope: str) -> str | None:
    """校验格式和范围；游标不是授权令牌，不信任其中的对象归属。"""
    if cursor is None:
        return None
    try:
        if len(cursor) > 2048:
            raise ValueError
        payload = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
        if (
            not isinstance(payload, list)
            or len(payload) != 2
            or payload[0] != scope
            or not isinstance(payload[1], str)
        ):
            raise ValueError
        return str(payload[1])
    except (ValueError, UnicodeError, binascii.Error):
        raise ServiceError("INVALID_CURSOR", "游标无效或与读取范围不匹配") from None


def ordinal_cursor(cursor: str | None, scope: str, maximum: int) -> int:
    value = decode_cursor(cursor, scope)
    if value is None:
        return 0
    try:
        ordinal = int(value)
        if not 1 <= ordinal < maximum:
            raise ValueError
        return ordinal
    except ValueError:
        raise ServiceError("INVALID_CURSOR", "游标不在可继续读取的范围内") from None
