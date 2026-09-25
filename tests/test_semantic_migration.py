"""两层派生索引升降级、精确结构与包含索引代的备份恢复验证。"""

import json
import os
import subprocess
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.engine import make_url
from test_semantic import DeterministicModel, build, imported, new_index
from test_semantic_annotations import annotated_index, annotation, references

from novel_lens.config import Settings
from novel_lens.database import Database
from novel_lens.semantic import SemanticService
from novel_lens.semantic_contracts import SemanticSearch


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
