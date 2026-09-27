"""跨作品资产目录的真实数据库验收；数据仅创建在隔离测试库。"""

from pathlib import Path
from typing import Any
from uuid import uuid4

from starlette.testclient import TestClient
from test_preparation import import_book
from test_semantic import DeterministicModel

from novel_lens.asset_contracts import (
    AnnotationCreate,
    AnnotationOut,
    AnnotationSetStatus,
    TagCreate,
    TagOut,
)
from novel_lens.assets import AssetService
from novel_lens.contracts import SourceRange
from novel_lens.database import Database
from novel_lens.preparation import PreparationService
from novel_lens.reading import ReadingService
from novel_lens.work_management import WorkManagementService


def test_cross_work_assets_filters_and_cursor(
    client: TestClient, database: Database, tmp_path: Path
) -> None:
    """第二处引用也参与章节筛选，状态/标签/搜索游标不能互换。"""
    prep = PreparationService(database, 1024 * 1024, DeterministicModel())
    books = [
        import_book(prep, tmp_path, "第一章正文。\n标题：第二章\n第二章正文。"),
        import_book(prep, tmp_path, "另一部作品。"),
        import_book(prep, tmp_path, "屏蔽作品。"),
    ]
    assets = AssetService(database)
    tag = assets.create_tag(
        TagCreate(
            request_id=uuid4(),
            namespace=str(uuid4()),
            name="共同标签",
            description="定位依据",
            aliases=["别名"],
        )
    ).result
    assert isinstance(tag, TagOut)
    reading = ReadingService(database)
    scopes: list[list[SourceRange]] = []
    for book in books:
        items = []
        for section in reading.list_sections(book.work.id, 100, None).items:
            paragraph = reading.list_paragraphs(book.work.id, section.id, 1, None).items[0]
            items.append(
                SourceRange(
                    work_id=book.work.id,
                    section_id=section.id,
                    start_paragraph_id=paragraph.id,
                    end_paragraph_id=paragraph.id,
                )
            )
        scopes.append(items)
    marks = []
    for book, spans, note in zip(
        books, scopes, ["字面100%_证据", "字面100XY证据", "隐藏"], strict=True
    ):
        mark = assets.create_annotation(
            AnnotationCreate(
                request_id=uuid4(),
                work_id=book.work.id,
                source_ranges=spans,
                tag_ids=[tag.id],
                note=note,
            )
        ).result
        assert isinstance(mark, AnnotationOut)
        marks.append(mark)
    withdrawn = assets.create_annotation(
        AnnotationCreate(
            request_id=uuid4(),
            work_id=books[0].work.id,
            source_ranges=scopes[0],
            tag_ids=[tag.id],
            note="撤回说明",
        )
    ).result
    assert isinstance(withdrawn, AnnotationOut)
    assets.set_annotation_status(
        AnnotationSetStatus(
            request_id=uuid4(),
            work_id=books[0].work.id,
            annotation_id=withdrawn.id,
            expected_version=withdrawn.version,
            status="withdrawn",
        )
    )
    WorkManagementService(database).set_visibility(books[2].work.id, "hidden")
    body = {"tag_ids": [str(tag.id)], "limit": 1}
    first = client.post("/assets/annotations/browse", json=body)
    assert first.status_code == 200, first.text
    page = first.json()
    assert page["items"][0]["id"] == str(marks[0].id)
    assert page["items"][0]["work_name"] == books[0].work.name
    assert page["items"][0]["section_title"] == "第一章"
    second = client.post(
        "/assets/annotations/browse", json=body | {"cursor": page["next_cursor"]}
    ).json()
    assert [item["id"] for item in second["items"]] == [str(marks[1].id)]
    assert second["next_cursor"] is None
    variations: list[dict[str, Any]] = [
        {"query": "字面"},
        {"status": None},
        {"work_id": str(books[0].work.id)},
    ]
    for changes in variations:
        wrong = client.post(
            "/assets/annotations/browse", json=body | changes | {"cursor": page["next_cursor"]}
        )
        assert wrong.status_code == 422
        assert wrong.json()["code"] == "INVALID_CURSOR"
    literal = client.post("/assets/annotations/browse", json=body | {"query": "100%_"}).json()
    assert [item["id"] for item in literal["items"]] == [str(marks[0].id)]
    chapter_filter = {"work_id": str(books[0].work.id), "section_id": str(scopes[0][1].section_id)}
    chapters = client.post("/assets/annotations/browse", json=body | chapter_filter).json()
    assert [item["id"] for item in chapters["items"]] == [str(marks[0].id)]
    all_status = client.post(
        "/assets/annotations/browse", json=body | {"status": None, "limit": 100}
    ).json()
    assert {item["id"] for item in all_status["items"]} == {
        str(m.id) for m in [*marks[:2], withdrawn]
    }
    hidden = client.post("/assets/annotations/browse", json={"work_id": str(books[2].work.id)})
    assert hidden.status_code == 409 and hidden.json()["code"] == "WORK_HIDDEN"
    wrong_section = client.post(
        "/assets/annotations/browse", json=chapter_filter | {"work_id": str(books[1].work.id)}
    )
    assert wrong_section.status_code == 404
    assert (
        client.post(
            "/assets/annotations/browse", json={"section_id": str(scopes[0][0].section_id)}
        ).status_code
        == 422
    )

    detail_body = {"work_id": str(books[0].work.id), "annotation_id": str(marks[0].id)}
    detail = client.post("/assets/annotations/detail", json=detail_body)
    assert detail.status_code == 200
    assert [r["section_title"] for r in detail.json()["locations"]] == ["第一章", "第二章"]
    assert all(r["start_ordinal"] == r["end_ordinal"] == 1 for r in detail.json()["locations"])
    assert detail.json()["tags"][0]["id"] == str(tag.id)
    assert (
        client.post(
            "/assets/annotations/detail", json=detail_body | {"work_id": str(books[1].work.id)}
        ).status_code
        == 404
    )
