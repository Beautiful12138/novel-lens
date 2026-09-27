"""页边标记的准确计数独立于标注列表分页。"""

from pathlib import Path
from uuid import uuid4

from starlette.testclient import TestClient
from test_preparation import import_book
from test_semantic import DeterministicModel

from novel_lens.asset_contracts import AnnotationCreate, AnnotationOut, AnnotationSetStatus
from novel_lens.assets import AssetService
from novel_lens.contracts import SourceRange
from novel_lens.database import Database
from novel_lens.preparation import PreparationService
from novel_lens.reading import ReadingService


def test_complete_counts_overlap_and_page_limits(
    database: Database, client: TestClient, tmp_path: Path
) -> None:
    prep = PreparationService(database, 1024 * 1024, DeterministicModel())
    book = import_book(prep, tmp_path, "\n".join(f"原文{i}" for i in range(201)))
    other = import_book(prep, tmp_path, "另一作品")
    reading, assets = ReadingService(database), AssetService(database)
    section = reading.list_sections(book.work.id, 1, None).items[0]
    page = reading.list_paragraphs(book.work.id, section.id, 200, None)
    last = reading.list_paragraphs(book.work.id, section.id, 200, page.next_cursor).items[0]

    def span(first: int, end: int) -> SourceRange:
        return SourceRange(
            work_id=book.work.id,
            section_id=section.id,
            start_paragraph_id=page.items[first - 1].id,
            end_paragraph_id=page.items[end - 1].id,
        )

    created = []
    for _ in range(14):
        result = assets.create_annotation(
            AnnotationCreate(
                request_id=uuid4(),
                work_id=book.work.id,
                source_ranges=[span(2, 4), span(3, 5)],
                note="交叠引用属于同一条标注",
            )
        ).result
        assert isinstance(result, AnnotationOut)
        created.append(result)
    assets.set_annotation_status(
        AnnotationSetStatus(
            request_id=uuid4(),
            work_id=book.work.id,
            annotation_id=created[0].id,
            expected_version=1,
            status="withdrawn",
        )
    )
    response = client.post("/assets/annotations/coverage", json=span(1, 6).model_dump(mode="json"))
    assert response.status_code == 200, response.text
    assert [item["annotation_count"] for item in response.json()["items"]] == [0, 13, 13, 13, 13, 0]
    assert [item["ordinal"] for item in response.json()["items"]] == list(range(1, 7))
    # 查询只含范围中间一页仍准确，不要求引用端点位于本页。
    middle = client.post("/assets/annotations/coverage", json=span(3, 3).model_dump(mode="json"))
    assert middle.json()["items"][0]["annotation_count"] == 13
    invalid = span(1, 6).model_dump(mode="json") | {"work_id": str(other.work.id)}
    assert client.post("/assets/annotations/coverage", json=invalid).status_code == 404
    oversized = span(1, 200).model_dump(mode="json") | {"end_paragraph_id": str(last.id)}
    failure = client.post("/assets/annotations/coverage", json=oversized)
    assert failure.status_code == 422 and failure.json()["code"] == "RANGE_TOO_LARGE"
