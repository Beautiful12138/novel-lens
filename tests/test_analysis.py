"""真实数据库验证任务隔离、进度、原子批次与并发恢复。"""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from typing import Any
from uuid import UUID, uuid4

import pytest
from part_fixtures import import_work
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from test_assets import source as source
from test_entities_relations import relation_request

from novel_lens.analysis import AnalysisService
from novel_lens.asset_contracts import (
    AnalysisCheckpoint,
    AnalysisCheckpointOut,
    AnalysisJobComplete,
    AnalysisJobCreate,
    AnalysisJobGet,
    AnalysisJobList,
    AnalysisJobOut,
    AnalysisJobUpdate,
    AnalysisTarget,
    AnnotationCreate,
    AnnotationGet,
    AnnotationOut,
    AssetWriteOut,
    CoverageGet,
    CoverageMark,
    EntityCreate,
    EntityOut,
    Recovery,
    RelationSnapshot,
    StyleGuideCreate,
    TagCreate,
)
from novel_lens.assets import AssetService
from novel_lens.contracts import SourceRange
from novel_lens.database import Database
from novel_lens.errors import ServiceError
from novel_lens.importing import ImportService
from novel_lens.reading import ReadingService


@pytest.fixture
def service(database: Database) -> AnalysisService:
    return AnalysisService(database)


def recovery(**changes: Any) -> Recovery:
    return Recovery.model_validate(dict(next_action="继续核对后续段落") | changes)


def create(
    service: AnalysisService, source: tuple[UUID, list[SourceRange]], **changes: Any
) -> AnalysisJobOut:
    result = service.create(
        AnalysisJobCreate.model_validate(
            dict(
                request_id=uuid4(),
                work_id=source[0],
                title="深读",
                goal="完整分析目标范围",
                target=dict(kind="whole_work"),
                recovery=recovery(),
            )
            | changes
        )
    ).result
    assert isinstance(result, AnalysisJobOut)
    return result


def key(job: AnalysisJobOut) -> dict[str, Any]:
    return dict(work_id=job.work_id, job_id=job.id)


def mutate(job: AnalysisJobOut, **changes: Any) -> dict[str, Any]:
    return key(job) | dict(request_id=uuid4(), expected_version=job.version) | changes


def checkpoint(
    service: AnalysisService, job: AnalysisJobOut, ref: SourceRange, **changes: Any
) -> AnalysisJobOut:
    service.checkpoint(
        AnalysisCheckpoint.model_validate(
            mutate(
                job,
                source_range=ref,
                writes=[],
                recovery=recovery(),
                outcome_note="核对完成，没有新增结论",
            )
            | changes
        )
    )
    return service.get(AnalysisJobGet(**key(job)))


def single(ref: SourceRange, identifier: UUID) -> SourceRange:
    return ref.model_copy(update=dict(start_paragraph_id=identifier, end_paragraph_id=identifier))


def expect(code: str, action: Any) -> None:
    with pytest.raises(ServiceError) as raised:
        action()
    assert raised.value.code == code


def test_target_normalization_independence_and_transitions(
    service: AnalysisService, source: tuple[UUID, list[SourceRange]]
) -> None:
    ref = source[1][0]
    p = ReadingService(service.database).list_paragraphs(source[0], ref.section_id, 100, None).items
    refs = [
        single(ref, p[2].id),
        ref.model_copy(update=dict(end_paragraph_id=p[1].id)),
        single(ref, p[1].id),
    ]
    job = create(service, source, target=dict(kind="ranges", source_ranges=refs))
    assert job.target.source_ranges == [ref] and job.counts.unprocessed == 3
    other = create(service, source)
    assert other.target_paragraph_count == 4
    expect(
        "RANGE_OUTSIDE_TARGET",
        lambda: service.mark(CoverageMark(**mutate(job, source_range=source[1][1], status="read"))),
    )
    expect(
        "INVALID_COVERAGE_TRANSITION",
        lambda: service.mark(
            CoverageMark(**mutate(job, source_range=ref, status="needs_revisit", reason="复核"))
        ),
    )
    job = checkpoint(service, job, single(ref, p[0].id))
    service.mark(CoverageMark(**mutate(job, source_range=ref, status="read")))
    job = service.get(AnalysisJobGet(**key(job)))
    assert job.counts.processed == 1 and job.counts.read == 2
    service.mark(
        CoverageMark(**mutate(job, source_range=ref, status="needs_revisit", reason="对比后文"))
    )
    job = service.get(AnalysisJobGet(**key(job)))
    service.mark(CoverageMark(**mutate(job, source_range=ref, status="read")))
    job = service.get(AnalysisJobGet(**key(job)))
    assert job.counts.needs_revisit == 3
    job = checkpoint(service, job, ref)
    page = service.coverage(CoverageGet(**key(job)))
    assert (
        len(page.items) == 1
        and page.items[0].status == "processed"
        and page.items[0].reason is None
    )
    assert service.get(AnalysisJobGet(**key(other))).counts.unprocessed == 4
    listed = service.list(AnalysisJobList(work_id=source[0], limit=1))
    assert listed.items[0].id == job.id and listed.next_cursor
    second = service.list(AnalysisJobList(work_id=source[0], cursor=listed.next_cursor))
    assert [item.id for item in second.items] == [other.id]
    assert service.get(AnalysisJobGet(**key(job))) == job


def test_coverage_paging_gaps_filters_and_version(
    service: AnalysisService, source: tuple[UUID, list[SourceRange]]
) -> None:
    ref = source[1][0]
    p = ReadingService(service.database).list_paragraphs(source[0], ref.section_id, 100, None).items
    job = create(
        service,
        source,
        target=dict(
            kind="ranges", source_ranges=[single(ref, p[0].id), single(ref, p[2].id), source[1][1]]
        ),
    )
    page = service.coverage(CoverageGet(**key(job), limit=1))
    assert page.items[0].source_range == single(ref, p[0].id) and page.next_cursor
    next_page = service.coverage(CoverageGet(**key(job), limit=1, cursor=page.next_cursor))
    assert next_page.items[0].source_range == single(ref, p[2].id)
    expect(
        "INVALID_CURSOR",
        lambda: service.coverage(CoverageGet(**key(job), status="read", cursor=page.next_cursor)),
    )
    expect("RANGE_OUTSIDE_TARGET", lambda: checkpoint(service, job, ref))
    job = checkpoint(service, job, single(ref, p[2].id))
    expect(
        "VERSION_CONFLICT",
        lambda: service.coverage(CoverageGet(**key(job), cursor=page.next_cursor)),
    )
    assert (
        len(
            service.coverage(
                CoverageGet(**key(job), status="processed", section_id=ref.section_id)
            ).items
        )
        == 1
    )
    expect("JOB_NOT_FOUND", lambda: service.get(AnalysisJobGet(work_id=uuid4(), job_id=job.id)))


def test_pause_complete_reopen_and_immutable_snapshots(
    service: AnalysisService, source: tuple[UUID, list[SourceRange]]
) -> None:
    job = create(service, source, target=dict(kind="ranges", source_ranges=[source[1][0]]))

    def completion(j: AnalysisJobOut, **kw: Any) -> AnalysisJobComplete:
        return AnalysisJobComplete(
            **mutate(
                j,
                recovery=recovery(),
                style_guide_version=1,
                calibration_note="核对样本常态与例外",
                limitations=None,
            )
            | kw
        )

    expect("JOB_INCOMPLETE", lambda: service.complete(completion(job)))
    pause = AnalysisJobUpdate(**mutate(job, status="paused", recovery=recovery()))
    service.update(pause)
    job = service.get(AnalysisJobGet(**key(job)))
    expect("JOB_STATE_CONFLICT", lambda: checkpoint(service, job, source[1][0]))
    expect(
        "JOB_STATE_CONFLICT",
        lambda: service.mark(CoverageMark(**mutate(job, source_range=source[1][0], status="read"))),
    )
    service.update(AnalysisJobUpdate(**mutate(job, status="running", recovery=recovery())))
    job = service.get(AnalysisJobGet(**key(job)))
    job = checkpoint(service, job, source[1][0])
    expect("STYLE_GUIDE_NOT_FOUND", lambda: service.complete(completion(job)))
    service.assets.create_style_guide(
        StyleGuideCreate(request_id=uuid4(), work_id=source[0], scope_note="仅样本范围", entries=[])
    )
    expect("VERSION_CONFLICT", lambda: service.complete(completion(job, style_guide_version=2)))
    request = completion(
        job,
        recovery=recovery(
            open_questions=[
                dict(
                    observation="后文尚待比较", question="是否有变化", source_ranges=[source[1][1]]
                )
            ]
        ),
    )
    done = service.complete(request)
    job = service.get(AnalysisJobGet(**key(job)))
    assert job.status == "completed" and job.completion and job.completion.style_guide_version == 1
    expect(
        "JOB_STATE_CONFLICT",
        lambda: service.update(
            AnalysisJobUpdate(**mutate(job, status="running", recovery=recovery()))
        ),
    )
    service.update(
        AnalysisJobUpdate(
            **mutate(job, status="running", reopen_reason="对比新结论", recovery=recovery())
        )
    )
    opened = service.get(AnalysisJobGet(**key(job)))
    assert opened.completion is None and opened.counts.processed == 3
    assert service.complete(request).result == done.result
    assert service.assets.write_result(request.request_id).result == done.result
    paused = service.update(pause).result
    assert isinstance(paused, AnalysisJobOut) and paused.status == "paused"


def test_checkpoint_rollback_replay_and_scope(
    service: AnalysisService, source: tuple[UUID, list[SourceRange]]
) -> None:
    job = create(service, source)
    old = AnnotationCreate(request_id=uuid4(), work_id=source[0], source_ranges=source[1])
    old_result = service.assets.create_annotation(old)
    new = TagCreate(request_id=uuid4(), namespace=uuid4().hex, name="批次", description="定义")
    bad = AnnotationCreate(
        request_id=uuid4(), work_id=source[0], source_ranges=source[1], tag_ids=[uuid4()]
    )
    writes = [
        dict(operation="annotation_create", input=old),
        dict(operation="tag_create", input=new),
        dict(operation="annotation_create", input=bad),
    ]
    request = AnalysisCheckpoint.model_validate(
        mutate(job, source_range=source[1][0], writes=writes, recovery=recovery())
    )
    expect("TAG_NOT_FOUND", lambda: service.checkpoint(request))
    assert service.get(AnalysisJobGet(**key(job))) == job
    for identifier in (request.request_id, new.request_id, bad.request_id):
        expect(
            "WRITE_NOT_COMMITTED",
            lambda identifier=identifier: service.assets.write_result(identifier),
        )
    assert service.assets.write_result(old.request_id).result == old_result.result
    good = request.model_copy(update=dict(writes=request.writes[:2]))
    committed = service.checkpoint(good)
    assert service.checkpoint(good).replayed
    assert isinstance(committed.result, AnalysisCheckpointOut) and committed.result.version == 2
    assert service.assets.write_result(new.request_id).operation == "tag_create"
    assert service.assets.create_annotation(old).replayed
    expect(
        "REQUEST_CONFLICT",
        lambda: service.checkpoint(good.model_copy(update=dict(outcome_note="不同输入"))),
    )
    current = service.get(AnalysisJobGet(**key(job)))
    foreign = old.model_copy(update=dict(request_id=uuid4(), work_id=uuid4()))
    expect(
        "INVALID_RANGE",
        lambda: checkpoint(
            service,
            current,
            source[1][0],
            writes=[dict(operation="annotation_create", input=foreign)],
        ),
    )


def test_checkpoint_all_asset_operations_and_omitted_entity_semantics(
    service: AnalysisService, source: tuple[UUID, list[SourceRange]]
) -> None:
    assets = service.assets
    identity = assets.create_entity(
        EntityCreate(request_id=uuid4(), work_id=source[0], type="character", canonical_name="甲")
    ).result
    assert isinstance(identity, EntityOut)
    annotation_request = AnnotationCreate(
        request_id=uuid4(), work_id=source[0], source_ranges=source[1], entity_ids=[identity.id]
    )
    annotation = assets.create_annotation(annotation_request).result
    assert isinstance(annotation, AnnotationOut)
    relation_req = relation_request(source)
    relation = assets.create_relation(relation_req).result
    assert isinstance(relation, RelationSnapshot)
    job = create(service, source)
    annotation_update = dict(
        request_id=uuid4(),
        work_id=source[0],
        annotation_id=annotation.id,
        expected_version=1,
        source_ranges=source[1],
        note="保留实体关联",
        tag_ids=[],
    )
    writes = [
        dict(operation="annotation_update", input=annotation_update),
        dict(
            operation="entity_update",
            input=dict(
                request_id=uuid4(),
                work_id=source[0],
                entity_id=identity.id,
                expected_version=1,
                type="character",
                canonical_name="甲新",
                aliases=[],
                note=None,
            ),
        ),
        dict(
            operation="entity_create",
            input=dict(
                request_id=uuid4(), work_id=source[0], type="location", canonical_name="城市"
            ),
        ),
        dict(operation="relation_create", input=relation_req),
        dict(
            operation="relation_update",
            input=relation_req.model_dump()
            | dict(request_id=uuid4(), relation_id=relation.id, expected_version=1, note="校准"),
        ),
        dict(
            operation="relation_set_status",
            input=dict(
                request_id=uuid4(),
                work_id=source[0],
                relation_id=relation.id,
                expected_version=2,
                status="withdrawn",
            ),
        ),
        dict(
            operation="style_guide_create",
            input=dict(request_id=uuid4(), work_id=source[0], scope_note="样本", entries=[]),
        ),
        dict(
            operation="style_guide_update",
            input=dict(
                request_id=uuid4(),
                work_id=source[0],
                expected_version=1,
                scope_note="样本校准",
                entries=[],
            ),
        ),
    ]
    request = AnalysisCheckpoint.model_validate(
        mutate(job, source_range=source[1][0], writes=writes, recovery=recovery())
    )
    service.checkpoint(request)
    changed = assets.get_annotation(AnnotationGet(work_id=source[0], annotation_id=annotation.id))
    assert changed.entity_ids == [identity.id]
    explicit = request.model_dump()
    explicit["writes"][0]["input"]["entity_ids"] = []
    expect(
        "REQUEST_CONFLICT", lambda: service.checkpoint(AnalysisCheckpoint.model_validate(explicit))
    )
    for item in request.writes:
        assert assets.write_result(item.input.request_id).operation == item.operation


def test_checkpoint_database_failure_and_oversize_roll_back(
    service: AnalysisService, source: tuple[UUID, list[SourceRange]]
) -> None:
    job = create(service, source)
    tag: dict[str, Any] = dict(
        operation="tag_create",
        input=dict(request_id=uuid4(), namespace=uuid4().hex, name="恢复", description="定义"),
    )
    request = AnalysisCheckpoint.model_validate(
        mutate(job, source_range=source[1][0], writes=[tag], recovery=recovery())
    )
    with service.database.engine.begin() as connection:
        connection.execute(
            text(
                """CREATE FUNCTION fail_checkpoint() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN IF NEW.job_id = '"""
                + str(job.id)
                + """'::uuid THEN RAISE EXCEPTION 'injected'; END IF; RETURN NEW; END $$;
            CREATE TRIGGER fail_checkpoint BEFORE INSERT ON analysis_coverage
            FOR EACH ROW EXECUTE FUNCTION fail_checkpoint();"""
            )
        )
    try:
        with pytest.raises(DBAPIError):
            service.checkpoint(request)
    finally:
        with service.database.engine.begin() as connection:
            connection.execute(
                text(
                    "DROP TRIGGER fail_checkpoint ON analysis_coverage; "
                    "DROP FUNCTION fail_checkpoint()"
                )
            )
    assert service.get(AnalysisJobGet(**key(job))) == job
    expect("WRITE_NOT_COMMITTED", lambda: service.assets.write_result(tag["input"]["request_id"]))
    too_large = request.model_copy(update=dict(recovery=recovery(next_action="大" * 350000)))
    expect("ASSET_TOO_LARGE", lambda: service.checkpoint(too_large))
    assert service.get(AnalysisJobGet(**key(job))) == job
    service.checkpoint(request)


def test_concurrent_reverse_batches_and_same_version(
    service: AnalysisService, source: tuple[UUID, list[SourceRange]]
) -> None:
    a, b = create(service, source), create(service, source)
    writes = [
        dict(
            operation="tag_create",
            input=dict(request_id=uuid4(), namespace=uuid4().hex, name="并发", description="定义"),
        )
        for _ in range(2)
    ]
    requests = [
        AnalysisCheckpoint.model_validate(
            mutate(j, source_range=source[1][0], writes=w, recovery=recovery())
        )
        for j, w in ((a, writes), (b, list(reversed(writes))))
    ]
    barrier = Barrier(2)

    def run(request: AnalysisCheckpoint) -> str:
        barrier.wait(timeout=10)
        try:
            service.checkpoint(request)
            return "ok"
        except ServiceError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(run, requests)) == ["ok", "ok"]
    current = service.get(AnalysisJobGet(**key(a)))
    requests = [
        AnalysisCheckpoint(
            **mutate(
                current,
                source_range=source[1][1],
                writes=[],
                recovery=recovery(),
                outcome_note="无新增",
            )
        )
        for _ in range(2)
    ]
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(run, requests)) == ["VERSION_CONFLICT", "ok"]
    same = AnalysisCheckpoint(
        **mutate(
            service.get(AnalysisJobGet(**key(b))),
            source_range=source[1][1],
            writes=[],
            recovery=recovery(),
            outcome_note="无新增",
        )
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(run, [same, same])) == ["ok", "ok"]
    assert service.get(AnalysisJobGet(**key(b))).version == 3


def test_empty_sections_and_recovery_references(service: AnalysisService) -> None:
    work = import_work(
        ImportService(service.database, 10000),
        f"分部：{uuid4()}\n标题：空\n标题：有文\n一段".encode(),
        uuid4(),
    ).work
    job = create(service, (work.id, []))
    assert job.target_paragraph_count == 1
    ref = service.coverage(CoverageGet(**key(job))).items[0].source_range
    invalid = ref.model_copy(update=dict(work_id=uuid4()))
    expect(
        "INVALID_RANGE",
        lambda: service.update(
            AnalysisJobUpdate(**mutate(job, status="paused", recovery=recovery(next_range=invalid)))
        ),
    )
    assert service.get(AnalysisJobGet(**key(job))).version == 1


def test_strict_batch_validation(source: tuple[UUID, list[SourceRange]]) -> None:
    identifier = uuid4()
    data = dict(
        request_id=identifier,
        work_id=source[0],
        job_id=uuid4(),
        expected_version=1,
        source_range=source[1][0],
        recovery=recovery(),
    )
    for writes, extras in [
        ([], {}),
        (
            [
                dict(
                    operation="tag_create",
                    input=dict(request_id=identifier, namespace="n", name="x", description="x"),
                )
            ],
            {},
        ),
        ([], dict(outcome_note=" ")),
        ([dict(operation="unknown", input={})], {}),
    ]:
        with pytest.raises(ValidationError):
            AnalysisCheckpoint.model_validate(data | dict(writes=writes) | extras)
    with pytest.raises(ValidationError):
        AnalysisTarget(kind="ranges", source_ranges=[])


def test_cross_batch_entity_foreign_keys_do_not_deadlock(
    service: AnalysisService,
    source: tuple[UUID, list[SourceRange]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同时引用对方实体后再修订自身实体，外键检查不能与更新锁形成环。"""
    identities = [
        service.assets.create_entity(
            EntityCreate(
                request_id=uuid4(), work_id=source[0], type="character", canonical_name=str(i)
            )
        ).result
        for i in range(2)
    ]
    assert all(isinstance(item, EntityOut) for item in identities)
    names = [EntityOut.model_validate(item.model_dump()) for item in identities]
    jobs = [create(service, source) for _ in range(2)]
    barrier = Barrier(2)
    original = AssetService.create_annotation

    def synchronized(self: AssetService, request: AnnotationCreate) -> AssetWriteOut:
        result = original(self, request)
        barrier.wait(timeout=10)
        return result

    monkeypatch.setattr(AssetService, "create_annotation", synchronized)
    requests = [
        AnalysisCheckpoint.model_validate(
            mutate(
                job,
                source_range=source[1][0],
                recovery=recovery(),
                writes=[
                    dict(
                        operation="annotation_create",
                        input=dict(
                            request_id=uuid4(),
                            work_id=source[0],
                            source_ranges=source[1],
                            entity_ids=[names[1 - i].id],
                        ),
                    ),
                    dict(
                        operation="entity_update",
                        input=dict(
                            request_id=uuid4(),
                            work_id=source[0],
                            entity_id=names[i].id,
                            expected_version=1,
                            type="character",
                            canonical_name="修订",
                            aliases=[],
                            note=None,
                        ),
                    ),
                ],
            )
        )
        for i, job in enumerate(jobs)
    ]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(service.checkpoint, requests))
    assert len(results) == 2 and all(
        service.get(AnalysisJobGet(**key(j))).counts.processed == 3 for j in jobs
    )


@pytest.mark.parametrize("operation", ["pause", "complete", "revisit"])
def test_checkpoint_competes_with_other_job_mutations(
    service: AnalysisService, source: tuple[UUID, list[SourceRange]], operation: str
) -> None:
    job = create(service, source, target=dict(kind="ranges", source_ranges=[source[1][0]]))
    job = checkpoint(service, job, source[1][0])
    service.assets.create_style_guide(
        StyleGuideCreate(request_id=uuid4(), work_id=source[0], scope_note="样本", entries=[])
    )
    batch = AnalysisCheckpoint(
        **mutate(
            job, source_range=source[1][0], writes=[], outcome_note="再次核对", recovery=recovery()
        )
    )
    barrier = Barrier(2)

    def run(which: str) -> str:
        barrier.wait(timeout=10)
        try:
            if which == "checkpoint":
                service.checkpoint(batch)
            elif which == "pause":
                service.update(
                    AnalysisJobUpdate(**mutate(job, status="paused", recovery=recovery()))
                )
            elif which == "complete":
                service.complete(
                    AnalysisJobComplete(
                        **mutate(
                            job,
                            recovery=recovery(),
                            style_guide_version=1,
                            calibration_note="校准",
                            limitations=None,
                        )
                    )
                )
            else:
                service.mark(
                    CoverageMark(
                        **mutate(
                            job, source_range=source[1][0], status="needs_revisit", reason="复核"
                        )
                    )
                )
            return "ok"
        except ServiceError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(run, ["checkpoint", operation])) == ["VERSION_CONFLICT", "ok"]


def test_commit_failure_does_not_publish_checkpoint(
    service: AnalysisService, source: tuple[UUID, list[SourceRange]]
) -> None:
    """延迟约束在 COMMIT 才失败，结果快照与全部业务变更仍须一起撤销。"""
    job = create(service, source)
    child = TagCreate(
        request_id=uuid4(), namespace=uuid4().hex, name="提交阶段", description="定义"
    )
    request = AnalysisCheckpoint.model_validate(
        mutate(
            job,
            source_range=source[1][0],
            recovery=recovery(),
            writes=[dict(operation="tag_create", input=child)],
        )
    )
    with service.database.engine.begin() as connection:
        connection.execute(
            text(
                """CREATE FUNCTION fail_commit() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN IF NEW.request_id = '"""
                + str(request.request_id)
                + """'::uuid THEN
        RAISE EXCEPTION 'injected commit failure'; END IF; RETURN NEW; END $$;
        CREATE CONSTRAINT TRIGGER fail_commit AFTER UPDATE ON asset_write_requests
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION fail_commit();"""
            )
        )
    try:
        with pytest.raises(DBAPIError):
            service.checkpoint(request)
    finally:
        with service.database.engine.begin() as connection:
            connection.execute(
                text(
                    "DROP TRIGGER fail_commit ON asset_write_requests; DROP FUNCTION fail_commit()"
                )
            )
    assert service.get(AnalysisJobGet(**key(job))) == job
    expect("WRITE_NOT_COMMITTED", lambda: service.assets.write_result(request.request_id))
    expect("WRITE_NOT_COMMITTED", lambda: service.assets.write_result(child.request_id))
    service.checkpoint(request)


def test_database_constraints(
    service: AnalysisService, source: tuple[UUID, list[SourceRange]]
) -> None:
    """数据库拒绝错误状态、无对应完成说明、无原因回看及不存在的段落引用。"""
    job = create(service, source)
    mutations = [
        ("UPDATE analysis_jobs SET version=0 WHERE id=:job", "ck_analysis_jobs_version"),
        (
            "UPDATE analysis_jobs SET status='completed' WHERE id=:job",
            "ck_analysis_jobs_completion",
        ),
        (
            "INSERT INTO analysis_coverage VALUES (:job,:paragraph,'unprocessed',NULL)",
            "ck_analysis_coverage_status",
        ),
        (
            "INSERT INTO analysis_coverage VALUES (:job,:paragraph,'needs_revisit',NULL)",
            "ck_analysis_coverage_reason",
        ),
    ]
    for sql, constraint in mutations:
        with pytest.raises(IntegrityError) as error, service.database.engine.begin() as connection:
            connection.execute(
                text(sql), dict(job=job.id, paragraph=source[1][0].start_paragraph_id)
            )
        assert (
            getattr(getattr(error.value.orig, "diag", None), "constraint_name", None) == constraint
        )
    with pytest.raises(IntegrityError), service.database.engine.begin() as connection:
        connection.execute(
            text("INSERT INTO analysis_coverage VALUES (:job,:paragraph,'read',NULL)"),
            dict(job=job.id, paragraph=uuid4()),
        )
    assert service.get(AnalysisJobGet(**key(job))) == job
