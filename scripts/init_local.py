"""为全新本机数据库生成密码和应用配置；不连接数据库或覆盖已有文件。"""

from __future__ import annotations

import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def initialize(root: Path) -> None:
    """只用于首次安装；独占创建文件，失败时回收本次创建的文件。"""
    password_file = root / "data/postgres/password"
    env_file = root / ".env"
    targets = (password_file, env_file)
    if any(path.exists() or path.is_symlink() for path in targets):
        raise FileExistsError(".env 或密码文件已存在；请复用现有配置，不重新初始化。")
    password = secrets.token_hex(32)
    config = (
        "# 本机初始化生成；不要提交 Git 或用于其他电脑的数据库。\n"
        "NOVEL_LENS_HOST=127.0.0.1\n"
        "NOVEL_LENS_PORT=8000\n"
        "NOVEL_LENS_LOG_LEVEL=INFO\n"
        f"NOVEL_LENS_DATABASE_URL=postgresql+psycopg://novel_lens:{password}"
        "@127.0.0.1:15432/novel_lens?sslmode=disable\n"
        "NOVEL_LENS_MAX_FILE_BYTES=67108864\n"
        "NOVEL_LENS_MAX_REQUEST_BYTES=68157440\n"
    )
    password_file.parent.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []
    try:
        for path, content in ((password_file, password + "\n"), (env_file, config)):
            # x 模式也防止预检后另一初始化进程创建文件时被覆盖。
            with path.open("x", encoding="utf-8", newline="\n") as stream:
                created.append(path)
                stream.write(content)
    except OSError:
        for path in reversed(created):
            path.unlink(missing_ok=True)
        raise


def main() -> int:
    """输出不包含密码；配置已存在或写入失败时非零退出。"""
    try:
        initialize(ROOT)
    except FileExistsError:
        print("未初始化：.env 或密码文件已存在；请复用配置，见部署指南。", file=sys.stderr)
        return 1
    except OSError:
        print("初始化失败：请检查项目目录的写入权限；未输出凭据。", file=sys.stderr)
        return 1
    print("已生成 .env 和 data/postgres/password。下一步执行 docker compose up -d --build --wait。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
