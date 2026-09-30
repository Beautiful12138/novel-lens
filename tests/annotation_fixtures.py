"""为资产行为测试提供明确的新标注内容，正文范围由各用例给定。"""

from typing import Any


def annotation_fields(ranges: list[Any]) -> dict[str, Any]:
    return {
        "kind": "observation",
        "title": "样例写法观察",
        "scope_note": "限于本次给定的原文范围",
        "references": [
            {"evidence_range": ref, "role_note": "支撑这项观察的原文"} for ref in ranges
        ],
    }
