"""已有 0001 原文库升级后，文件字节、结构 UUID 和读取位置保持不变。"""

from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from conftest import ROOT, temporary_database
from pydantic import SecretStr
from sqlalchemy import select

from novel_lens.asset_contracts import AnnotationCreate, AnnotationGet, AnnotationOut
from novel_lens.assets import AssetService
from novel_lens.config import Settings
from novel_lens.contracts import SourceRange
from novel_lens.database import Database
from novel_lens.importing import ImportService
from novel_lens.reading import ReadingService
from novel_lens.schema import sources


def test_upgrade_preserves_existing_source(
    postgres_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    with temporary_database(postgres_url, "0001") as old_url:
        database = Database(Settings.model_construct(database_url=SecretStr(old_url)))
        try:
            data = f"\ufeff书名：{uuid4()}\r\n标题：章\r\n　原文😀 \t\r\n末段".encode()
            key = uuid4()
            importing, reading = ImportService(database, 10000), ReadingService(database)
            work = importing.import_source(data, key).work
            sections = reading.list_sections(work.id, 100, None)
            section = sections.items[0]
            paragraphs = reading.list_paragraphs(work.id, section.id, 100, None)
            with monkeypatch.context() as patch:
                patch.setenv("NOVEL_LENS_DATABASE_URL", old_url)
                config = Config(str(ROOT / "alembic.ini"))
                command.upgrade(config, "head")
                command.check(config)
            assert importing.result(key).work == work
            assert reading.list_sections(work.id, 100, None) == sections
            assert reading.list_paragraphs(work.id, section.id, 100, None) == paragraphs
            with database.engine.connect() as connection:
                assert (
                    connection.execute(
                        select(sources.c.content).where(sources.c.work_id == work.id)
                    ).scalar_one()
                    == data
                )
            assets = AssetService(database)
            result = assets.create_annotation(
                AnnotationCreate(
                    request_id=uuid4(),
                    work_id=work.id,
                    source_ranges=[
                        SourceRange(
                            work_id=work.id,
                            section_id=section.id,
                            start_paragraph_id=paragraphs.items[0].id,
                            end_paragraph_id=paragraphs.items[-1].id,
                        )
                    ],
                )
            ).result
            assert isinstance(result, AnnotationOut)
            assert (
                assets.get_annotation(AnnotationGet(work_id=work.id, annotation_id=result.id))
                == result
            )
        finally:
            database.close()
