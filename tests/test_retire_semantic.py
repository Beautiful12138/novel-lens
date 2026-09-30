"""验证无模型新安装及旧索引退役；所有写入均在随机独立库。"""

import asyncio
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from annotation_fixtures import annotation, references
from conftest import ROOT, temporary_database
from mcp import Client
from mcp.shared.exceptions import MCPError
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_live_http import running_server
from test_understanding_migration import _connect, _seed

from novel_lens.config import Settings
from novel_lens.database import Database
from scripts.init_database import initialize


def snapshot(database: Database) -> dict[str, str]:
    """只记录业务内容摘要；作品中忽略待删除的旧搜索世代字段。"""
    from novel_lens.schema import metadata

    with database.engine.connect() as conn:
        return {
            name: conn.scalar(
                text(
                    "SELECT md5(coalesce(string_agg(d,'' ORDER BY d),'')) FROM "
                    f"(SELECT md5((to_jsonb(t)-'reference_version')::text) d FROM {name} t) q"
                )
            )
            for name in metadata.tables
        }


def test_new_install_without_vector_and_nonempty_refusal(postgres_url: str) -> None:
    with temporary_database(postgres_url, revision="empty") as url:
        database = Database(Settings.model_construct(database_url=SecretStr(url)))
        try:
            assert initialize(database.engine) == "0017"
            with database.engine.connect() as conn:
                assert (
                    conn.scalar(text("SELECT count(*) FROM pg_extension WHERE extname='vector'"))
                    == 0
                )
                assert conn.scalar(text("SELECT version_num FROM alembic_version")) == "0017"
            before = snapshot(database)
            with pytest.raises(ValueError, match="非空"):
                initialize(database.engine)
            assert snapshot(database) == before
        finally:
            database.close()


def test_0017_preserves_business_and_rejects_external_vector_dependency(
    postgres_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    with temporary_database(postgres_url, revision="0015") as url:
        with _connect(url) as conn:
            ids = _seed(conn)
        monkeypatch.setenv("NOVEL_LENS_DATABASE_URL", url)
        config = Config(str(ROOT / "alembic.ini"))
        command.upgrade(config, "0016")
        database = Database(Settings.model_construct(database_url=SecretStr(url)))
        try:
            # 保留新的合法标注，证明 0017 不会像 0016 一样清空分析成果。
            annotation(database, ids["work"], references(database, ids["work"]))
            before = snapshot(database)
            with database.engine.begin() as conn:
                conn.execute(text("CREATE TABLE other_vector_use (value vector(3))"))
            with pytest.raises(DBAPIError):
                command.upgrade(config, "head")
            with database.engine.begin() as conn:
                assert conn.scalar(text("SELECT version_num FROM alembic_version")) == "0016"
                assert conn.scalar(text("SELECT count(*) FROM semantic_index_items")) > 0
                conn.execute(text("DROP TABLE other_vector_use"))
            assert snapshot(database) == before
            command.upgrade(config, "head")
            command.check(config)
            assert snapshot(database) == before
            with database.engine.connect() as conn:
                assert (
                    conn.scalar(text("SELECT count(*) FROM pg_extension WHERE extname='vector'"))
                    == 0
                )
                assert (
                    conn.scalar(
                        text(
                            "SELECT count(*) FROM pg_tables WHERE schemaname='public' "
                            "AND (tablename LIKE 'semantic_%' OR tablename LIKE 'reference_%')"
                        )
                    )
                    == 0
                )
                assert (
                    conn.scalar(
                        text("SELECT count(*) FROM pg_trigger WHERE tgname LIKE 'reference_%'")
                    )
                    == 0
                )
            with pytest.raises(RuntimeError, match="备份"):
                command.downgrade(config, "0016")
        finally:
            database.close()


def test_retired_http_and_maintenance_mcp_endpoints(postgres_url: str, tmp_path: Path) -> None:
    with running_server(postgres_url, tmp_path, "retired-semantic") as http:
        for endpoint in ("semantic-search", "semantic-indexes", "semantic-indexes/x/build"):
            assert (
                http.post(
                    f"/works/00000000-0000-0000-0000-000000000000/{endpoint}", json={}
                ).status_code
                == 404
            )

        async def exercise() -> None:
            async with Client(str(http.base_url).rstrip("/") + "/mcp") as mcp:
                names = {t.name for t in (await mcp.list_tools()).tools}
                assert {"source_search", "annotation_search", "source_read"} <= names
                assert not any("semantic" in name for name in names)
                with pytest.raises(MCPError):
                    await mcp.call_tool("source_semantic_search", {})

        asyncio.run(exercise())
