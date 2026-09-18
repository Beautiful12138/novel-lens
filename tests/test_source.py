"""小样例验证 Canonical 解析和可回查的字节位置。"""

import pytest

from novel_lens.source import SourceFailure, parse_canonical


def test_unicode_whitespace_and_byte_positions() -> None:
    data = (
        "\ufeff\r\n书名：样例\r\n标题：重名\r　　首段😀\u2028仍属同段 \t\n \t　\n尾段\r标题：重名\r"
    ).encode()
    parsed = parse_canonical(data)
    assert parsed.name.text == "样例"
    assert [s.heading.text for s in parsed.sections] == ["重名", "重名"]
    assert parsed.sections[1].paragraphs == []
    assert [p.text for p in parsed.sections[0].paragraphs] == [
        "　　首段😀\u2028仍属同段 \t",
        "尾段",
    ]
    for paragraph in parsed.sections[0].paragraphs:
        position = paragraph.position
        assert data[position.start_byte : position.end_byte].decode() == paragraph.text
    assert parsed.sections[0].paragraphs[0].position.line == 4


@pytest.mark.parametrize(
    ("data", "code"),
    [
        (b"\xff", "INVALID_ENCODING"),
        ("书名：书\n标题：章\n正文\x00".encode(), "INVALID_CHARACTER"),
        ("正文".encode(), "MISSING_NAME"),
        ("书名： \n标题：章\n正文".encode(), "INVALID_NAME"),
        ("书名：书\n正文".encode(), "MISSING_SECTION"),
        ("书名：书\n书名：另一部".encode(), "DUPLICATE_NAME"),
        ("书名：书\n标题：\n正文".encode(), "INVALID_TITLE"),
        ("书名：书\n标题：章\n　 \t".encode(), "EMPTY_BODY"),
    ],
)
def test_reject_unsupported_source(data: bytes, code: str) -> None:
    with pytest.raises(SourceFailure) as caught:
        parse_canonical(data)
    assert caught.value.code == code


def test_encoding_error_position_and_line_limit() -> None:
    prefix = "\ufeff书名：书\r\n标题：章\r正文\n".encode()
    with pytest.raises(SourceFailure) as caught:
        parse_canonical(prefix + b"\xff")
    position = caught.value.position
    assert position is not None
    assert (position.start_byte, position.end_byte, position.line) == (
        len(prefix),
        len(prefix) + 1,
        4,
    )
    with pytest.raises(SourceFailure, match="200000"):
        parse_canonical("书名：书\n标题：章\n".encode() + b"x\n" * 199999)
