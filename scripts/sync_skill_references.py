"""同步中文写作 Skill 的唯一写作原则副本；运行 Skill 无需本脚本。"""

from __future__ import annotations

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs/中文小说正文写作原则.md"
TARGET = ROOT / ".agents/skills/chinese-novel-writing/references/writing-principles.md"
NOTICE = "<!-- 从写作原则原文同步生成；维护者请修改原文后同步，勿单独编辑副本。 -->\n\n"


def main() -> int:
    """只同步固定文件；检查模式不写入，缺失或失配时返回非零。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="只检查写作原则副本是否与维护源一致")
    args = parser.parse_args()
    expected = NOTICE + SOURCE.read_text(encoding="utf-8")
    matches = TARGET.is_file() and TARGET.read_text(encoding="utf-8") == expected
    if args.check:
        print("写作原则副本一致" if matches else "写作原则副本需要同步")
        return 0 if matches else 1
    if not matches:
        TARGET.parent.mkdir(parents=True, exist_ok=True)
        TARGET.write_text(expected, encoding="utf-8", newline="\n")
    print("写作原则副本已同步" if not matches else "写作原则副本无变化")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
