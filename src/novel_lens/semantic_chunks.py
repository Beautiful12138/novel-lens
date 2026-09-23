"""逐块规划完整自然段窗口；调用方提供短事务读取，不跨网络调用持有事务。"""

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from novel_lens.embedding import (
    MAX_PARAGRAPH_BYTES,
    MAX_TOKENS,
    OVERLAP_TOKENS,
    TARGET_TOKENS,
    EmbeddingClient,
)


@dataclass
class Chunk:
    """仅在本批内持有正文；持久层只保存摘要、范围、token 数和向量。"""

    paragraphs: list[dict[str, Any]]
    tokens: list[int] | None
    blocked_reason: str | None = None
    annotation_id: UUID | None = None
    evidence_id: str | None = None
    range_ordinal: int | None = None

    @property
    def body(self) -> str:
        return "\n".join(row["text"] for row in self.paragraphs)

    @property
    def text_sha256(self) -> str:
        if self.blocked_reason == "TOKENIZATION_INPUT_TOO_LARGE":
            return str(self.paragraphs[0]["text_sha256"])
        return hashlib.sha256(self.body.encode()).hexdigest()

    @property
    def bytes(self) -> int:
        return sum(int(row["bytes"]) for row in self.paragraphs) + len(self.paragraphs) - 1

    @property
    def cursor(self) -> dict[str, int | None]:
        last = self.paragraphs[-1]
        return {
            "section": last["section_ordinal"],
            "paragraph": last["ordinal"],
            "overlap_start": None,
        }


def next_chunk(
    cursor: dict[str, Any],
    read_next: Callable[[dict[str, Any]], dict[str, Any] | None],
    read_overlap: Callable[[dict[str, Any]], list[dict[str, Any]]],
    model: EmbeddingClient,
) -> tuple[Chunk | None, dict[str, Any]]:
    """贪心增加新段；重叠先让位于新段，长段与缺口清空重叠，保证严格前进。"""
    first = read_next(cursor)
    if first is None:
        return None, cursor
    if first["bytes"] > MAX_PARAGRAPH_BYTES:
        chunk = Chunk([first], None, "TOKENIZATION_INPUT_TOO_LARGE")
        return chunk, chunk.cursor
    single = model.tokenize(first["text"])
    if len(single) > TARGET_TOKENS:
        reason = "INPUT_TOO_LONG" if len(single) > MAX_TOKENS else None
        chunk = Chunk([first], single, reason)
        return chunk, chunk.cursor
    rows = []
    if cursor.get("overlap_start") is not None and first["section_ordinal"] == cursor["section"]:
        rows = read_overlap(cursor)
    rows.append(first)
    tokens = model.tokenize("\n".join(row["text"] for row in rows)) if len(rows) > 1 else single
    while len(tokens) > TARGET_TOKENS and len(rows) > 1:
        rows.pop(0)
        tokens = model.tokenize("\n".join(row["text"] for row in rows))
    chunk = Chunk(rows, tokens)
    while True:
        candidate = read_next(chunk.cursor)
        if (
            candidate is None
            or candidate["section_id"] != first["section_id"]
            or candidate["bytes"] > MAX_PARAGRAPH_BYTES
        ):
            break
        joined = chunk.body + "\n" + candidate["text"]
        trial = model.tokenize(joined)
        if len(trial) > TARGET_TOKENS:
            break
        rows.append(candidate)
        chunk.tokens = trial
    after = chunk.cursor
    # 枚举完整后缀的最终分词，最长符合预算者用于下一块；不以各段计数求和。
    for offset in range(len(rows)):
        if len(model.tokenize("\n".join(row["text"] for row in rows[offset:]))) <= OVERLAP_TOKENS:
            after["overlap_start"] = rows[offset]["ordinal"]
            break
    return chunk, after
