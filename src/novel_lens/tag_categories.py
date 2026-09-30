"""固定的写法浏览目录；分类是查找入口，不是分析维度或文学质量评分。"""

from typing import Annotated, Literal

from pydantic import AfterValidator

CategoryId = Literal["content", "perspective", "interaction", "language", "structure", "emotion"]
CATEGORY_NAMES: dict[str, str] = {
    "content": "内容选择与展开",
    "perspective": "视角与信息",
    "interaction": "人物与互动",
    "language": "语言与声音",
    "structure": "节奏与结构",
    "emotion": "情绪与氛围",
}


def normalize_categories(values: list[CategoryId]) -> list[CategoryId]:
    """分类是集合，规范化顺序使重试指纹与输入顺序无关。"""
    return sorted(set(values))


Categories = Annotated[list[CategoryId], AfterValidator(normalize_categories)]
