"""空库结构匹配、旧库拒绝且无数据丢失；不实施旧分析数据转换。"""

from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from conftest import ROOT, temporary_database
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from novel_lens.config import Settings
from novel_lens.database import Database


def test_current_schema_matches_metadata(
    postgres_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NOVEL_LENS_DATABASE_URL", postgres_url)
    command.check(Config(str(ROOT / "alembic.ini")))


def test_nonempty_legacy_database_is_rejected(
    postgres_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    with temporary_database(postgres_url, "0010") as url:
        database = Database(Settings.model_construct(database_url=SecretStr(url)))
        identifier = uuid4()
        try:
            with database.engine.begin() as conn:
                conn.execute(
                    text("""
                    INSERT INTO works(id,name,request_id,fingerprint,source_sha256,
                        source_bytes,section_count,paragraph_count,character_count)
                    VALUES (:id,'旧作品',:request,:hash,:hash,1,1,1,1)
                """),
                    {"id": identifier, "request": uuid4(), "hash": "0" * 64},
                )
            monkeypatch.setenv("NOVEL_LENS_DATABASE_URL", url)
            with pytest.raises(DBAPIError, match="requires an empty database"):
                command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
            with database.engine.connect() as conn:
                assert (
                    conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                    == "0010"
                )
                assert conn.execute(text("SELECT id FROM works")).scalar_one() == identifier
                assert (
                    conn.execute(text("SELECT to_regclass('work_sources')")).scalar_one()
                    == "work_sources"
                )
                assert conn.execute(text("SELECT to_regclass('parts')")).scalar_one() is None
        finally:
            database.close()
