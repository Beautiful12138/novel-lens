"""真实 PGroonga 上验证搜索语义、定位、分页与修订可见性。"""

from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Connection, event, text, update
from starlette.testclient import TestClient
from test_assets import create_annotation, revision

from novel_lens.asset_contracts import AnnotationOut
from novel_lens.assets import AssetService
from novel_lens.contracts import SourceRange
from novel_lens.database import Database
from novel_lens.errors import ServiceError
from novel_lens.importing import ImportService
from novel_lens.reading import ReadingService
from novel_lens.schema import annotations
from novel_lens.search import SearchService
from novel_lens.search_contracts import SearchRequest

TEXTS = [
    "张三看见月光。",
    "她看见月亮，光很暗。",
    "ＡＢＣ abc abcd",
    "a OR b",
    "只有 a",
    "惊！问？句。",
    "风",
    "ﬃ月光",
    "张三独坐。",
    "x%_\\y",
    "只有 abcd",
    "前" * 1000 + "月光😀" + "后" * 300,
]


def import_search_source(database: Database) -> tuple[UUID, list[SourceRange]]:
    """跨两个章节制造相同段落序号，返回可回读的真实坐标。"""
    data = (f"书名：{uuid4()}\n标题：甲\n" + "\n".join(TEXTS) + "\n标题：乙\n月光").encode()
    work = ImportService(database, 100000).import_source(data, uuid4()).work
    reading = ReadingService(database)
    refs = []
    for section in reading.list_sections(work.id, 100, None).items:
        for paragraph in reading.list_paragraphs(work.id, section.id, 100, None).items:
            refs.append(
                SourceRange(
                    work_id=work.id,
                    section_id=section.id,
                    start_paragraph_id=paragraph.id,
                    end_paragraph_id=paragraph.id,
                )
            )
    return work.id, refs


@pytest.fixture
def source(database: Database) -> tuple[UUID, list[SourceRange]]:
    return import_search_source(database)


@pytest.mark.parametrize(
    ("terms", "expected"),
    [
        (["月"], [0, 1, 7, 11, 12]),
        (["月光"], [0, 7, 11, 12]),
        (["张三"], [0, 8]),
        (["ABC"], [2]),
        (["bc"], []),
        (["！"], [5]),
        (["惊！"], [5]),
        (["a OR b"], [3]),
        (["ffi"], [7]),
        (["风"], [6]),
        (["没有的词"], []),
        (["x%_\\y"], [9]),
    ],
)
def test_source_terms(
    database: Database,
    source: tuple[UUID, list[SourceRange]],
    terms: list[str],
    expected: list[int],
) -> None:
    page = SearchService(database).source(SearchRequest(work_id=source[0], terms=terms))
    assert [hit.source_range for hit in page.items] == [source[1][i] for i in expected]
    for hit in page.items:
        full = (TEXTS + ["月光"])[source[1].index(hit.source_range)]
        excerpt = hit.excerpt
        assert excerpt.text == full[excerpt.character_start : excerpt.character_start + 200]
        assert len(excerpt.text) <= 200
        assert excerpt.truncated_before == (excerpt.character_start > 0)
        assert excerpt.truncated_after == (excerpt.character_start + len(excerpt.text) < len(full))
    if terms == ["a OR b"]:
        assert not page.items[0].excerpt.match_located
    if terms == ["月光"]:
        assert page.items[-2].excerpt.character_start == 960
        assert "月光😀" in page.items[-2].excerpt.text


def test_all_any_scope_and_keyset(
    database: Database,
    source: tuple[UUID, list[SourceRange]],
) -> None:
    search = SearchService(database)
    other = import_search_source(database)
    request = SearchRequest(work_id=source[0], terms=[" 月光 ", "张三", "月光"])
    assert [hit.source_range for hit in search.source(request).items] == source[1][:1]
    request = request.model_copy(update={"match": "any", "limit": 1})
    seen: list[SourceRange] = []
    first_cursor = None
    while True:
        page = search.source(request)
        seen.extend(hit.source_range for hit in page.items)
        if not page.next_cursor:
            break
        first_cursor = first_cursor or page.next_cursor
        request = request.model_copy(update={"cursor": page.next_cursor})
    assert seen == [source[1][i] for i in [0, 7, 8, 11, 12]]
    invalid_changes: list[dict[str, Any]] = [
        dict(work_id=other[0]),
        dict(match="all"),
        dict(terms=["月"]),
    ]
    for changes in invalid_changes:
        with pytest.raises(ServiceError) as error:
            search.source(request.model_copy(update={"cursor": first_cursor} | changes))
        assert error.value.code == "INVALID_CURSOR"
    with pytest.raises(ServiceError) as error:
        search.annotations(request.model_copy(update={"cursor": first_cursor}))
    assert error.value.code == "INVALID_CURSOR"
    with pytest.raises(ServiceError) as error:
        search.source(SearchRequest(work_id=uuid4(), terms=["月"]))
    assert error.value.code == "WORK_NOT_FOUND"


def test_note_update_null_rollback_and_same_time_pagination(
    database: Database,
    source: tuple[UUID, list[SourceRange]],
) -> None:
    assets, search = AssetService(database), SearchService(database)
    source = (source[0], [source[1][0], source[1][-1]])
    first = create_annotation(assets, source, note="留白导致悬念")
    second = create_annotation(assets, source, note="悬念使用对话")
    create_annotation(assets, import_search_source(database), note="悬念")
    with database.engine.begin() as c:
        c.execute(
            update(annotations)
            .where(annotations.c.id == second.id)
            .values(created_at=first.created_at)
        )
    request = SearchRequest(work_id=source[0], terms=["悬念"], limit=1)
    page = search.annotations(request)
    following = search.annotations(request.model_copy(update={"cursor": page.next_cursor}))
    hits = page.items + following.items
    assert [h.annotation_id for h in hits] == sorted([first.id, second.id])
    assert all(h.first_source_range == source[1][0] and h.source_range_count == 2 for h in hits)
    assert not following.next_cursor
    assert (
        len(
            search.annotations(
                SearchRequest(work_id=source[0], terms=["留白", "对话"], match="any")
            ).items
        )
        == 2
    )
    assert search.annotations(SearchRequest(work_id=source[0], terms=["留白", "对话"])).items == []
    # 回滚不遗留索引命中，随后提交的业务修订立即可见。
    with database.engine.connect() as c:
        c.execute(update(annotations).where(annotations.c.id == first.id).values(note="回滚词"))
        c.rollback()
    assert not search.annotations(SearchRequest(work_id=source[0], terms=["回滚词"])).items
    changed = assets.update_annotation(revision(first, note="修订新词")).result
    assert isinstance(changed, AnnotationOut)
    assert changed.version == 2
    assert (
        search.annotations(SearchRequest(work_id=source[0], terms=["修订新词"])).items[0].version
        == 2
    )
    assets.update_annotation(revision(second, note=None))
    assert search.annotations(request).items == []


@pytest.mark.parametrize("indexed", [True, False])
def test_index_and_sequential_semantics(
    database: Database,
    source: tuple[UUID, list[SourceRange]],
    indexed: bool,
) -> None:
    def configure(c: Connection) -> None:
        c.execute(text(f"SET LOCAL enable_seqscan={'off' if indexed else 'on'}"))
        c.execute(text(f"SET LOCAL enable_indexscan={'on' if indexed else 'off'}"))
        c.execute(text(f"SET LOCAL enable_bitmapscan={'on' if indexed else 'off'}"))

    event.listen(database.engine, "begin", configure)
    try:
        assets = AssetService(database)
        for i in [0, 2, 5, 7]:
            create_annotation(assets, (source[0], [source[1][i]]), note=TEXTS[i])
        # 候选全集不变，并实际检查两种执行计划确已选中。
        with database.engine.begin() as c:
            c.execute(text("SET LOCAL pgroonga.match_escalation_threshold=-1"))
            plan = c.execute(
                text("""EXPLAIN (FORMAT JSON) SELECT id FROM paragraphs
                WHERE text &@ ('月', NULL, 'ix_paragraphs_text_search')
                ::pgroonga_full_text_search_condition""")
            ).scalar_one()[0]["Plan"]
            assert (plan["Node Type"] != "Seq Scan") == indexed
        for term, expected in [("月", [0, 1, 7, 11, 12]), ("bc", []), ("！", [5])]:
            hits = SearchService(database).source(SearchRequest(work_id=source[0], terms=[term]))
            assert [h.source_range for h in hits.items] == [source[1][i] for i in expected]
        for mode, expected in [("all", [0]), ("any", [0, 7, 8, 11, 12])]:
            request = SearchRequest(work_id=source[0], terms=["月光", "张三"], match=mode)  # type: ignore[arg-type]
            assert [h.source_range for h in SearchService(database).source(request).items] == [
                source[1][i] for i in expected
            ]
        for terms, expected_count in [(["bc", "未命中"], 0), (["月", "！"], 3)]:
            request = SearchRequest(work_id=source[0], terms=terms, match="any")
            assert len(SearchService(database).annotations(request).items) == expected_count
    finally:
        event.remove(database.engine, "begin", configure)


INVALID_SEARCH: list[dict[str, Any]] = [
    {"terms": []},
    {"terms": [" "]},
    {"terms": ["x\x00"]},
    {"terms": ["x" * 129]},
    {"terms": ["x"] * 9},
    {"terms": [5]},
    {"terms": "x"},
    {"match": "AND"},
    {"limit": True},
    {"limit": "2"},
    {"limit": 0},
    {"limit": 101},
    {"cursor": 42},
    {"unknown-secret": "secret"},
]


def test_rest_validation_and_result(
    client: TestClient,
    database: Database,
    source: tuple[UUID, list[SourceRange]],
) -> None:
    arguments = dict(work_id=str(source[0]), terms=["月"])
    for path in ["/source/search", "/annotations/search"]:
        for change in INVALID_SEARCH:
            response = client.post(path, json=arguments | change)
            assert response.status_code == 422, response.text
            assert response.json()["code"] == "INVALID_INPUT"
            assert "secret" not in response.text
    result = client.post("/source/search", json=arguments)
    assert result.json() == SearchService(database).source(
        SearchRequest.model_validate(arguments)
    ).model_dump(mode="json")
