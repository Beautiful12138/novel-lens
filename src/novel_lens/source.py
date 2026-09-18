"""解析明确的 TXT 协议并保留字节位置；不猜章节、不裁剪正文。"""

import re
from dataclasses import dataclass, field
from hashlib import sha256

from novel_lens.contracts import Issue, Position, ValidationReport

RULE_VERSION = "txt-v1"
FORMAT_SPACE = " \t\u3000"


@dataclass(frozen=True, slots=True)
class SourceLine:
    text: str
    position: Position


@dataclass(slots=True)
class ParsedSection:
    heading: SourceLine
    paragraphs: list[SourceLine] = field(default_factory=list)


@dataclass(slots=True)
class ParsedSource:
    name: SourceLine
    sections: list[ParsedSection]


class SourceFailure(Exception):
    """可公开的首个定位问题，不将输入正文带入异常消息。"""

    def __init__(self, code: str, message: str, position: Position | None = None) -> None:
        super().__init__(message)
        self.code, self.message, self.position = code, message, position


def source_lines(data: bytes) -> list[SourceLine]:
    """只把 CRLF、CR、LF 视为行结束；其他 Unicode 分隔符是正文字符。"""
    try:
        data.decode("utf-8")
    except UnicodeDecodeError as exc:
        line = len(re.findall(rb"\r\n|\r|\n", data[: exc.start])) + 1
        raise SourceFailure(
            "INVALID_ENCODING",
            "文件必须是有效的 UTF-8 TXT",
            Position(start_byte=exc.start, end_byte=exc.end, line=line),
        ) from None
    start = 3 if data.startswith(b"\xef\xbb\xbf") else 0
    result = []
    for line, match in enumerate(re.finditer(rb"([^\r\n]*)(?:\r\n|\r|\n|$)", data[start:]), 1):
        text = match.group(1).decode("utf-8")
        position = Position(
            start_byte=start + match.start(1), end_byte=start + match.end(1), line=line
        )
        if "\x00" in text:
            raise SourceFailure("INVALID_CHARACTER", "TXT 不允许含 NUL 字符", position)
        if text.strip(FORMAT_SPACE):
            result.append(SourceLine(text, position))
            if len(result) > 200000:
                raise SourceFailure("TOO_MANY_LINES", "非空行数超过 200000", position)
    return result


def parse_canonical(data: bytes) -> ParsedSource:
    lines = source_lines(data)
    if not lines or not lines[0].text.startswith("书名："):
        raise SourceFailure(
            "MISSING_NAME", "首条非空行必须为书名字段", lines[0].position if lines else None
        )
    name = lines[0].text[3:]
    if not name or name != name.strip(FORMAT_SPACE) or len(name) > 256:
        raise SourceFailure(
            "INVALID_NAME", "书名须为 1–256 字符且无首尾格式空白", lines[0].position
        )
    parsed = ParsedSource(SourceLine(name, lines[0].position), [])
    for line in lines[1:]:
        if line.text.startswith("书名："):
            raise SourceFailure("DUPLICATE_NAME", "一个文件只能有一个书名字段", line.position)
        if line.text.startswith("标题："):
            title = line.text[3:]
            if not title or title != title.strip(FORMAT_SPACE):
                raise SourceFailure("INVALID_TITLE", "标题不能为空或含首尾格式空白", line.position)
            parsed.sections.append(ParsedSection(SourceLine(title, line.position)))
        elif not parsed.sections:
            raise SourceFailure("MISSING_SECTION", "正文前必须有标题字段", line.position)
        else:
            parsed.sections[-1].paragraphs.append(line)
    if not parsed.sections:
        raise SourceFailure("MISSING_SECTION", "至少需要一个标题字段")
    if not any(section.paragraphs for section in parsed.sections):
        raise SourceFailure("EMPTY_BODY", "作品必须有正文")
    return parsed


def report(data: bytes, issue: Issue | None = None) -> ValidationReport:
    return ValidationReport(
        status="invalid" if issue else "valid",
        source_sha256=sha256(data).hexdigest(),
        rule_version=RULE_VERSION,
        issue_count=int(issue is not None),
        issues=[issue] if issue else [],
    )
