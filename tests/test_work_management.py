"""真实数据库验证作品屏蔽、HTTP 物理删除、回滚和并发边界。"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from typing import Any
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from conftest import ROOT, temporary_database
from mcp import Client
from pydantic import SecretStr
from sqlalchemy import Connection, event, text
from sqlalchemy.exc import IntegrityError
from starlette.testclient import TestClient
from test_analysis import create as create_job
from test_assets import create_annotation, create_tag
from test_entities_relations import entity, relation_request
from test_live_http import running_server
from test_mcp_http import call
from test_semantic import DeterministicModel, build, imported
from test_semantic_annotations import references
from test_style_guides import guide_request

from novel_lens.analysis import AnalysisService
from novel_lens.asset_contracts import AnnotationCreate, AnnotationOut, CoverageMark
from novel_lens.assets import AssetService
from novel_lens.config import Settings
from novel_lens.database import Database
from novel_lens.errors import ServiceError
from novel_lens.reading import ReadingService
from novel_lens.schema import metadata
from novel_lens.semantic import SemanticService, lock_key
from novel_lens.semantic_contracts import SemanticCreate, SemanticSearch
from novel_lens.work_management import WorkManagementService


def snapshot(database: Database) -> dict[str, str]:
    """对全部业务行求摘要，用于证明其他作品、共享词表和回执未改变。"""
    with database.engine.connect() as conn:
        return {
            table: conn.execute(
                text(
                    "SELECT md5(coalesce(string_agg(d,'' ORDER BY d),'')) FROM "
                    f"(SELECT md5(to_jsonb(t)::text) d FROM {table} t) q"
                )
            ).scalar_one()
            for table in metadata.tables
        }


def test_http_delete_all_assets_and_reimport(database: Database, client: TestClient) -> None:
    assets = AssetService(database)
    other = imported(database, "标题：保留\n不能改动的原文")
    tag = create_tag(assets, uuid4().hex, "共享标签")
    before = snapshot(database)
    data = f"书名：{uuid4()}\n标题：甲\n首段\n尾段".encode()
    key = str(uuid4())
    response = client.post("/work-imports", data={"request_id": key}, files={"file": data})
    assert response.status_code == 201
    work = UUID(response.json()["work"]["id"])
    refs = references(database, work)
    person = entity(assets, work)
    create_annotation(assets, (work, refs), tag_ids=[tag.id], entity_ids=[person.id])
    assets.create_relation(relation_request((work, refs), tag_ids=[tag.id], entity_ids=[person.id]))
    assets.create_style_guide(guide_request((work, refs)))
    analysis = AnalysisService(database)
    job = create_job(analysis, (work, refs))
    analysis.mark(
        CoverageMark(
            request_id=uuid4(),
            work_id=work,
            job_id=job.id,
            expected_version=job.version,
            source_range=refs[0],
            status="read",
        )
    )
    semantic = SemanticService(database, DeterministicModel())
    for kind in ("fulltext", "annotation"):
        index = semantic.create(
            SemanticCreate.model_validate(dict(work_id=work, kind=kind, request_id=uuid4()))
        ).index_id
        assert build(semantic, work, index).coverage.complete
    assert client.delete(f"/works/{work}").status_code == 204
    assert snapshot(database) == before
    assert client.delete(f"/works/{work}").status_code == 204
    assert client.get(f"/works/{work}").status_code == 404
    assert client.get(f"/works/{work}/file").status_code == 404
    assert client.get(f"/work-imports/{key}").status_code == 404
    assert client.get(f"/works/{other}").status_code == 200
    again = client.post("/work-imports", data={"request_id": str(uuid4())}, files={"file": data})
    assert again.status_code == 201 and again.json()["work"]["id"] != str(work)


def test_visibility_lists_search_and_restore(database: Database, client: TestClient) -> None:
    work = imported(database, "标题：甲\n可检索原文")
    assets = AssetService(database)
    create_annotation(assets, (work, references(database, work)), note="可检索说明")
    service = SemanticService(database, DeterministicModel())
    for kind in ("fulltext", "annotation"):
        index = service.create(
            SemanticCreate.model_validate(dict(work_id=work, kind=kind, request_id=uuid4()))
        ).index_id
        build(service, work, index)
    reading = ReadingService(database)
    original = reading.file(work)
    for _ in range(2):
        result = client.patch(f"/works/{work}/visibility", json={"visibility": "hidden"})
        assert result.status_code == 200 and result.json()["visibility"] == "hidden"
    assert str(work) not in {r["id"] for r in client.get("/works").json()["items"]}
    assert work not in {r.id for r in reading.list_works(1000, None).items}
    for value in ("hidden", "all"):
        assert str(work) in {
            r["id"]
            for r in client.get("/works", params={"visibility": value, "limit": 1000}).json()[
                "items"
            ]
        }
    assert client.get(f"/works/{work}/file").content == original
    for path in ("/source/search", "/annotations/search"):
        for format in ("full", "compact"):
            result = client.post(
                path, json={"work_id": str(work), "terms": ["可检索"], "format": format}
            )
            assert result.status_code == 409 and result.json()["code"] == "WORK_HIDDEN"
    for kind in ("fulltext", "annotation"):
        payload = dict(work_id=str(work), kind=kind, query="可检索", allow_partial=True)
        result = client.post(f"/works/{work}/semantic-search", json=payload)
        assert result.status_code == 409 and result.json()["code"] == "WORK_HIDDEN"
    assert (
        client.patch(f"/works/{work}/visibility", json={"visibility": "visible"}).status_code == 200
    )
    for path in ("/source/search", "/annotations/search"):
        assert client.post(path, json={"work_id": str(work), "terms": ["可检索"]}).json()["items"]
    for kind in ("fulltext", "annotation"):
        assert service.search(
            SemanticSearch.model_validate(dict(work_id=work, kind=kind, query="可检索"))
        ).items
    assert reading.file(work) == original
    assert (
        client.patch(f"/works/{work}/visibility", json={"visibility": "deleted"}).status_code == 422
    )
    assert (
        client.patch(f"/works/{uuid4()}/visibility", json={"visibility": "hidden"}).status_code
        == 404
    )
    # 页面游标不能跨可见性筛选复用。
    imported(database, "标题：甲\n分页样例")
    page = client.get("/works", params={"limit": 1}).json()
    assert page["next_cursor"]
    assert (
        client.get(
            "/works", params={"visibility": "hidden", "cursor": page["next_cursor"]}
        ).status_code
        == 422
    )


def test_delete_failure_rolls_back(database: Database, client: TestClient) -> None:
    work = imported(database, "标题：甲\n回滚原文")
    create_annotation(AssetService(database), (work, references(database, work)))
    before = snapshot(database)
    # 真实 PostgreSQL 触发器在清理已进行后拒绝最后的作品删除。
    with database.engine.begin() as conn:
        conn.execute(
            text(f"""
            CREATE FUNCTION reject_work_delete() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN IF OLD.id='{work}'::uuid THEN RAISE EXCEPTION 'test failure'; END IF;
            RETURN OLD; END $$;
            CREATE TRIGGER reject_work_delete BEFORE DELETE ON works
                FOR EACH ROW EXECUTE FUNCTION reject_work_delete();
        """)
        )
    try:
        assert client.delete(f"/works/{work}").status_code == 500
        assert snapshot(database) == before
    finally:
        with database.engine.begin() as conn:
            conn.execute(
                text("DROP TRIGGER reject_work_delete ON works; DROP FUNCTION reject_work_delete()")
            )


@pytest.mark.parametrize("kind", ["fulltext", "annotation"])
def test_build_lock_rejects_delete(database: Database, client: TestClient, kind: str) -> None:
    work = imported(database, "标题：甲\n构建中的原文")
    with database.engine.begin() as conn:
        conn.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key(work, kind)})
        result = client.delete(f"/works/{work}")
        assert result.status_code == 409 and result.json()["code"] == "WORK_BUSY"
        assert client.get(f"/works/{work}/file").status_code == 200
    assert client.delete(f"/works/{work}").status_code == 204


def test_write_finishes_before_delete_without_orphan_receipt(database: Database) -> None:
    work = imported(database, "标题：甲\n并发原文")
    assets = AssetService(database)
    request = AnnotationCreate(
        request_id=uuid4(), work_id=work, source_ranges=references(database, work)
    )
    ready, release, deleting = Event(), Event(), Event()

    def hold_writer(
        conn: Connection,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        if statement.startswith("INSERT INTO annotations"):
            ready.set()
            assert release.wait(10)
        if statement.startswith("SELECT works.id") and "FOR UPDATE" in statement:
            deleting.set()

    event.listen(database.engine, "before_cursor_execute", hold_writer)
    try:
        with ThreadPoolExecutor(2) as pool:
            writer = pool.submit(assets.create_annotation, request)
            assert ready.wait(5)
            deletion = pool.submit(WorkManagementService(database).delete, work)
            try:
                assert deleting.wait(5)
                assert not deletion.done()
            finally:
                release.set()
            assert isinstance(writer.result(10).result, AnnotationOut)
            deletion.result(10)
    finally:
        release.set()
        event.remove(database.engine, "before_cursor_execute", hold_writer)
    with pytest.raises(ServiceError) as missing:
        assets.write_result(request.request_id)
    assert missing.value.status == 404
    with pytest.raises(ServiceError) as deleted:
        assets.create_annotation(request)
    assert deleted.value.code == "WORK_NOT_FOUND"


def test_visibility_migration(postgres_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    with temporary_database(postgres_url, "0009") as url:
        db = Database(Settings.model_construct(database_url=SecretStr(url)))
        try:
            work = imported(db, "标题：甲\n旧版本原文")
            original = ReadingService(db).file(work)
            with monkeypatch.context() as patch:
                patch.setenv("NOVEL_LENS_DATABASE_URL", url)
                config = Config(str(ROOT / "alembic.ini"))
                command.upgrade(config, "head")
                command.check(config)
                assert ReadingService(db).get_work(work).visibility == "visible"
                with pytest.raises(IntegrityError), db.engine.begin() as conn:
                    conn.execute(text("UPDATE works SET visibility='bad'"))
                WorkManagementService(db).set_visibility(work, "hidden")
                command.downgrade(config, "0009")
                assert ReadingService(db).file(work) == original
                command.upgrade(config, "head")
                assert ReadingService(db).get_work(work).visibility == "visible"
                command.check(config)
        finally:
            db.close()


def test_real_http_delete_restart(postgres_url: str, tmp_path: Path) -> None:
    data = f"书名：{uuid4()}\n标题：章\nHTTP物理删除".encode()
    with running_server(postgres_url, tmp_path, "delete") as client:
        work = client.post(
            "/work-imports", data={"request_id": str(uuid4())}, files={"file": data}
        ).json()["work"]["id"]
        assert (
            client.patch(f"/works/{work}/visibility", json={"visibility": "hidden"}).status_code
            == 200
        )

        async def check_mcp() -> None:
            async with Client(str(client.base_url).rstrip("/") + "/mcp") as mcp:
                page = await call(mcp, "work_list", {"limit": 1000})
                assert work not in {r["id"] for r in page["items"]}
                for name in ("source_search", "annotation_search"):
                    await call(mcp, name, {"work_id": work, "terms": ["删除"]}, code="WORK_HIDDEN")
                await call(
                    mcp,
                    "source_semantic_search",
                    {"work_id": work, "query": "删除"},
                    code="WORK_HIDDEN",
                )

        asyncio.run(check_mcp())
        assert client.delete(f"/works/{work}").status_code == 204
    with running_server(postgres_url, tmp_path, "after-delete") as client:
        assert client.get(f"/works/{work}").status_code == 404
        assert client.delete(f"/works/{work}").status_code == 204
        assert (
            client.post(
                "/work-imports", data={"request_id": str(uuid4())}, files={"file": data}
            ).status_code
            == 201
        )
