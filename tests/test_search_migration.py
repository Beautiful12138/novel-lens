"""已有 0005 数据启用搜索、索引重建与回退后的兼容行为。"""

import pytest
from alembic import command
from alembic.config import Config
from conftest import ROOT, temporary_database
from pydantic import SecretStr
from sqlalchemy import select, text
from starlette.testclient import TestClient
from test_assets import create_annotation
from test_search import import_search_source

from novel_lens.app import create_app
from novel_lens.assets import AssetService
from novel_lens.config import Settings
from novel_lens.database import Database
from novel_lens.reading import ReadingService
from novel_lens.schema import metadata
from novel_lens.search import SearchService
from novel_lens.search_contracts import SearchRequest


def test_upgrade_reindex_and_downgrade(postgres_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    with temporary_database(postgres_url, "0005") as url:
        settings = Settings.model_construct(database_url=SecretStr(url))
        database = Database(settings)
        try:
            source = import_search_source(database)
            assets = AssetService(database)
            create_annotation(assets, source, note="月光留白")
            request = SearchRequest(work_id=source[0], terms=["月光"])
            with TestClient(create_app(settings)) as client:
                for path in ["/source/search", "/annotations/search"]:
                    response = client.post(path, json=request.model_dump(mode="json"))
                    assert response.status_code == 503
                    assert response.json()["code"] == "SEARCH_UNAVAILABLE"
                assert client.get(f"/works/{source[0]}").status_code == 200
            with database.engine.connect() as c:
                before = {
                    t.name: list(c.execute(select(t)).mappings()) for t in metadata.sorted_tables
                }
            with monkeypatch.context() as patch:
                patch.setenv("NOVEL_LENS_DATABASE_URL", url)
                config = Config(str(ROOT / "alembic.ini"))
                command.upgrade(config, "head")
                command.check(config)
                search = SearchService(database)
                source_hits, note_hits = search.source(request), search.annotations(request)
                assert len(source_hits.items) == 4 and len(note_hits.items) == 1
                with database.engine.begin() as c:
                    for name in ["ix_paragraphs_text_search", "ix_annotations_note_search"]:
                        c.execute(text(f"REINDEX INDEX {name}"))
                    for table in metadata.sorted_tables:
                        assert list(c.execute(select(table)).mappings()) == before[table.name]
                assert search.source(request) == source_hits
                assert search.annotations(request) == note_hits
                command.downgrade(config, "0005")
                assert ReadingService(database).get_work(source[0]).id == source[0]
                command.upgrade(config, "head")
                assert search.source(request) == source_hits
                assert search.annotations(request) == note_hits
        finally:
            database.close()
