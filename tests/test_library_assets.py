"""在真实 PostgreSQL 上验证默认目录查旧，不以索引或文学模型代替资产筛选。"""

import asyncio
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest
from mcp import Client
from pydantic import ValidationError
from test_live_http import running_server
from test_mcp_http import call
from test_preparation import batch_for, import_book
from test_semantic import DeterministicModel

from novel_lens.asset_contracts import (
    AnnotationCreate,
    AnnotationOut,
    AnnotationSetStatus,
    TagCreate,
    TagOut,
    TagUpdate,
)
from novel_lens.assets import AssetService
from novel_lens.contracts import SourceRange
from novel_lens.database import Database
from novel_lens.errors import ServiceError
from novel_lens.library import LibraryService
from novel_lens.preparation import PreparationService
from novel_lens.reference_contracts import LibraryBrowse


def test_library_tag_lookup_and_annotation_filters(database: Database, tmp_path: Path) -> None:
    model = DeterministicModel()
    preparation = PreparationService(database, 1024 * 1024, model)
    library, assets = LibraryService(database, model), AssetService(database)
    book = import_book(preparation, tmp_path, "第一处证据。\n第二处证据。")
    other = import_book(preparation, tmp_path, "其他作品的证据。")
    span = batch_for(preparation, book).source_range
    other_span = batch_for(preparation, other).source_range
    namespace = str(uuid4())
    created_tags = []
    for name, aliases in [("陈墨瞳", ["诺诺_100%"]), ("另一观察", ["诺诺X100P"])]:
        tag = assets.create_tag(
            TagCreate(
                request_id=uuid4(),
                namespace=namespace,
                name=name,
                description="检索线索",
                aliases=aliases,
            )
        ).result
        assert isinstance(tag, TagOut)
        created_tags.append(tag)
    first, second = created_tags
    page = library.browse(LibraryBrowse(view="tags", namespace=namespace, query="诺诺_"))
    assert page.tag_page is not None
    assert [t.id for t in page.tag_page.items] == [first.id]  # 下划线不是 SQL 通配符。
    assert page.tag_page.items[0].full_name == f"{namespace}/陈墨瞳"
    for query in ("陈墨瞳", f"{namespace}/陈", "100%"):
        found = library.browse(LibraryBrowse(view="tags", query=query)).tag_page
        assert found is not None and [t.id for t in found.items] == [first.id]
    empty = library.browse(LibraryBrowse(view="tags", namespace=namespace, query="不存在")).tag_page
    assert empty is not None and not empty.items
    first_page = library.browse(LibraryBrowse(view="tags", namespace=namespace, limit=1)).tag_page
    assert first_page is not None and first_page.next_cursor
    next_page = library.browse(
        LibraryBrowse(
            view="tags", namespace=namespace, limit=1, cursor=first_page.next_cursor, format="full"
        )
    ).tag_page
    assert next_page is not None and [t.id for t in next_page.items] == [second.id]
    with pytest.raises(ServiceError, match="游标"):
        library.browse(
            LibraryBrowse(
                view="tags", namespace=namespace, query="检索", cursor=first_page.next_cursor
            )
        )

    marked: list[AnnotationOut] = []
    # 同标签不同观察允许并存；重复标签输入规范化，不改变交集语义。
    last = SourceRange(**(span.model_dump() | {"start_paragraph_id": span.end_paragraph_id}))
    for scope, ids, refs in [
        (span, [first.id, second.id], [span]),
        (span, [first.id], [last]),
        (span, [], [span]),
        (other_span, [first.id], [other_span]),
    ]:
        result = assets.create_annotation(
            AnnotationCreate(
                request_id=uuid4(),
                work_id=scope.work_id,
                source_ranges=refs,
                tag_ids=ids,
                note="有依据的观察",
            )
        ).result
        assert isinstance(result, AnnotationOut)
        marked.append(result)
    assets.set_annotation_status(
        AnnotationSetStatus(
            request_id=uuid4(),
            work_id=book.work.id,
            annotation_id=marked[1].id,
            expected_version=1,
            status="withdrawn",
        )
    )
    request = LibraryBrowse(view="annotations", work_id=book.work.id, tag_ids=[first.id], limit=1)
    results = library.browse(request).annotations
    assert results is not None and results.next_cursor
    assert results.items[0].id == marked[0].id
    assert {t.full_name for t in results.items[0].tags} == {first.full_name, second.full_name}
    full = library.browse(request.model_copy(update={"format": "full"})).annotations
    assert full is not None and full.items[0].tags == results.items[0].tags
    next_annotations = library.browse(
        request.model_copy(update={"cursor": results.next_cursor})
    ).annotations
    assert next_annotations is not None and [m.id for m in next_annotations.items] == [marked[1].id]
    with pytest.raises(ServiceError, match="游标"):
        library.browse(
            request.model_copy(update={"cursor": results.next_cursor, "status": "active"})
        )
    active = library.browse(request.model_copy(update={"status": "active"})).annotations
    assert active is not None and [m.id for m in active.items] == [marked[0].id]
    intersection = library.browse(
        LibraryBrowse(
            view="annotations",
            work_id=book.work.id,
            tag_ids=[first.id, second.id, first.id],
            source_range=last,
            status="active",
        )
    ).annotations
    assert intersection is not None and [m.id for m in intersection.items] == [marked[0].id]
    withdrawn = library.browse(
        LibraryBrowse(
            view="annotations", work_id=book.work.id, source_range=last, status="withdrawn"
        )
    ).annotations
    assert withdrawn is not None and [m.id for m in withdrawn.items] == [marked[1].id]
    first_paragraph = span.model_copy(update={"end_paragraph_id": span.start_paragraph_id})
    no_match = library.browse(
        LibraryBrowse(
            view="annotations",
            work_id=book.work.id,
            source_range=first_paragraph,
            status="withdrawn",
        )
    ).annotations
    assert no_match is not None and not no_match.items
    untagged = library.browse(LibraryBrowse(view="annotations", work_id=book.work.id)).annotations
    assert untagged is not None
    assert next(m for m in untagged.items if m.id == marked[2].id).tags == []
    with pytest.raises(ServiceError) as cross:
        library.browse(
            LibraryBrowse(view="annotations", work_id=book.work.id, source_range=other_span)
        )
    assert cross.value.code == "INVALID_RANGE"
    with pytest.raises(ServiceError) as missing:
        library.browse(LibraryBrowse(view="annotations", work_id=book.work.id, tag_ids=[uuid4()]))
    assert missing.value.code == "TAG_NOT_FOUND"
    assets.update_tag(
        TagUpdate(
            request_id=uuid4(),
            tag_id=first.id,
            expected_version=1,
            name="新称呼",
            description=first.description,
            aliases=first.aliases,
        )
    )
    renamed = library.browse(request).annotations
    assert renamed is not None
    assert (
        next(t for t in renamed.items[0].tags if t.id == first.id).full_name
        == f"{namespace}/新称呼"
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"view": "tags", "work_id": str(uuid4())},
        {"view": "tags", "query": "  "},
        {"view": "tags", "status": None},
        {"view": "works", "tag_ids": []},
        {"view": "annotations", "work_id": str(uuid4()), "query": "人物"},
        {
            "view": "annotations",
            "work_id": str(uuid4()),
            "annotation_id": str(uuid4()),
            "tag_ids": [],
        },
    ],
)
def test_library_rejects_filters_on_wrong_view(payload: dict[str, Any]) -> None:
    """拒绝会被静默忽略的筛选，避免 AI 把未筛选结果当作命中。"""
    with pytest.raises(ValidationError):
        LibraryBrowse.model_validate(payload)


def test_default_mcp_asset_lookup_matches_http(postgres_url: str, tmp_path: Path) -> None:
    source = tmp_path / "资产查询.txt"
    source.write_text("分部：正文\n标题：一\n他停在门前。\n随后推开门。", encoding="utf-8")

    async def exercise(rest: httpx.Client) -> None:
        async with Client(str(rest.base_url).rstrip("/") + "/mcp") as mcp:
            definitions = (await mcp.list_tools()).tools
            assert len(definitions) == 8
            browse = next(t for t in definitions if t.name == "library_browse")
            assert {
                "namespace",
                "query",
                "tag_ids",
                "source_range",
                "status",
            } <= browse.input_schema["properties"].keys()
            imported = await call(
                mcp,
                "prepare_import",
                dict(request_id=str(uuid4()), name=str(uuid4()), file_path=str(source)),
            )
            identity = dict(work_id=imported["work"]["id"], job_id=imported["job"]["id"])
            current = await call(mcp, "prepare_status", identity)
            scope = current["remaining"]["items"][0]["source_range"]
            namespace = str(uuid4())
            saved = await call(
                mcp,
                "prepare_batch",
                dict(
                    **identity,
                    request_id=str(uuid4()),
                    expected_version=current["job"]["version"],
                    source_range=scope,
                    marks=[
                        dict(
                            source_ranges=[scope],
                            note="行动承接",
                            tags=[
                                dict(namespace=namespace, name="动作", description="具体行动的接续")
                            ],
                        )
                    ],
                    recovery={"next_action": "核对已有观察"},
                    outcome_note="实际读完并保存",
                ),
            )
            tag_query = dict(view="tags", namespace=namespace, query="动作")
            tags = await call(mcp, "library_browse", tag_query)
            assert tags == rest.post("/library/browse", json=tag_query).json()
            tag_id = tags["tag_page"]["items"][0]["id"]
            lookup = dict(
                view="annotations",
                work_id=identity["work_id"],
                tag_ids=[tag_id],
                status="active",
                source_range=scope,
            )
            page = await call(mcp, "library_browse", lookup)
            assert page == rest.post("/library/browse", json=lookup).json()
            assert page["annotations"]["items"][0]["tags"] == [
                dict(id=tag_id, full_name=f"{namespace}/动作")
            ]
            assert page["annotations"]["items"][0]["id"] == saved["annotations"][0]["id"]
            detail = await call(
                mcp,
                "library_browse",
                dict(
                    view="annotations",
                    work_id=identity["work_id"],
                    annotation_id=saved["annotations"][0]["id"],
                    format="full",
                ),
            )
            assert detail["annotation"]["source_ranges"] == [scope]
            assert detail["tags"][0]["description"] == "具体行动的接续"

    with running_server(postgres_url, tmp_path, "library-assets", mcp_profile="business") as rest:
        asyncio.run(exercise(rest))
