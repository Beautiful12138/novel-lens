"""真实 PostgreSQL 验证四路召回、当前线索、范围隔离和原文续读。"""

from pathlib import Path
from uuid import uuid4

import pytest
from test_preparation import batch_for, drain, import_book
from test_semantic import DeterministicModel

from novel_lens.asset_contracts import AnnotationSetStatus, TagUpdate
from novel_lens.assets import AssetService
from novel_lens.database import Database
from novel_lens.errors import ServiceError
from novel_lens.library import LibraryService
from novel_lens.preparation import PreparationService
from novel_lens.reference import ReferenceService, fuse
from novel_lens.reference_contracts import (
    LibraryBrowse,
    MarkInput,
    PrepareImport,
    ReferenceQuery,
    ReferenceResult,
    ReferenceScope,
    SourceRead,
    TagInput,
)
from novel_lens.work_management import WorkManagementService


def test_bridge_candidate_eliminates_all_contained_ranges() -> None:
    """第三个候选桥接两个旧范围后，不能让被包含结果继续占用 top-k。"""
    section, mark1, mark2 = uuid4(), uuid4(), uuid4()
    endpoints = {i: uuid4() for i in (1, 5, 9, 20, 21)}
    rows = [
        dict(
            section_id=section,
            section_ordinal=1,
            start_ordinal=start,
            end_ordinal=end,
            start_paragraph_id=endpoints[start],
            end_paragraph_id=endpoints[end],
            annotation_id=mark,
        )
        for start, end, mark in [(1, 5, mark1), (5, 9, mark2), (1, 9, None), (20, 21, None)]
    ]
    result = fuse({"clue_semantic": rows}, 2)
    assert [(r["start_ordinal"], r["end_ordinal"]) for r in result] == [(1, 9), (20, 21)]
    assert result[0]["annotations"] == {mark1, mark2}
    assert result[0]["start_paragraph_id"] == endpoints[1]
    assert result[0]["end_paragraph_id"] == endpoints[9]
    assert result[0]["ranks"] == {"clue_semantic": 1}


def test_four_channels_current_clues_and_unmarked_source(
    database: Database, tmp_path: Path
) -> None:
    model = DeterministicModel()
    preparation = PreparationService(database, 1024 * 1024, model)
    query = ReferenceService(database, model)
    book = import_book(
        preparation, tmp_path, "他犹豫着放下杯子。\n标题：第二章\n未标记的紫色风筝。"
    )
    batch = batch_for(preparation, book)
    mark = MarkInput(
        source_ranges=[batch.source_range],
        note="迟疑通过动作表现",
        tags=[TagInput(namespace="写法", name="动作迟疑", description="可核对的动作线索")],
    )
    saved = preparation.batch(batch.model_copy(update={"marks": [mark]}))
    with pytest.raises(ServiceError) as pending:
        query.query(
            ReferenceQuery(
                format="full", scope=[ReferenceScope(work_id=book.work.id)], query="犹豫"
            )
        )
    assert pending.value.code == "REFERENCE_NOT_READY"
    drain(preparation, book, model)
    before = len(model.calls)
    result = query.query(
        ReferenceQuery(
            format="full",
            scope=[ReferenceScope(work_id=book.work.id)],
            query="犹豫",
            terms=["动作迟疑"],
        )
    )
    assert isinstance(result, ReferenceResult)
    assert len(model.calls) == before + 1  # 一次查询共用一次推理。
    first = result.items[0]
    assert set(first.channels) == {
        "source_semantic",
        "source_keyword",
        "clue_semantic",
        "clue_keyword",
    }
    assert first.annotation_ids == [saved.annotations[0].id]
    assert first.excerpt == "他犹豫着放下杯子。"
    raw = query.query(
        ReferenceQuery(
            format="full", scope=[ReferenceScope(work_id=book.work.id)], query="紫色风筝"
        )
    )
    assert isinstance(raw, ReferenceResult)
    # 常量向量替身不证明相关性名次；这里验证未标记原文确实参与并保留字面命中。
    unmarked = next(h for h in raw.items if h.excerpt == "未标记的紫色风筝。")
    assert unmarked.annotation_ids == [] and "source_keyword" in unmarked.channels
    assert raw.diagnostics["works"][0]["scope_coverage"]["complete"]
    assets = AssetService(database)
    tag = assets.get_tag(saved.annotations[0].tag_ids[0])
    assets.update_tag(
        TagUpdate(
            request_id=uuid4(),
            tag_id=tag.id,
            expected_version=tag.version,
            name=tag.name,
            description="具体动作承接未出口的选择",
            aliases=["话没说完"],
        )
    )
    with pytest.raises(ServiceError) as changed_tag:
        query.query(ReferenceQuery(search_id=result.search_id))
    assert changed_tag.value.code == "REFERENCE_SEARCH_STALE"
    alias_hit = query.query(
        ReferenceQuery(
            format="full", scope=[ReferenceScope(work_id=book.work.id)], query="话没说完"
        )
    )
    assert isinstance(alias_hit, ReferenceResult)
    alias_match = next(h for h in alias_hit.items if h.annotation_ids)
    assert "clue_keyword" in alias_match.channels and "clue_semantic" not in alias_match.channels
    definition = query.query(
        ReferenceQuery(
            format="full", scope=[ReferenceScope(work_id=book.work.id)], query="未出口的选择"
        )
    )
    assert isinstance(definition, ReferenceResult)
    assert any("clue_keyword" in h.channels and h.annotation_ids for h in definition.items)

    # 新说明字面立即参与；索引同步之前不得沿用旧说明的向量。
    edited = preparation.batch(
        batch.model_copy(
            update={
                "request_id": uuid4(),
                "expected_version": saved.version,
                "marks": [
                    MarkInput(
                        annotation_id=first.annotation_ids[0],
                        expected_version=1,
                        source_ranges=[batch.source_range],
                        note="反复推辞",
                    )
                ],
            }
        )
    )
    current = query.query(
        ReferenceQuery(
            format="full", scope=[ReferenceScope(work_id=book.work.id)], query="反复推辞"
        )
    )
    assert isinstance(current, ReferenceResult)
    annotated = next(h for h in current.items if h.annotation_ids)
    assert "clue_keyword" in annotated.channels and "clue_semantic" not in annotated.channels
    assert not current.diagnostics["works"][0]["work_indexes"]["clues_ready"]
    AssetService(database).set_annotation_status(
        AnnotationSetStatus(
            request_id=uuid4(),
            work_id=book.work.id,
            annotation_id=edited.annotations[0].id,
            expected_version=edited.annotations[0].version,
            status="withdrawn",
        )
    )
    with pytest.raises(ServiceError) as withdrawn:
        query.query(ReferenceQuery(search_id=current.search_id))
    assert withdrawn.value.code == "REFERENCE_SEARCH_STALE"
    retracted = query.query(
        ReferenceQuery(
            format="full", scope=[ReferenceScope(work_id=book.work.id)], query="反复推辞"
        )
    )
    assert isinstance(retracted, ReferenceResult)
    assert all(not h.annotation_ids for h in retracted.items)


def test_part_scope_append_pending_and_hidden_work(database: Database, tmp_path: Path) -> None:
    model = DeterministicModel()
    preparation = PreparationService(database, 1024 * 1024, model)
    service = ReferenceService(database, model)
    book = import_book(preparation, tmp_path, "旧卷雨声")
    other = import_book(preparation, tmp_path, "外部作品雨声")
    drain(preparation, other, model)
    path = tmp_path / "new-part.txt"
    path.write_text("分部：第二卷\n标题：新章\n新卷雨声", encoding="utf-8")
    appended = preparation.import_file(
        PrepareImport(request_id=uuid4(), work_id=book.work.id, file_path=str(path))
    )
    result = service.query(
        ReferenceQuery(
            format="full",
            scope=[ReferenceScope(work_id=book.work.id, part_ids=[book.part.id])],
            query="雨声",
        )
    )
    assert isinstance(result, ReferenceResult)
    assert (
        result.diagnostics["works"][0]["scope_coverage"]["complete"]
        and not result.diagnostics["works"][0]["work_indexes"]["source_ready"]
    )
    assert all(
        h.source_range.work_id == book.work.id and h.part_id == book.part.id for h in result.items
    )
    with pytest.raises(ServiceError) as new_part:
        service.query(
            ReferenceQuery(
                format="full",
                scope=[ReferenceScope(work_id=book.work.id, part_ids=[appended.part.id])],
                query="雨声",
            )
        )
    assert new_part.value.code == "REFERENCE_NOT_READY"
    with pytest.raises(ServiceError) as cross_work:
        service.query(
            ReferenceQuery(
                format="full",
                scope=[ReferenceScope(work_id=book.work.id, part_ids=[other.part.id])],
                query="雨声",
            )
        )
    assert cross_work.value.code == "PART_NOT_FOUND"
    WorkManagementService(database).set_visibility(book.work.id, "hidden")
    with pytest.raises(ServiceError) as hidden:
        service.query(
            ReferenceQuery(
                format="full", scope=[ReferenceScope(work_id=book.work.id)], query="雨声"
            )
        )
    assert hidden.value.code == "WORK_HIDDEN"


def test_overlap_merging_discontinuous_evidence_and_read_pages(
    database: Database, tmp_path: Path
) -> None:
    model = DeterministicModel()
    preparation = PreparationService(database, 1024 * 1024, model)
    book = import_book(preparation, tmp_path, "起因\n雨声\n回应\n标题：远处\n雨声又起")
    library = LibraryService(database, model)
    sections = library.browse(LibraryBrowse(view="sections", work_id=book.work.id)).sections
    assert sections is not None
    paragraphs = library.read(
        SourceRead(work_id=book.work.id, section_id=sections.items[0].id)
    ).items
    batch = batch_for(preparation, book)
    single = batch.source_range.model_copy(
        update={"start_paragraph_id": paragraphs[1].id, "end_paragraph_id": paragraphs[1].id}
    )
    preparation.batch(
        batch.model_copy(update={"marks": [MarkInput(source_ranges=[single], note="雨声")]})
    )
    drain(preparation, book, model)
    result = ReferenceService(database, model).query(
        ReferenceQuery(format="full", scope=[ReferenceScope(work_id=book.work.id)], query="雨声")
    )
    assert isinstance(result, ReferenceResult)
    assert len(result.items) == 2
    assert len({h.source_range.section_id for h in result.items}) == 2
    read = SourceRead(**single.model_dump(), before=1, after=1, limit=1)
    page = library.read(read)
    collected = [p.text for p in page.items]
    while page.next_cursor:
        page = library.read(read.model_copy(update={"cursor": page.next_cursor}))
        collected += [p.text for p in page.items]
    assert collected == ["起因", "雨声", "回应"]
    model.fail = True
    with pytest.raises(ServiceError) as unavailable:
        ReferenceService(database, model).query(
            ReferenceQuery(
                format="full", scope=[ReferenceScope(work_id=book.work.id)], query="雨声"
            )
        )
    assert unavailable.value.code == "EMBEDDING_UNAVAILABLE"
