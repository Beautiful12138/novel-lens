"""分类导航的真实数据库行为：共享身份、范围隔离、去重和原文回读。"""

from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from annotation_fixtures import annotation_fields
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from starlette.testclient import TestClient
from test_preparation import batch_for, import_book

from novel_lens.asset_contracts import (
    AnnotationCreate,
    AnnotationOut,
    AnnotationSetStatus,
    Recovery,
    TagCreate,
    TagOut,
    TagUpdate,
)
from novel_lens.assets import AssetService
from novel_lens.database import Database
from novel_lens.errors import ServiceError
from novel_lens.library import LibraryService
from novel_lens.preparation import PreparationService
from novel_lens.reference_contracts import (
    LibraryBrowse,
    MarkInput,
    PrepareFinish,
    PrepareStatus,
    SourceRead,
    TagInput,
)


def test_categories_scopes_any_all_and_source(database: Database, tmp_path: Path) -> None:
    preparation = PreparationService(database, 1024 * 1024)
    library, assets = LibraryService(database), AssetService(database)
    books = [import_book(preparation, tmp_path, f"原文{i}。\n保留上下文。") for i in range(3)]
    spans = [batch_for(preparation, b).source_range for b in books]
    first = assets.create_tag(
        TagCreate(
            request_id=uuid4(),
            namespace=str(uuid4()),
            name="对白",
            description="话语接续",
            categories=["interaction", "structure", "interaction"],
        )
    ).result
    second = assets.create_tag(
        TagCreate(request_id=uuid4(), namespace=str(uuid4()), name="停顿", description="场面停顿")
    ).result
    unused = assets.create_tag(
        TagCreate(request_id=uuid4(), namespace=str(uuid4()), name="未使用", description="词表")
    ).result
    assert isinstance(first, TagOut) and isinstance(second, TagOut) and isinstance(unused, TagOut)
    assert first.categories == ["interaction", "structure"]
    marks = []
    for i, ids in [(0, [first.id, second.id]), (0, [first.id]), (1, [second.id]), (2, [first.id])]:
        mark = assets.create_annotation(
            AnnotationCreate(
                request_id=uuid4(),
                work_id=books[i].work.id,
                **annotation_fields([spans[i]]),
                tag_ids=ids,
                note="有原文依据",
            )
        ).result
        assert isinstance(mark, AnnotationOut)
        marks.append(mark)
    with database.engine.begin() as conn:
        conn.execute(
            text("UPDATE works SET visibility='hidden' WHERE id=:w"), {"w": books[2].work.id}
        )
    scope: dict[str, Any] = {"work_ids": [b.work.id for b in books[:2]]}
    categories = library.browse(LibraryBrowse(view="categories", **scope)).categories
    assert categories is not None
    assert {c.id: (c.tag_count, c.annotation_count) for c in categories} == {
        "interaction": (1, 2),
        "structure": (1, 2),
        "unclassified": (1, 2),
    }
    tags = library.browse(LibraryBrowse(view="tags", **scope)).tag_page
    assert tags is not None and {t.id: t.annotation_count for t in tags.items} == {
        first.id: 2,
        second.id: 2,
    }
    unclassified = library.browse(
        LibraryBrowse(view="tags", category="unclassified", **scope)
    ).tag_page
    assert unclassified is not None and unclassified.items[0].id == second.id
    unused_page = library.browse(LibraryBrowse(view="tags", namespace=unused.namespace)).tag_page
    assert unused_page is not None and unused_page.items[0].annotation_count == 0
    request = LibraryBrowse(view="annotations", tag_ids=[first.id, second.id], **scope)
    any_page = library.browse(request).annotations
    assert any_page is not None and {a.id for a in any_page.items} == {m.id for m in marks[:3]}
    all_page = library.browse(request.model_copy(update={"tag_match": "all"})).annotations
    assert all_page is not None and [a.id for a in all_page.items] == [marks[0].id]
    first_page = library.browse(request.model_copy(update={"limit": 1})).annotations
    assert first_page is not None and first_page.next_cursor
    with pytest.raises(ServiceError, match="游标"):
        library.browse(
            request.model_copy(update={"cursor": first_page.next_cursor, "tag_match": "all"})
        )
    following = library.browse(
        request.model_copy(update={"cursor": first_page.next_cursor, "format": "full"})
    ).annotations
    assert following is not None and len(following.items) == 2
    assets.set_annotation_status(
        AnnotationSetStatus(
            request_id=uuid4(),
            work_id=books[0].work.id,
            annotation_id=marks[0].id,
            expected_version=1,
            status="withdrawn",
        )
    )
    remaining = library.browse(request).annotations
    assert remaining is not None and {a.id for a in remaining.items} == {marks[1].id, marks[2].id}
    detail = library.browse(
        LibraryBrowse(view="annotations", work_id=books[0].work.id, annotation_id=marks[1].id)
    )
    assert detail.locations and detail.locations[0].section_title
    read = library.read(SourceRead(**spans[0].model_dump()))
    assert read.items[0].text == "原文0。"
    with pytest.raises(ServiceError) as hidden:
        library.browse(LibraryBrowse(view="tags", work_id=books[2].work.id))
    assert hidden.value.code == "WORK_HIDDEN"


def test_categories_update_replay_and_unknown_rejected(database: Database) -> None:
    service = AssetService(database)
    created = service.create_tag(
        TagCreate(
            request_id=uuid4(),
            namespace="写法",
            name=str(uuid4()),
            description="观察",
            categories=["language"],
        )
    ).result
    assert isinstance(created, TagOut)
    request = TagUpdate(
        request_id=uuid4(),
        tag_id=created.id,
        expected_version=created.version,
        name=created.name,
        description=created.description,
        aliases=["别名"],
        categories=["language", "perspective"],
    )
    updated = service.update_tag(request)
    assert updated.result.categories == ["language", "perspective"]  # type: ignore[union-attr]
    assert service.update_tag(request).replayed
    with pytest.raises(ServiceError) as conflict:
        service.update_tag(request.model_copy(update={"request_id": uuid4()}))
    assert conflict.value.code == "VERSION_CONFLICT"
    # 未提供分类时保留；空数组明确清空，避免普通定义修订丢失归类。
    preserved = service.update_tag(
        request.model_copy(
            update={"request_id": uuid4(), "expected_version": 2, "categories": None}
        )
    ).result
    assert isinstance(preserved, TagOut) and preserved.categories == ["language", "perspective"]
    cleared = service.update_tag(
        request.model_copy(update={"request_id": uuid4(), "expected_version": 3, "categories": []})
    ).result
    assert isinstance(cleared, TagOut) and cleared.categories == []
    # 数据库本身也拒绝无效分类，不能仅依赖客户端校验。
    with pytest.raises(IntegrityError):
        with database.engine.begin() as conn:
            conn.execute(
                text("UPDATE tags SET categories='[\"invented\"]' WHERE id=:id"), {"id": created.id}
            )
    with pytest.raises(ValidationError):
        TagCreate.model_validate(
            {
                "request_id": uuid4(),
                "namespace": "写法",
                "name": "任意",
                "description": "描述",
                "categories": ["invented"],
            }
        )


def test_prepare_finish_without_embedding_and_tag_reuse(database: Database, tmp_path: Path) -> None:
    service = PreparationService(database, 1024 * 1024)
    book = import_book(service, tmp_path)
    batch = batch_for(service, book)
    saved = service.batch(
        batch.model_copy(
            update={
                "marks": [
                    MarkInput(
                        **annotation_fields([batch.source_range]),
                        note="具体表达观察",
                        tags=[
                            TagInput(
                                namespace="写法",
                                name=str(uuid4()),
                                description="对白接续",
                                categories=["interaction"],
                                aliases=["接话"],
                            )
                        ],
                    )
                ]
            }
        )
    )
    tag = AssetService(database).get_tag(saved.annotations[0].tag_ids[0])
    assert tag.categories == ["interaction"] and tag.aliases == ["接话"]
    repeated = batch.model_copy(
        update={
            "request_id": uuid4(),
            "expected_version": saved.version,
            "marks": [
                MarkInput(
                    **annotation_fields([batch.source_range]),
                    note="具体表达观察",
                    tags=[
                        TagInput(
                            namespace=tag.namespace,
                            name=tag.name,
                            description="不能覆盖共享定义",
                            categories=["language"],
                            aliases=["不能覆盖共享别名"],
                        )
                    ],
                )
            ],
        }
    )
    saved = service.batch(repeated)
    assert AssetService(database).get_tag(tag.id) == tag
    finish = PrepareFinish(
        request_id=uuid4(),
        work_id=book.work.id,
        job_id=book.job.id,
        expected_version=saved.version,
        recovery=Recovery(next_action="完成"),
        note="已读完",
    )
    assert service.finish(finish).job.status == "completed"
    assert service.finish(finish).replayed
    assert service.status(PrepareStatus(work_id=book.work.id, job_id=book.job.id)).work_ready


def test_http_new_browse_and_removed_query(client: TestClient) -> None:
    assert client.post("/library/browse", json={"view": "categories"}).status_code == 200
    assert (
        client.post("/library/browse", json={"view": "annotations", "tag_match": "any"}).status_code
        == 200
    )
    assert (
        client.post("/library/browse", json={"view": "tags", "category": "invented"}).status_code
        == 422
    )
    assert client.post("/reference/query", json={"query": "任意"}).status_code == 404
