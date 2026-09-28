"""为统一召回补充有界字面词，并在真实候选范围内批量定位预览。"""

import json
import re
from functools import lru_cache
from tempfile import TemporaryDirectory
from typing import Any

import jieba  # type: ignore[import-untyped]
from sqlalchemy import Connection, text

# 只过滤查询套语，不维护情绪或题材同义词表。完整问句仍交给语义通道。
_FILLER = frozenset(
    "人物 场景 片段 原文 小说 作品 描写 描述 表现 展现 查找 搜索 寻找 查询 "
    "关于 有关 一个 一种 一些 如何 怎样 怎么 时候 通过 进行 出现 之间 以及 "
    "可以 需要 希望 帮忙 找到 请问 相关 内容 情节 对话".split()
)
_NEGATION = frozenset("不 没 没有 未 并非 不是 不要 不能 不会 不曾 未曾 无法 别 勿 莫 无".split())


@lru_cache(maxsize=1)
def _tokenizer() -> Any:
    """每进程初始化一次；分词缓存只写独立临时目录，不修改共享缓存。"""
    tokenizer = jieba.Tokenizer()
    with TemporaryDirectory(prefix="novel-lens-tokenizer-") as directory:
        tokenizer.tmp_dir = directory
        tokenizer.initialize()
    return tokenizer


def keyword_terms(query: str, explicit: list[str]) -> list[str]:
    """保留调用方用词，最多补 12 个词；否定后的同分句不拆成肯定关键词。

    分词仅提供辅助候选，不重写完整查询，也不承担复杂否定和文学判断。
    """
    derived: list[str] = []
    for clause in re.split(r"[，。！？；、,!?;\n]", query):
        for token in _tokenizer().lcut(clause, HMM=False):
            token = token.strip()
            if token in _NEGATION:
                break
            if (
                2 <= len(token) <= 32
                and token not in _FILLER
                and any(c.isalpha() for c in token)
                and token not in derived
            ):
                derived.append(token)
            if len(derived) == 12:
                break
        if len(derived) == 12:
            break
    return list(dict.fromkeys([query, *explicit, *derived]))


def previews(
    conn: Connection, candidates: list[dict[str, Any]], terms: list[str]
) -> dict[int, tuple[str, bool]]:
    """一次 SQL 为全部候选选取命中段；只返回连续原文片段，不读取整书到应用。

    PGroonga 定位沿用索引的规范化规则。找不到命中时用首段；多段范围、
    段内前后省略均标记截断。调用方须在同一检索快照内提供已经验证的范围。
    """
    if not candidates:
        return {}
    ranges = [
        {
            "position": n,
            "section": str(row["section_id"]),
            "first": row["start_ordinal"],
            "last": row["end_ordinal"],
        }
        for n, row in enumerate(candidates)
    ]
    rows = conn.execute(
        text("""
        WITH ranges AS (
          SELECT * FROM jsonb_to_recordset(CAST(:ranges AS jsonb))
            AS r(position integer,section uuid,first integer,last integer)
        ), located AS MATERIALIZED (
          SELECT r.position,r.first,r.last,p.ordinal,p.text,
            (pgroonga_match_positions_character(
              p.text,CAST(:terms AS text[]),'ix_paragraphs_text_search'))[1][1] AS hit
          FROM ranges r JOIN paragraphs p ON p.section_id=r.section
            AND p.ordinal BETWEEN r.first AND r.last
        ), chosen AS (
          SELECT DISTINCT ON (position) *,greatest(coalesce(hit,0)-40,0) AS offset_chars
          FROM located ORDER BY position,(hit IS NOT NULL) DESC,ordinal
        )
        SELECT position,substr(text,offset_chars+1,200) AS excerpt,
          first<>last OR offset_chars>0 OR length(text)>offset_chars+200 AS truncated
        FROM chosen
        """),
        {"ranges": json.dumps(ranges), "terms": terms},
    )
    return {row.position: (row.excerpt, row.truncated) for row in rows}
