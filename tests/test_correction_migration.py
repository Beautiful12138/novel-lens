"""0008 到 0009 保留原资产与旧回执，新增状态和版本满足约束。"""

from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from conftest import ROOT, temporary_database
from legacy_asset_seed import create_annotation, create_tag
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from novel_lens.asset_contracts import AnnotationGet, TagUpdate
from novel_lens.assets import AssetService
from novel_lens.config import Settings
from novel_lens.contracts import SourceRange
from novel_lens.database import Database
from novel_lens.importing import ImportService
from novel_lens.reading import ReadingService


def test_correction_upgrade_preserves_legacy(
    postgres_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    with temporary_database(postgres_url, "0008") as url:
        db = Database(Settings.model_construct(database_url=SecretStr(url)))
        try:
            data = f"书名：{uuid4()}\n标题：章\n原文不变".encode()
            work = ImportService(db, 10000).import_source(data, uuid4()).work
            reading = ReadingService(db)
            section = reading.list_sections(work.id, 100, None).items[0]
            p = reading.list_paragraphs(work.id, section.id, 100, None).items[0]
            ref = SourceRange(
                work_id=work.id,
                section_id=section.id,
                start_paragraph_id=p.id,
                end_paragraph_id=p.id,
            )
            assets = AssetService(db)
            tag = create_tag(assets, uuid4().hex, "旧标签")
            old = create_annotation(assets, (work.id, [ref]), tag_ids=[tag.id])
            with db.engine.connect() as conn:
                receipts = {
                    row[0]: row[1]
                    for row in conn.execute(
                        text("SELECT request_id,response FROM asset_write_requests")
                    )
                }
            with monkeypatch.context() as patch:
                patch.setenv("NOVEL_LENS_DATABASE_URL", url)
                config = Config(str(ROOT / "alembic.ini"))
                command.upgrade(config, "head")
                command.check(config)
                current = assets.get_annotation(
                    AnnotationGet(work_id=work.id, annotation_id=old.id)
                )
                assert (
                    current.status == "active"
                    and current.model_dump(exclude={"status"}) == old.model_dump()
                )
                now = assets.get_tag(tag.id)
                assert now.version == 1 and now.updated_at == now.created_at
                assert now.model_dump(exclude={"version", "updated_at"}) == tag.model_dump()
                assets.update_tag(
                    TagUpdate(
                        request_id=uuid4(),
                        tag_id=tag.id,
                        expected_version=1,
                        name="修订",
                        description="新定义",
                        aliases=[],
                    )
                )
                for identifier, expected in receipts.items():
                    assert assets.write_result(identifier).model_dump(mode="json") == expected | {
                        "replayed": True
                    }
                assert reading.file(work.id) == data
                for stmt in [
                    "UPDATE tags SET version=0",
                    "UPDATE annotations SET status='invalid'",
                ]:
                    with pytest.raises(IntegrityError), db.engine.begin() as conn:
                        conn.execute(text(stmt))
                command.downgrade(config, "0008")
                with db.engine.connect() as conn:
                    assert (
                        conn.execute(
                            text("SELECT note FROM annotations WHERE id=:id"), {"id": old.id}
                        ).scalar()
                        == old.note
                    )
                    assert (
                        conn.execute(
                            text("SELECT name FROM tags WHERE id=:id"), {"id": tag.id}
                        ).scalar()
                        == "修订"
                    )
                command.upgrade(config, "head")
                command.check(config)
                assert reading.file(work.id) == data
        finally:
            db.close()
