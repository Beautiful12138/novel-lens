"""真实数据库验证纠错状态、共享标签修订、并发、批次回滚及语义排除。"""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from test_analysis import create, key, mutate, recovery
from test_assets import create_annotation, create_tag
from test_assets import source as source
from test_semantic import DeterministicModel, build, new_index
from test_semantic_annotations import annotated_index

from novel_lens.analysis import AnalysisService
from novel_lens.asset_contracts import (
    AnalysisCheckpoint,
    AnalysisCheckpointOut,
    AnalysisJobGet,
    AnnotationGet,
    AnnotationList,
    AnnotationOut,
    AnnotationSetStatus,
    AnnotationUpdate,
    CoverageGet,
    TagSearch,
    TagUpdate,
)
from novel_lens.assets import AssetService
from novel_lens.contracts import SourceRange
from novel_lens.database import Database
from novel_lens.errors import ServiceError
from novel_lens.search import SearchService
from novel_lens.search_contracts import AnnotationSearchRequest, SearchRequest
from novel_lens.semantic import SemanticService
from novel_lens.semantic_contracts import SemanticBuild, SemanticGet, SemanticSearch


def status_request(a: AnnotationOut, *, active: bool = False) -> AnnotationSetStatus:
    return AnnotationSetStatus(
        request_id=uuid4(),
        work_id=a.work_id,
        annotation_id=a.id,
        expected_version=a.version,
        status="active" if active else "withdrawn",
    )


def test_status_filters_replay_and_restore(
    database: Database,
    source: tuple[UUID, list[SourceRange]],
) -> None:
    assets, search = AssetService(database), SearchService(database)
    a = create_annotation(assets, source, note="纠错标记")
    b = create_annotation(assets, source, note="纠错标记")
    query = AnnotationList(work_id=source[0], limit=1)
    old_page = assets.list_annotations(query)
    request = status_request(a)
    revoked = assets.set_annotation_status(request).result
    assert isinstance(revoked, AnnotationOut)
    assert revoked.status == "withdrawn" and revoked.version == 2
    assert revoked.source_ranges == a.source_ranges and revoked.note == a.note
    assert assets.set_annotation_status(request).replayed
    assert [p.id for p in assets.list_annotations(query).items] == [b.id]
    assert (
        assets.list_annotations(query.model_copy(update={"status": "withdrawn"})).items[0].id
        == a.id
    )
    assert (
        len(assets.list_annotations(query.model_copy(update={"status": None, "limit": 100})).items)
        == 2
    )
    assert (
        assets.list_annotations(query.model_copy(update={"cursor": old_page.next_cursor}))
        .items[0]
        .id
        == b.id
    )
    with pytest.raises(ServiceError) as err:
        assets.list_annotations(
            query.model_copy(update={"cursor": old_page.next_cursor, "status": None})
        )
    assert err.value.code == "INVALID_CURSOR"
    sq = AnnotationSearchRequest(work_id=source[0], terms=["纠错"], limit=1)
    assert search.annotations(sq).items[0].annotation_id == b.id
    assert (
        search.annotations(sq.model_copy(update={"status": "withdrawn"})).items[0].status
        == "withdrawn"
    )
    all_page = search.annotations(sq.model_copy(update={"status": None}))
    with pytest.raises(ServiceError) as err:
        search.annotations(sq.model_copy(update={"cursor": all_page.next_cursor}))
    assert err.value.code == "INVALID_CURSOR"
    # 内容修订不自动恢复撤回状态；旧请求只能重放当时快照。
    edit = AnnotationUpdate(
        request_id=uuid4(),
        work_id=a.work_id,
        annotation_id=a.id,
        expected_version=2,
        source_ranges=a.source_ranges,
        tag_ids=[],
        note="修正说明",
    )
    changed = assets.update_annotation(edit).result
    assert isinstance(changed, AnnotationOut) and changed.status == "withdrawn"
    restored = assets.set_annotation_status(status_request(changed, active=True)).result
    assert (
        isinstance(restored, AnnotationOut)
        and restored.version == 4
        and restored.status == "active"
    )
    assert assets.set_annotation_status(request).result == revoked
    assert assets.get_annotation(AnnotationGet(work_id=a.work_id, annotation_id=a.id)) == restored
    with pytest.raises(ServiceError) as err:
        assets.set_annotation_status(status_request(a))
    assert err.value.code == "VERSION_CONFLICT"
    with pytest.raises(ServiceError) as err:
        assets.set_annotation_status(
            status_request(restored).model_copy(update={"work_id": uuid4()})
        )
    assert err.value.code == "ANNOTATION_NOT_FOUND"


def test_tag_edit_conflict_concurrency_and_shared_links(
    database: Database,
    source: tuple[UUID, list[SourceRange]],
) -> None:
    assets = AssetService(database)
    namespace = uuid4().hex
    tag = create_tag(assets, namespace, "旧名称", aliases=["旧别名"])
    a = create_annotation(assets, source, tag_ids=[tag.id])
    create_tag(assets, namespace, "已占用")
    req = TagUpdate(
        request_id=uuid4(),
        tag_id=tag.id,
        expected_version=1,
        name="已占用",
        description="新定义",
        aliases=[],
    )
    with pytest.raises(ServiceError) as err:
        assets.update_tag(req)
    assert err.value.code == "TAG_NAME_CONFLICT" and assets.get_tag(tag.id) == tag
    with pytest.raises(ServiceError) as err:
        assets.write_result(req.request_id)
    assert err.value.code == "WRITE_NOT_COMMITTED"
    req = req.model_copy(update={"name": "新名称"})
    changed = assets.update_tag(req)
    assert assets.update_tag(req).replayed
    now = assets.get_tag(tag.id)
    assert now.version == 2 and now.namespace == namespace and now.aliases == []
    assert assets.get_annotation(AnnotationGet(work_id=a.work_id, annotation_id=a.id)) == a
    assert assets.list_tags(TagSearch(namespace=namespace, query="旧别名")).items == []
    assert assets.list_tags(TagSearch(namespace=namespace, query="新定义")).items == [now]
    barrier = Barrier(2)

    def competing(name: str) -> str:
        barrier.wait()
        try:
            assets.update_tag(
                req.model_copy(update={"request_id": uuid4(), "expected_version": 2, "name": name})
            )
            return "ok"
        except ServiceError as error:
            return error.code

    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(competing, ["修订甲", "修订乙"])) == ["VERSION_CONFLICT", "ok"]
    assert assets.get_tag(tag.id).version == 3
    assert assets.update_tag(req).result == changed.result


def test_correction_checkpoint_rollback_and_content_status_race(
    database: Database,
    source: tuple[UUID, list[SourceRange]],
) -> None:
    service = AnalysisService(database)
    assets = service.assets
    a = create_annotation(assets, source)
    tag = create_tag(assets, uuid4().hex, "定义")
    job = create(service, source)
    withdraw = status_request(a)
    bad = TagUpdate(
        request_id=uuid4(),
        tag_id=tag.id,
        expected_version=99,
        name="修订",
        description="修订定义",
        aliases=[],
    )
    req = AnalysisCheckpoint.model_validate(
        mutate(
            job,
            source_range=source[1][0],
            recovery=recovery(),
            writes=[
                dict(operation="annotation_set_status", input=withdraw.model_dump()),
                dict(operation="tag_update", input=bad.model_dump()),
            ],
        )
    )
    with pytest.raises(ServiceError) as err:
        service.checkpoint(req)
    assert err.value.code == "VERSION_CONFLICT"
    assert assets.get_annotation(AnnotationGet(work_id=a.work_id, annotation_id=a.id)) == a
    assert service.get(AnalysisJobGet(**key(job))).version == 1
    for identifier in [req.request_id, withdraw.request_id, bad.request_id]:
        with pytest.raises(ServiceError) as err:
            assets.write_result(identifier)
        assert err.value.code == "WRITE_NOT_COMMITTED"
    assert isinstance(req.writes[1].input, TagUpdate)
    req.writes[1].input.expected_version = 1
    result = service.checkpoint(req)
    assert service.checkpoint(req).replayed
    assert isinstance(result.result, AnalysisCheckpointOut) and len(result.result.writes) == 2
    assert assets.get_tag(tag.id).version == 2
    assert service.coverage(CoverageGet(**key(job), status="processed")).items
    old = assets.get_annotation(AnnotationGet(work_id=a.work_id, annotation_id=a.id))
    edit = AnnotationUpdate(
        request_id=uuid4(),
        work_id=a.work_id,
        annotation_id=a.id,
        expected_version=old.version,
        source_ranges=a.source_ranges,
        tag_ids=[],
        note="竞争修订",
    )
    restore = status_request(old, active=True)
    barrier = Barrier(2)

    def race(action: str) -> str:
        barrier.wait()
        try:
            assets.update_annotation(edit) if action == "edit" else assets.set_annotation_status(
                restore
            )
            return "ok"
        except ServiceError as error:
            return error.code

    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(race, ["edit", "restore"])) == ["VERSION_CONFLICT", "ok"]
    assert assets.get_annotation(AnnotationGet(work_id=a.work_id, annotation_id=a.id)).version == 3


def test_retraction_semantic_reuse_gaps_and_inference(
    database: Database,
    source: tuple[UUID, list[SourceRange]],
) -> None:
    assets = AssetService(database)
    a = create_annotation(assets, source, note="首段")
    model = DeterministicModel()
    semantic = SemanticService(database, model)
    index = annotated_index(semantic, source[0])
    assert build(semantic, source[0], index).coverage.complete
    full = new_index(semantic, source[0])
    assert build(semantic, source[0], full).coverage.complete
    request = SemanticSearch(work_id=source[0], kind="annotation", query="首段")
    assert semantic.search(request).items
    revoked = assets.set_annotation_status(status_request(a)).result
    assert isinstance(revoked, AnnotationOut)
    assert not semantic.search(request).items
    assert semantic.search(SemanticSearch(work_id=source[0], query="首段")).items
    assert SearchService(database).source(SearchRequest(work_id=source[0], terms=["首段"])).items
    status = semantic.get(SemanticGet(work_id=source[0], kind="annotation"))
    assert status.active and status.active.coverage.total == 0 and status.active.coverage.complete
    calls = len(model.calls)
    active = assets.set_annotation_status(status_request(revoked, active=True)).result
    assert isinstance(active, AnnotationOut)
    assert len(model.calls) == calls
    assert semantic.search(request).items
    # 新代只捕获有效标注；撤回时创建的空代不假装包含恢复后的证据。
    revoked = assets.set_annotation_status(status_request(active)).result
    assert isinstance(revoked, AnnotationOut)
    empty = annotated_index(semantic, source[0])
    assert build(semantic, source[0], empty).coverage.complete
    active = assets.set_annotation_status(status_request(revoked, active=True)).result
    assert isinstance(active, AnnotationOut)
    state = semantic.get(SemanticGet(work_id=source[0], kind="annotation"))
    assert state.active and not state.active.coverage.complete
    with pytest.raises(ServiceError) as err:
        semantic.search(request)
    assert err.value.code == "INDEX_INCOMPLETE"
    target = annotated_index(semantic, source[0])

    def withdraw_during_embed() -> None:
        assets.set_annotation_status(status_request(active))

    model.during_embed = withdraw_during_embed
    result = semantic.build(SemanticBuild(work_id=source[0], index_id=target, request_id=uuid4()))
    assert result.batch_discarded and result.batch_ready == 0
