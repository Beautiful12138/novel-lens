"""两层派生索引升降级、精确结构与包含索引代的备份恢复验证。"""

import json
import os
import subprocess
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from conftest import ROOT, temporary_database
from psycopg import sql
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.engine import make_url
from test_semantic import DeterministicModel, build, imported, new_index
from test_semantic_annotations import annotated_index, annotation, references

from novel_lens.config import Settings
from novel_lens.database import Database
from novel_lens.semantic import SemanticService
from novel_lens.semantic_contracts import SemanticGet, SemanticSearch


def test_upgrade_downgrade_preserves_source(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = os.environ.get("NOVEL_LENS_TEST_DATABASE_URL")
    if not raw:
        pytest.skip("未配置隔离数据库")
    with temporary_database(raw, "0006") as url:
        database = Database(Settings.model_construct(database_url=SecretStr(url)))
        try:
            work = imported(database, "标题：甲\n迁移前的原文")
            service = SemanticService(database, DeterministicModel())
            with pytest.raises(Exception) as not_ready:
                service.get(SemanticGet(work_id=work))
            assert getattr(not_ready.value, "code", None) == "SEMANTIC_SCHEMA_NOT_READY"
            with monkeypatch.context() as patch:
                patch.setenv("NOVEL_LENS_DATABASE_URL", url)
                config = Config(str(ROOT / "alembic.ini"))
                command.upgrade(config, "head")
                command.check(config)
                index = new_index(service, work)
                build(service, work, index)
                assert service.search(SemanticSearch(work_id=work, query="迁移")).items
                command.downgrade(config, "0006")
                with database.engine.connect() as conn:
                    assert (
                        conn.execute(text("SELECT text FROM paragraphs")).scalar() == "迁移前的原文"
                    )
                    assert (
                        conn.execute(
                            text(
                                "SELECT count(*) FROM pg_extension "
                                "WHERE extname IN ('pgroonga','vector')"
                            )
                        ).scalar()
                        == 2
                    )
                    assert (
                        conn.execute(text("SELECT to_regclass('semantic_indexes')")).scalar()
                        is None
                    )
                command.upgrade(config, "head")
                command.check(config)
                assert service.get(SemanticGet(work_id=work)).state == "missing"
        finally:
            database.close()


def test_backup_restore_semantic_tables(postgres_url: str, tmp_path: Path) -> None:
    """可选 WSL 容器验收；只恢复到当次创建的随机空库，不覆盖原库。"""
    container = os.environ.get("NOVEL_LENS_TEST_POSTGRES_CONTAINER")
    if not container:
        pytest.skip("未指定隔离 WSL PostgreSQL 容器，未验证真实备份恢复")
    # 此入口只接受测试运行器创建的专属容器，禁止误指向业务服务。
    assert container.startswith("novel-lens-fulltext-")
    url = make_url(postgres_url)
    database = Database(Settings.model_construct(database_url=SecretStr(postgres_url)))
    name = "novel_lens_restore_" + uuid4().hex
    admin_url = url.set(drivername="postgresql", database="postgres").render_as_string(
        hide_password=False
    )
    tables = (
        "semantic_indexes",
        "semantic_index_heads",
        "semantic_index_items",
        "semantic_build_receipts",
        "semantic_annotation_snapshots",
    )

    def docker(*args: str, data: bytes | None = None) -> bytes:
        return subprocess.run(
            ["wsl", "-d", "Ubuntu-24.04", "--", "docker", "exec", "-i", container, *args],
            input=data,
            capture_output=True,
            check=True,
        ).stdout

    try:
        service = SemanticService(database, DeterministicModel())
        work = imported(database, "标题：甲\n备份包含完整原文引用")
        index = new_index(service, work)
        build(service, work, index)
        annotation(database, work, references(database, work))
        annotated = annotated_index(service, work)
        build(service, work, annotated)
        with database.engine.connect() as conn:
            before = {
                table: sorted(
                    json.dumps(row, sort_keys=True)
                    for row in conn.execute(text(f"SELECT to_jsonb(t) FROM {table} t")).scalars()
                )
                for table in tables
            }
        assert url.database is not None
        backup = docker("pg_dump", "-U", "postgres", "-Fc", url.database)
        (tmp_path / "semantic.dump").write_bytes(backup)
        with psycopg.connect(admin_url, autocommit=True) as admin:
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
            try:
                docker("pg_restore", "-U", "postgres", "--exit-on-error", "-d", name, data=backup)
                restored = Database(
                    Settings.model_construct(
                        database_url=SecretStr(
                            url.set(database=name).render_as_string(hide_password=False)
                        )
                    )
                )
                try:
                    with restored.engine.connect() as conn:
                        after = {
                            table: sorted(
                                json.dumps(row, sort_keys=True)
                                for row in conn.execute(
                                    text(f"SELECT to_jsonb(t) FROM {table} t")
                                ).scalars()
                            )
                            for table in tables
                        }
                    assert after == before
                    search = SemanticService(restored, DeterministicModel())
                    assert (
                        search.search(SemanticSearch(work_id=work, query="备份")).index_id == index
                    )
                    assert (
                        search.search(
                            SemanticSearch(work_id=work, kind="annotation", query="备份")
                        ).index_id
                        == annotated
                    )
                finally:
                    restored.close()
            finally:
                admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
    finally:
        database.close()


def test_0008_roundtrip_preserves_fulltext_and_assets(monkeypatch: pytest.MonkeyPatch) -> None:
    """降级只移除标注派生数据；全文向量、历史回执及可变分析资产不受影响。"""
    raw = os.environ.get("NOVEL_LENS_TEST_DATABASE_URL")
    if not raw:
        pytest.skip("未配置隔离数据库")
    with temporary_database(raw) as url:
        database = Database(Settings.model_construct(database_url=SecretStr(url)))
        try:
            work = imported(database, "标题：甲\n原文保留")
            a = annotation(database, work, references(database, work))
            service = SemanticService(database, DeterministicModel())
            full = new_index(service, work)
            build(service, work, full)
            annotated = annotated_index(service, work)
            build(service, work, annotated)

            def full_rows() -> list[str]:
                with database.engine.connect() as conn:
                    return list(
                        conn.execute(
                            text("""
                        SELECT (to_jsonb(i)-'annotation_id'-'evidence_id'-'range_ordinal')::text
                        FROM semantic_index_items i WHERE index_id=:id ORDER BY id
                    """),
                            {"id": full},
                        )
                        .scalars()
                        .all()
                    )

            before = full_rows()
            with monkeypatch.context() as patch:
                patch.setenv("NOVEL_LENS_DATABASE_URL", url)
                config = Config(str(ROOT / "alembic.ini"))
                command.check(config)
                command.downgrade(config, "0007")
                assert full_rows() == before
                with database.engine.connect() as conn:
                    assert (
                        conn.execute(
                            text("SELECT version FROM annotations WHERE id=:id"), {"id": a.id}
                        ).scalar_one()
                        == 1
                    )
                    assert (
                        conn.execute(
                            text("SELECT count(*) FROM semantic_indexes WHERE id=:id"),
                            {"id": annotated},
                        ).scalar_one()
                        == 0
                    )
                    assert (
                        conn.execute(
                            text("SELECT count(*) FROM semantic_build_receipts WHERE index_id=:id"),
                            {"id": full},
                        ).scalar_one()
                        == 1
                    )
                with pytest.raises(Exception) as not_ready:
                    service.get(SemanticGet(work_id=work, kind="annotation"))
                assert getattr(not_ready.value, "code", None) == "SEMANTIC_SCHEMA_NOT_READY"
                command.upgrade(config, "0008")
                command.check(config)
                assert full_rows() == before
                assert service.search(SemanticSearch(work_id=work, query="原文")).index_id == full
                assert service.get(SemanticGet(work_id=work, kind="annotation")).state == "missing"
                rebuilt = annotated_index(service, work)
                assert build(service, work, rebuilt).coverage.complete
        finally:
            database.close()
