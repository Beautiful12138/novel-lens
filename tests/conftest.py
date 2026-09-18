"""数据库测试只使用当次创建的独立数据库，不清理开发库。"""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from psycopg import sql
from pydantic import SecretStr
from sqlalchemy.engine import make_url
from starlette.testclient import TestClient

from novel_lens.app import create_app
from novel_lens.config import Settings
from novel_lens.database import Database

ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def temporary_database(raw: str, revision: str = "head") -> Iterator[str]:
    """创建当次测试库并迁移到指定版本；退出只删除这个随机库。"""
    base = make_url(raw)
    name = "novel_lens_test_" + uuid4().hex
    admin_url = base.set(drivername="postgresql", database="postgres")
    test_url = base.set(database=name).render_as_string(hide_password=False)
    with psycopg.connect(admin_url.render_as_string(hide_password=False), autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {} ENCODING 'UTF8'").format(sql.Identifier(name)))
        try:
            # 仅在迁移加载配置期间替换环境，退出即恢复；不更改开发者的 .env。
            with pytest.MonkeyPatch.context() as patch:
                patch.setenv("NOVEL_LENS_DATABASE_URL", test_url)
                config = Config(str(ROOT / "alembic.ini"))
                command.upgrade(config, revision)
            yield test_url
        finally:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[str]:
    """显式测试连接需有 CREATE DATABASE 权限；未配置时明确跳过集成测试。"""
    raw = os.environ.get("NOVEL_LENS_TEST_DATABASE_URL")
    if not raw:
        pytest.skip("未配置 NOVEL_LENS_TEST_DATABASE_URL，未验证 PostgreSQL 行为")
    with temporary_database(raw) as url:
        yield url


@pytest.fixture
def settings(postgres_url: str) -> Settings:
    return Settings.model_construct(database_url=SecretStr(postgres_url))


@pytest.fixture
def database(settings: Settings) -> Iterator[Database]:
    database = Database(settings)
    try:
        yield database
    finally:
        database.close()


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as client:
        yield client
