"""同步包内 HTTP 指南和独立叙述者参考；运行 Skill 无需本脚本。"""

from __future__ import annotations

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COPIES = tuple(
    (
        "docs/AI-HTTP使用指南.md",
        f".agents/skills/{skill}/references/http-api.md",
        "<!-- 从 docs/AI-HTTP使用指南.md 同步生成；勿单独编辑副本。 -->\n\n",
    )
    for skill in ("novel-analysis", "novel-scene-writing")
) + tuple(
    (
        f"docs/{name}.md",
        f".agents/skills/novel-scene-writing/references/{name}.md",
        f"<!-- 从 docs/{name}.md 同步生成；勿单独编辑副本。 -->\n\n",
    )
    for name in ("龙族叙述者声音分析", "龙族叙述者逐章细读", "叙述者声音原创示例")
)


def main() -> int:
    """只同步固定文件；检查模式不写入，缺失或失配时返回非零。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="只检查包内副本是否与维护源一致")
    args = parser.parse_args()
    valid = True
    for source, destination, notice in COPIES:
        target = ROOT / destination
        expected = notice + (ROOT / source).read_text(encoding="utf-8")
        matches = target.is_file() and target.read_text(encoding="utf-8") == expected
        if args.check:
            valid = valid and matches
            print(f"{destination}: {'一致' if matches else '需要同步'}")
        else:
            if not matches:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(expected, encoding="utf-8", newline="\n")
            print(f"{destination}: {'无变化' if matches else '已同步'}")
    return 0 if valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
