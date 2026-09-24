"""0003 有数据数据库升级后原资产、字节和请求结果保持不变。"""

from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from conftest import ROOT, temporary_database
from legacy_asset_seed import create_annotation, create_tag, legacy_columns
from pydantic import SecretStr
from sqlalchemy import select, text
from test_entities_relations import entity, relation
from test_style_guides import guide_request

from novel_lens.asset_contracts import RelationSetStatus, StyleGuideGet
from novel_lens.assets import AssetService
from novel_lens.config import Settings
from novel_lens.contracts import SourceRange
from novel_lens.database import Database
from novel_lens.importing import ImportService
from novel_lens.reading import ReadingService
from novel_lens.schema import asset_write_requests, metadata


@pytest.mark.parametrize("revision", ["0003", "0004"])
def test_upgrade_preserves_assets_and_snapshots(
    postgres_url: str, monkeypatch: pytest.MonkeyPatch, revision: str
) -> None:
    with temporary_database(postgres_url, revision) as url:
        database = Database(Settings.model_construct(database_url=SecretStr(url)))
        try:
            assets = AssetService(database)
            work = (
                ImportService(database, 10000)
                .import_source(
                    f"\ufeff书名：{uuid4()}\r\n标题：甲\r\n原文😀\r\n标题：乙\r\n尾声".encode(),
                    uuid4(),
                )
                .work
            )
            reading = ReadingService(database)
            refs = []
            for section in reading.list_sections(work.id, 100, None).items:
                paragraph = reading.list_paragraphs(work.id, section.id, 100, None).items[0]
                refs.append(
                    SourceRange(
                        work_id=work.id,
                        section_id=section.id,
                        start_paragraph_id=paragraph.id,
                        end_paragraph_id=paragraph.id,
                    )
                )
            source = (work.id, refs)
            tag, identity = create_tag(assets, uuid4().hex, "迁移"), entity(assets, work.id)
            create_annotation(assets, source, tag_ids=[tag.id], entity_ids=[identity.id])
            linked = relation(assets, source, tag_ids=[tag.id], entity_ids=[identity.id])
            assets.set_relation_status(
                RelationSetStatus(
                    request_id=uuid4(),
                    work_id=work.id,
                    relation_id=linked.id,
                    expected_version=1,
                    status="withdrawn",
                )
            )
            guide_before = (
                assets.create_style_guide(guide_request(source)).result
                if revision == "0004"
                else None
            )
            tables = [
                t
                for t in metadata.sorted_tables
                if not t.name.startswith("analysis_")
                and not t.name.startswith("semantic_")
                and (revision == "0004" or not t.name.startswith("style_guide"))
            ]
            with database.engine.connect() as connection:
                before = {
                    t.name: [
                        dict(r) for r in connection.execute(select(*legacy_columns(t))).mappings()
                    ]
                    for t in tables
                }
                snapshots = [
                    assets.write_result(r["request_id"]) for r in before[asset_write_requests.name]
                ]
            with monkeypatch.context() as patch:
                patch.setenv("NOVEL_LENS_DATABASE_URL", url)
                config = Config(str(ROOT / "alembic.ini"))
                command.upgrade(config, "head")
                command.check(config)
            with database.engine.connect() as connection:
                assert (
                    connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                    == "0009"
                )
                for table in tables:
                    assert [
                        dict(r)
                        for r in connection.execute(select(*legacy_columns(table))).mappings()
                    ] == before[table.name]
            for snapshot in snapshots:
                assert assets.write_result(snapshot.request_id) == snapshot
            guide = guide_before or assets.create_style_guide(guide_request(source)).result
            assert assets.get_style_guide(StyleGuideGet(work_id=work.id)) == guide
        finally:
            database.close()
