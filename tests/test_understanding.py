"""验证作品认识入口与连续阅读契约，不以文学评分代替数据行为。"""

from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from test_assets import assets as assets
from test_assets import source as source

from novel_lens.asset_contracts import (
    AnnotationBrowse,
    AnnotationCreate,
    AnnotationGet,
    AnnotationOut,
    AnnotationReference,
    AnnotationSetStatus,
    AnnotationUpdate,
)
from novel_lens.assets import AssetService
from novel_lens.contracts import SourceRange
from novel_lens.database import Database
from novel_lens.errors import ServiceError
from novel_lens.library import LibraryService
from novel_lens.query_views import annotation_view
from novel_lens.reading import ReadingService
from novel_lens.reference_contracts import LibraryBrowse, SourceRead


def content(work: UUID, refs: list[AnnotationReference]) -> AnnotationCreate:
    return AnnotationCreate(
        request_id=uuid4(),
        work_id=work,
        kind="comparison",
        title="闲谈中的关系变化",
        scope_note="仅比较已读的两章，尚未判断全书",
        note="第一处话题由动作打断，第二处叙述直接解释；需要分别回读比较。",
        references=refs,
    )


def test_read_target_keeps_context_and_each_evidence_role(
    assets: AssetService,
    source: tuple[UUID, list[SourceRange]],
    database: Database,
) -> None:
    work, ranges = source
    paragraphs = (
        ReadingService(database).list_paragraphs(work, ranges[0].section_id, 100, None).items
    )
    evidence = ranges[0].model_copy(
        update={
            "start_paragraph_id": paragraphs[1].id,
            "end_paragraph_id": paragraphs[1].id,
        }
    )
    request = content(
        work,
        [
            AnnotationReference(
                evidence_range=evidence, reading_range=ranges[0], role_note="动作打断处"
            ),
            AnnotationReference(evidence_range=ranges[1], role_note="直接解释的对照处"),
        ],
    )
    result = assets.create_annotation(request).result
    assert isinstance(result, AnnotationOut)
    assert assets.create_annotation(request).replayed
    assert result.references[1].reading_range == ranges[1]
    view = annotation_view(result, "compact").model_dump(mode="json")
    assert "source_ranges" not in view
    assert view["references"][0]["role_note"] == "动作打断处"
    library = LibraryService(database)
    read = library.read(SourceRead(**view["references"][0]["read_target"]))
    assert [item.text for item in read.items] == [p.text for p in paragraphs]
    detail = assets.browse_annotation_detail(AnnotationGet(work_id=work, annotation_id=result.id))
    assert detail.locations[0].start_ordinal == paragraphs[1].ordinal
    assert detail.locations[0].reading_start_ordinal == paragraphs[0].ordinal
    assert detail.locations[0].reading_end_ordinal == paragraphs[-1].ordinal
    page = library.browse(LibraryBrowse(view="annotations", work_id=work, kind="comparison"))
    assert page.annotations is not None
    assert page.annotations.items[0].title == request.title
    assert page.annotations.items[0].scope_note == request.scope_note
    observations = library.browse(
        LibraryBrowse(view="annotations", work_id=work, kind="observation")
    )
    assert observations.annotations is not None and observations.annotations.items == []


@pytest.mark.parametrize("invalid", ["narrow", "chapter", "work"])
def test_reading_range_failure_preserves_asset_and_receipt(
    assets: AssetService,
    source: tuple[UUID, list[SourceRange]],
    invalid: str,
) -> None:
    work, ranges = source
    request = content(work, [AnnotationReference(evidence_range=ranges[0], role_note="完整依据")])
    saved = assets.create_annotation(request).result
    assert isinstance(saved, AnnotationOut)
    reading = {
        "narrow": ranges[0].model_copy(update={"end_paragraph_id": ranges[0].start_paragraph_id}),
        "chapter": ranges[1],
        "work": ranges[0].model_copy(update={"work_id": uuid4()}),
    }[invalid]
    data = request.model_dump() | {
        "request_id": uuid4(),
        "annotation_id": saved.id,
        "expected_version": saved.version,
        "references": [
            AnnotationReference(
                evidence_range=ranges[0], reading_range=reading, role_note="无效上下文"
            )
        ],
    }
    with pytest.raises(ServiceError):
        assets.update_annotation(AnnotationUpdate(**data))
    assert assets.get_annotation(AnnotationGet(work_id=work, annotation_id=saved.id)) == saved
    # 同一请求键在失败后仍可用于修正后的原子写入。
    data["references"] = request.references
    revised = assets.update_annotation(AnnotationUpdate(**data)).result
    assert isinstance(revised, AnnotationOut) and revised.version == saved.version + 1


def test_kind_cursor_and_withdrawal(
    assets: AssetService, source: tuple[UUID, list[SourceRange]]
) -> None:
    work, ranges = source
    request = content(work, [AnnotationReference(evidence_range=ranges[0], role_note="依据")])
    ids = []
    for _ in range(2):
        result = assets.create_annotation(request.model_copy(update={"request_id": uuid4()})).result
        assert isinstance(result, AnnotationOut)
        ids.append(result.id)
    query = AnnotationBrowse(work_id=work, kind="comparison", limit=1)
    page = assets.browse_annotations(query)
    assert page.next_cursor
    with pytest.raises(ServiceError, match="游标"):
        assets.browse_annotations(
            query.model_copy(update={"kind": "observation", "cursor": page.next_cursor})
        )
    assets.set_annotation_status(
        AnnotationSetStatus(
            request_id=uuid4(),
            work_id=work,
            annotation_id=ids[0],
            expected_version=1,
            status="withdrawn",
        )
    )
    assert [item.id for item in assets.browse_annotations(query).items] == [ids[1]]
    assert (
        assets.browse_annotations(query.model_copy(update={"query": "闲谈"})).items[0].id == ids[1]
    )


def test_old_or_empty_content_is_rejected(source: tuple[UUID, list[SourceRange]]) -> None:
    work, ranges = source
    with pytest.raises(ValidationError):
        AnnotationCreate.model_validate(
            {"request_id": uuid4(), "work_id": work, "source_ranges": ranges}
        )
    request = content(work, [AnnotationReference(evidence_range=ranges[0], role_note="依据")])
    for field in ("note", "title", "scope_note"):
        with pytest.raises(ValidationError):
            AnnotationCreate.model_validate(request.model_dump() | {field: "   "})
    with pytest.raises(ValidationError):
        AnnotationCreate.model_validate(
            request.model_dump() | {"references": request.references * 2}
        )
