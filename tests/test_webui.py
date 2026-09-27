"""正式网页入口及其阅读路径；造数只使用测试框架的隔离 PostgreSQL 库。"""

from pathlib import Path
from uuid import uuid4

import pytest
from starlette.testclient import TestClient
from test_preparation import import_book
from test_semantic import DeterministicModel

from novel_lens.app import create_app
from novel_lens.asset_contracts import AnnotationCreate, AnnotationOut
from novel_lens.assets import AssetService
from novel_lens.config import Settings
from novel_lens.contracts import SourceRange
from novel_lens.database import Database
from novel_lens.preparation import PreparationService
from novel_lens.reading import ReadingService


def test_missing_build_is_actionable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """未构建前端不影响后台 API，网页访问明确说明恢复步骤。"""
    monkeypatch.setattr("novel_lens.web.WEB_ROOT", tmp_path)
    with TestClient(create_app(Settings.model_construct())) as client:
        assert client.get("/ui/").status_code == 503
        assert "npm run build" in client.get("/ui/").text
        assert client.get("/health").status_code == 200


def test_formal_ui_and_missing_build() -> None:
    """真实构建入口不覆盖 API；未构建时显式报错，不返回原型。"""
    from novel_lens.web import WEB_ROOT

    with TestClient(create_app(Settings.model_construct())) as client:
        response = client.get("/ui/")
        if (WEB_ROOT / "index.html").is_file():
            assert response.status_code == 200
            assert 'src="/ui/assets/' in response.text
            assert client.get("/ui/.env").status_code == 404
        else:
            assert response.status_code == 503
            assert "npm run build" in response.text
        assert client.get("/health").json() == {"status": "ok"}


def test_web_reading_pages_and_cross_work_reference(
    database: Database, client: TestClient, tmp_path: Path
) -> None:
    """原文跨页保真、多处引用保留；同名章节仍通过作品 ID 隔离。"""
    preparation = PreparationService(database, 1024 * 1024, DeterministicModel())
    book = import_book(preparation, tmp_path, "\n".join(f"正文第{i}段。" for i in range(85)))
    other = import_book(preparation, tmp_path, "不能串入另一作品的原文。")
    reading = ReadingService(database)
    section = reading.list_sections(book.work.id, 100, None).items[0]
    other_section = reading.list_sections(other.work.id, 100, None).items[0]
    metadata = client.get(f"/works/{book.work.id}/sections/{section.id}")
    assert metadata.status_code == 200
    assert metadata.json()["part_name"] == "第一卷"
    assert client.get(f"/works/{book.work.id}/sections/{other_section.id}").status_code == 404
    body = {"work_id": str(book.work.id), "section_id": str(section.id), "limit": 40}
    first = client.post("/source/read", json=body).json()
    second = client.post("/source/read", json=body | {"cursor": first["next_cursor"]}).json()
    assert [p["ordinal"] for p in first["items"]] == list(range(1, 41))
    assert second["items"][0]["text"] == "正文第40段。"
    span = SourceRange(
        work_id=book.work.id,
        section_id=section.id,
        start_paragraph_id=first["items"][-1]["id"],
        end_paragraph_id=second["items"][0]["id"],
    )
    tail = SourceRange.model_validate(
        span.model_dump()
        | {
            "start_paragraph_id": second["items"][-2]["id"],
            "end_paragraph_id": second["items"][-1]["id"],
        }
    )
    mark = (
        AssetService(database)
        .create_annotation(
            AnnotationCreate(
                request_id=uuid4(),
                work_id=book.work.id,
                source_ranges=[span, tail],
                note="完整说明。" * 100,
            )
        )
        .result
    )
    assert isinstance(mark, AnnotationOut)
    listed = client.post(
        "/library/browse",
        json={
            "view": "annotations",
            "work_id": str(book.work.id),
            "status": "active",
            "source_range": {
                "work_id": str(book.work.id),
                "section_id": str(section.id),
                **first["actual_range"],
            },
        },
    ).json()["annotations"]["items"]
    assert [item["id"] for item in listed] == [str(mark.id)]
    detail = client.post(
        "/library/browse",
        json={
            "view": "annotations",
            "work_id": str(book.work.id),
            "annotation_id": str(mark.id),
        },
    ).json()["annotation"]
    assert len(detail["source_ranges"]) == 2
    assert detail["note"] == mark.note
    wrong_work = client.post("/source/read", json=body | {"work_id": str(other.work.id)})
    assert wrong_work.status_code == 404
