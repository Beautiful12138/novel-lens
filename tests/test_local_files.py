"""验证读取期间真实文件变化的拒绝行为，不接触开发小说。"""

import os
from pathlib import Path

import pytest

from novel_lens.errors import ServiceError
from novel_lens.local_files import read_source


def test_source_change_during_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "book.txt"
    source.write_bytes(b"initial")
    fstat = os.fstat
    changed = False

    def change_after_stat(descriptor: int) -> os.stat_result:
        nonlocal changed
        result = fstat(descriptor)
        if not changed:
            changed = True
            # 在读取器取到初始大小后模拟另一个编辑器修改真实文件。
            with source.open("ab") as writer:
                writer.write(b" appended")
        return result

    monkeypatch.setattr(os, "fstat", change_after_stat)
    with pytest.raises(ServiceError) as caught:
        read_source(str(source), 1024)
    assert caught.value.code == "SOURCE_FILE_CHANGED"
    assert source.read_bytes() == b"initial appended"
