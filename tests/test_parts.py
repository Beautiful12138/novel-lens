"""新模型的真实数据库验收；所有素材与数据库均为测试专用。"""

from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from starlette.testclient import TestClient
from test_semantic import DeterministicModel

from novel_lens.analysis import AnalysisService
from novel_lens.asset_contracts import (
    AnalysisJobCreate,
    AnalysisJobGet,
    AnalysisJobOut,
    AnalysisTarget,
    Recovery,
)
from novel_lens.catalog import CatalogService
from novel_lens.contracts import ParagraphOut, PartUpdate, WorkCreate, WorkOut
from novel_lens.database import Database
from novel_lens.errors import ServiceError
from novel_lens.importing import ImportService
from novel_lens.reading import ReadingService
from novel_lens.search import SearchService
from novel_lens.search_contracts import SearchRequest
from novel_lens.semantic import SemanticService
from novel_lens.semantic_contracts import SemanticBuild, SemanticCreate, SemanticGet, SemanticSearch


def create_work(database: Database) -> WorkOut:
    result = CatalogService(database).create(WorkCreate(request_id=uuid4(), name=str(uuid4())))
    assert isinstance(result.result, WorkOut)
    return result.result


def source(name: str = "正文") -> bytes:
    return f"\ufeff分部：{name}\r\n标题：重名章\n　风雨😀 \t\r\n第二段\n标题：重名章\n尾声".encode()


def test_append_names_bytes_and_catalog_replay(database: Database) -> None:
    catalog, reader = CatalogService(database), ReadingService(database)
    request = WorkCreate(request_id=uuid4(), name=str(uuid4()))
    creation = catalog.create(request)
    work = creation.result
    assert isinstance(work, WorkOut) and work.part_count == 0
    assert catalog.create(request).replayed
    importing = ImportService(database, 10000)
    key = uuid4()
    first = importing.import_source(source("火之晨曦"), key, work.id)
    first_sections = reader.list_sections(work.id, 100, None).items
    second = importing.import_source(source("悼亡者之瞳"), uuid4(), work.id)
    assert second.work.part_count == 2
    assert second.part.ordinal == 2
    assert [s.ordinal for s in reader.list_sections(work.id, 100, None).items] == [1, 2, 3, 4]
    assert reader.list_sections(work.id, 100, None, first.part.id).items == first_sections
    assert importing.import_source(source("火之晨曦"), key, work.id).work == first.work
    assert importing.result(key).part == first.part
    raw = reader.file(work.id, first.part.id)
    assert raw == source("火之晨曦")
    assert first.part.source_sha256 == sha256(raw).hexdigest()
    for section in first_sections:
        for paragraph in reader.list_paragraphs(work.id, section.id, 100, None, "full").items:
            assert isinstance(paragraph, ParagraphOut)
            pos = paragraph.source_position
            assert raw[pos.start_byte : pos.end_byte].decode() == paragraph.text
    rename = PartUpdate(
        request_id=uuid4(), work_id=work.id, part_id=first.part.id, expected_version=1, name="上篇"
    )
    assert catalog.update(rename).result.name == "上篇"
    assert catalog.update(rename).replayed
    assert reader.file(work.id, first.part.id) == raw
    assert [s.id for s in reader.list_sections(work.id, 100, None, first.part.id).items] == [
        s.id for s in first_sections
    ]
    with pytest.raises(ServiceError, match="版本"):
        catalog.update(rename.model_copy(update={"request_id": uuid4(), "name": "旧版本"}))
    with pytest.raises(ServiceError) as conflict:
        importing.import_source(source("不同字节"), key, work.id)
    assert conflict.value.code == "REQUEST_CONFLICT"
    with pytest.raises(ServiceError) as foreign:
        reader.file(create_work(database).id, first.part.id)
    assert foreign.value.code == "PART_NOT_FOUND"


def test_part_filters_and_fixed_analysis_targets(database: Database) -> None:
    work = create_work(database)
    importing, reader = ImportService(database, 10000), ReadingService(database)
    first = importing.import_source(source("上篇"), uuid4(), work.id)
    analysis = AnalysisService(database)

    def job(target: AnalysisTarget) -> UUID:
        result = analysis.create(
            AnalysisJobCreate(
                request_id=uuid4(),
                work_id=work.id,
                title="分析",
                goal="深读",
                target=target,
                recovery=Recovery(next_action="阅读"),
            )
        )
        assert isinstance(result.result, AnalysisJobOut)
        return result.result.id

    whole = job(AnalysisTarget(kind="whole_work"))
    partial = job(AnalysisTarget(kind="part", part_id=first.part.id))
    second = importing.import_source(source("下篇"), uuid4(), work.id)
    assert reader.get_work(work.id).paragraph_count == 6
    for identifier in (whole, partial):
        assert (
            analysis.get(AnalysisJobGet(work_id=work.id, job_id=identifier)).target_paragraph_count
            == 3
        )
    assert (
        analysis.get(AnalysisJobGet(work_id=work.id, job_id=partial)).target.part_id
        == first.part.id
    )
    search = SearchService(database)
    assert len(search.source(SearchRequest(work_id=work.id, terms=["风雨"])).items) == 2
    hits = search.source(
        SearchRequest(work_id=work.id, part_id=second.part.id, terms=["风雨"])
    ).items
    assert len(hits) == 1 and hits[0].part_name == "下篇" and hits[0].section_title == "重名章"
    page = reader.list_sections(work.id, 1, None, first.part.id)
    with pytest.raises(ServiceError) as cursor:
        reader.list_sections(work.id, 1, page.next_cursor, second.part.id)
    assert cursor.value.code == "INVALID_CURSOR"


def test_concurrent_append_and_failure_rollback(database: Database) -> None:
    work = create_work(database)
    importer = ImportService(database, 10000)
    key = uuid4()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: importer.import_source(source(), key, work.id), range(2)))
    assert results[0].part.id == results[1].part.id
    assert sum(not r.replayed for r in results) == 1
    # 不同部串行追加，不会抢占相同顺序或覆盖聚合统计。
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda n: importer.import_source(source(str(n)), uuid4(), work.id), range(2))
        )
    assert {r.part.ordinal for r in results} == {2, 3}
    before = ReadingService(database).get_work(work.id)
    with database.engine.begin() as conn:
        conn.execute(
            text("""CREATE FUNCTION reject_part_test() RETURNS trigger AS $$
            BEGIN IF NEW.text='reject-part-test' THEN RAISE EXCEPTION 'injected'; END IF;
            RETURN NEW; END; $$ LANGUAGE plpgsql;
            CREATE TRIGGER reject_part_test BEFORE INSERT ON paragraphs
            FOR EACH ROW EXECUTE FUNCTION reject_part_test();""")
        )
    failed = uuid4()
    try:
        with pytest.raises(DBAPIError):
            importer.import_source(
                "分部：失败\n标题：章\n正常\nreject-part-test".encode(), failed, work.id
            )
        assert ReadingService(database).get_work(work.id) == before
        with pytest.raises(ServiceError) as absent:
            importer.result(failed)
        assert absent.value.code == "IMPORT_NOT_COMMITTED"
    finally:
        with database.engine.begin() as conn:
            conn.execute(
                text(
                    "DROP TRIGGER reject_part_test ON paragraphs; DROP FUNCTION reject_part_test()"
                )
            )


def test_append_invalidates_semantic_source(database: Database) -> None:
    work = create_work(database)
    importer = ImportService(database, 10000)
    first = importer.import_source(source(), uuid4(), work.id)
    service = SemanticService(database, DeterministicModel())
    index = service.create(SemanticCreate(work_id=work.id, request_id=uuid4())).index_id
    for _ in range(10):
        result = service.build(SemanticBuild(work_id=work.id, index_id=index, request_id=uuid4()))
        if result.coverage.complete:
            break
    assert result.coverage.complete
    hits = service.search(SemanticSearch(work_id=work.id, part_id=first.part.id, query="风雨"))
    assert hits.items and all(h.part_id == first.part.id for h in hits.items)
    importer.import_source(source("续篇"), uuid4(), work.id)
    status = service.get(SemanticGet(work_id=work.id))
    assert status.active is not None and status.active.source_stale
    assert not status.active.coverage.complete
    with pytest.raises(ServiceError) as stale:
        service.search(SemanticSearch(work_id=work.id, query="风雨", allow_partial=True))
    assert stale.value.code == "INDEX_SOURCE_CHANGED"


def test_http_new_catalog_and_old_import_removed(client: TestClient) -> None:
    work = client.post("/works", json={"request_id": str(uuid4()), "name": str(uuid4())}).json()[
        "result"
    ]
    url = f"/works/{work['id']}/part-imports"
    key = str(uuid4())
    response = client.post(url, files={"file": ("p.txt", source())}, data={"request_id": key})
    assert response.status_code == 201, response.text
    part = response.json()["part"]
    assert client.get(f"/works/{work['id']}/parts/{part['id']}/file").content == source()
    assert client.get(f"/works/{work['id']}/sections").json()["items"][0]["part_id"] == part["id"]
    assert (
        client.post(
            "/work-imports", files={"file": ("p.txt", source())}, data={"request_id": key}
        ).status_code
        == 404
    )
    assert client.delete(f"/works/{work['id']}").status_code == 204
    assert client.get(f"/part-imports/{key}").status_code == 404


def test_cross_part_annotation_recall_and_import_index_lock(database: Database) -> None:
    """一条标注跨部引用；两类搜索筛选的是原文所属部，资产仍属于作品。"""
    from test_semantic import build
    from test_semantic_annotations import annotated_index, annotation, references

    from novel_lens.search_contracts import AnnotationSearchRequest

    work = create_work(database)
    importer = ImportService(database, 10000)
    first = importer.import_source(source("上篇"), uuid4(), work.id)
    second = importer.import_source(source("续篇"), uuid4(), work.id)
    asset = annotation(database, work.id, references(database, work.id))
    for part in (first.part, second.part):
        result = SearchService(database).annotations(
            AnnotationSearchRequest(work_id=work.id, part_id=part.id, terms=["说明"])
        )
        assert len(result.items) == 1
        hit = result.items[0]
        assert hit.annotation_id == asset.id and hit.part_id == part.id
        assert hit.part_name == part.name and hit.source_range_count == 6
        assert hit.first_source_range.section_id in {
            s.id for s in ReadingService(database).list_sections(work.id, 100, None, part.id).items
        }
    semantic = SemanticService(database, DeterministicModel())

    def finish(identifier: UUID) -> None:
        # 六处引用超过单批四项上限，按真实分批协议接续构建。
        for _ in range(10):
            if build(semantic, work.id, identifier).coverage.complete:
                return
        pytest.fail("小型跨部样例未完成构建")

    index = annotated_index(semantic, work.id)
    finish(index)
    results = semantic.search(
        SemanticSearch(work_id=work.id, part_id=second.part.id, kind="annotation", query="风雨")
    )
    assert results.items and all(p.part_id == second.part.id for p in results.items)
    # 构建锁已持有时，导入立即拒绝且不留下分部；释放后相同键可成功。
    key = uuid4()
    with semantic._locked(work.id, "annotation"):
        with pytest.raises(ServiceError) as busy:
            importer.import_source(source("尾篇"), key, work.id)
        assert busy.value.code == "WORK_BUSY"
    importer.import_source(source("尾篇"), key, work.id)
    with pytest.raises(ServiceError) as stale:
        semantic.build(SemanticBuild(work_id=work.id, index_id=index, request_id=uuid4()))
    assert stale.value.code == "INDEX_SOURCE_CHANGED"
    rebuilt = annotated_index(semantic, work.id)
    finish(rebuilt)
    active = semantic.get(SemanticGet(work_id=work.id, kind="annotation")).active
    assert active is not None and not active.source_stale
