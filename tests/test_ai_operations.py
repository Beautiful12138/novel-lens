"""真实数据库验证 AI 寻址、局部修订、预检、追溯与跨协议调用。"""

import asyncio
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest
from annotation_fixtures import annotation_fields
from mcp import Client
from pydantic import ValidationError
from sqlalchemy import text
from test_live_http import running_server
from test_mcp_http import call
from test_preparation import batch_for, import_book

from novel_lens.annotation_access import AnnotationAccessService
from novel_lens.annotation_access_contracts import (
    AnnotationDiff,
    AnnotationExport,
    AnnotationHistory,
    AnnotationMany,
    PrepareBatches,
)
from novel_lens.contracts import OrdinalRange, SourceRange
from novel_lens.database import Database
from novel_lens.errors import ServiceError
from novel_lens.library import LibraryService
from novel_lens.preparation import PreparationService
from novel_lens.reference_contracts import (
    LibraryBrowse,
    PreparationInspect,
    PrepareBatch,
    PreparedImport,
    PreparedRead,
    PrepareRead,
    SourceRead,
)
from novel_lens.schema import metadata


def batch_input(service: PreparationService, book: PreparedImport) -> dict[str, Any]:
    return batch_for(service, book).model_dump(mode="json")


def fingerprint(database: Database) -> dict[str, str]:
    """只比较测试库业务摘要，证明预检没有回执、标签、资产或进度副作用。"""
    with database.engine.connect() as conn:
        return {
            name: conn.execute(
                text(
                    f"SELECT md5(coalesce(string_agg(h,'' ORDER BY h),'')) FROM "
                    f"(SELECT md5(to_jsonb(t)::text) h FROM {name} t) q"
                )
            ).scalar_one()
            for name in metadata.tables
        }


def test_ordinal_read_next_request_and_annotation_reverse_lookup(
    database: Database,
    tmp_path: Path,
) -> None:
    service = PreparationService(database, 1024 * 1024)
    book = import_book(service, tmp_path, "第一段\n第二段\n第三段\n第四段")
    request = batch_input(service, book)
    uuid_range = SourceRange.model_validate(request["source_range"])
    ordinal = OrdinalRange(
        work_id=book.work.id, section_id=uuid_range.section_id, start_ordinal=2, end_ordinal=3
    )
    library = LibraryService(database)
    page = library.read(
        SourceRead(
            work_id=book.work.id,
            section_id=uuid_range.section_id,
            start_ordinal=2,
            end_ordinal=3,
            before=1,
            after=1,
            limit=2,
        )
    )
    assert [p.ordinal for p in page.items] == [1, 2]
    assert page.next_request and page.next_request["start_ordinal"] == 2
    following = library.read(SourceRead.model_validate(page.next_request))
    assert [p.ordinal for p in following.items] == [3, 4] and following.next_request is None
    request["source_range"] = ordinal.model_dump(mode="json")
    request["marks"] = [{**annotation_fields([ordinal]), "note": "可检索的转折说明"}]
    result = service.batch(PrepareBatch.model_validate(request))
    ann = result.annotations[0]
    assert isinstance(ann.references[0].evidence_range, SourceRange)
    matches = library.browse(
        LibraryBrowse(
            view="annotations",
            work_id=book.work.id,
            query="转折说明",
            source_range=ordinal,
            format="full",
        )
    )
    assert matches.annotations and [a.id for a in matches.annotations.items] == [ann.id]
    outside = ordinal.model_copy(update={"start_ordinal": 4, "end_ordinal": 4})
    no_match = library.browse(
        LibraryBrowse(view="annotations", work_id=book.work.id, source_range=outside)
    )
    assert no_match.annotations and not no_match.annotations.items
    with pytest.raises(ServiceError, match="段号"):
        library.read(
            SourceRead(
                work_id=book.work.id,
                section_id=uuid_range.section_id,
                start_ordinal=2,
                end_ordinal=5,
            )
        )
    with pytest.raises(ValidationError):
        SourceRead(
            work_id=book.work.id,
            section_id=uuid_range.section_id,
            start_ordinal=1,
            end_ordinal=2,
            start_paragraph_id=uuid4(),
            end_paragraph_id=uuid4(),
        )


def test_patch_preserves_references_tags_versions_and_status(
    database: Database, tmp_path: Path
) -> None:
    service = PreparationService(database, 1024 * 1024)
    access = AnnotationAccessService(database)
    book = import_book(service, tmp_path, "\n".join(f"段落{i}" for i in range(1, 16)))
    request = batch_input(service, book)
    section_id = request["source_range"]["section_id"]
    refs = [
        dict(work_id=str(book.work.id), section_id=section_id, start_ordinal=i, end_ordinal=i)
        for i in range(1, 15)
    ]
    request["marks"] = [
        {
            **annotation_fields(refs),
            "note": "原说明",
            "tags": [{"namespace": str(uuid4()), "name": "保留标签", "description": "定义"}],
        }
    ]
    saved = service.batch(PrepareBatch.model_validate(request))
    original = saved.annotations[0]
    # 已处理范围仍可回读修订，外层任务版本与标注版本分别命名。
    edit = {
        **request,
        "request_id": str(uuid4()),
        "expected_job_version": saved.version,
        "marks": [
            {
                "operation": "patch",
                "annotation_id": str(original.id),
                "expected_annotation_version": original.version,
                "note": "修订说明",
            }
        ],
    }
    edit.pop("expected_version")
    updated = service.batch(PrepareBatch.model_validate(edit)).annotations[0]
    assert updated.note == "修订说明" and updated.version == original.version + 1
    assert updated.references == original.references and updated.tag_ids == original.tag_ids
    assert service.batch(PrepareBatch.model_validate(edit)).replayed
    # 显式引用增删保留其他顺序；省略 tags 不重建或清空共享关联。
    edit.update(request_id=str(uuid4()), expected_job_version=saved.version + 1)
    edit["marks"] = [
        {
            "operation": "patch",
            "annotation_id": str(original.id),
            "expected_annotation_version": updated.version,
            "remove_references": [refs[0]],
            "add_references": [
                {
                    "evidence_range": {**refs[0], "start_ordinal": 15, "end_ordinal": 15},
                    "role_note": "新增证据",
                }
            ],
        }
    ]
    changed = service.batch(PrepareBatch.model_validate(edit)).annotations[0]
    assert changed.references[:-1] == original.references[1:] and len(changed.references) == 14
    assert changed.tag_ids == original.tag_ids
    edit.update(request_id=str(uuid4()), expected_job_version=saved.version + 2)
    edit["marks"] = [
        {
            "operation": "patch",
            "annotation_id": str(original.id),
            "expected_annotation_version": changed.version,
            "status": "withdrawn",
        }
    ]
    withdrawn = service.batch(PrepareBatch.model_validate(edit)).annotations[0]
    assert withdrawn.status == "withdrawn" and withdrawn.version == changed.version + 1
    assert withdrawn.references == changed.references
    detail = access.many(AnnotationMany(work_id=book.work.id, annotation_ids=[original.id]))
    assert len(detail.items[0].locations) == 14
    assert detail.items[0].locations[-1].start_ordinal == 15
    with pytest.raises(ValidationError):
        PrepareBatch.model_validate(edit | {"expected_version": edit["expected_job_version"]})
    edit.update(request_id=str(uuid4()), expected_job_version=saved.version + 3)
    edit["marks"][0]["expected_annotation_version"] = original.version
    before = fingerprint(database)
    with pytest.raises(ServiceError) as conflict:
        service.batch(PrepareBatch.model_validate(edit))
    assert conflict.value.code == "VERSION_CONFLICT"
    assert conflict.value.details["issues"][0]["path"] == [
        "marks",
        0,
        "expected_annotation_version",
    ]
    assert fingerprint(database) == before


def test_validation_is_read_only_and_reports_each_reference(
    database: Database, tmp_path: Path
) -> None:
    service = PreparationService(database, 1024 * 1024)
    book = import_book(service, tmp_path, "甲\n乙\n丙")
    other = import_book(service, tmp_path, "他作")
    raw = batch_input(service, book)
    ref = raw["source_range"]
    bad1 = {**ref, "end_paragraph_id": str(uuid4())}
    bad2 = {**ref, "work_id": str(other.work.id)}
    raw["marks"] = [
        {**annotation_fields([bad1]), "note": "甲"},
        {**annotation_fields([bad2]), "note": "乙"},
    ]
    request = PrepareBatch.model_validate(raw)
    before = fingerprint(database)
    checked = service.validate(request)
    assert not checked.valid
    assert {issue.path[1] for issue in checked.issues if issue.path[0] == "marks"} == {0, 1}
    assert any(
        issue.path == ["marks", 0, "references", 0, "evidence_range"] for issue in checked.issues
    )
    assert fingerprint(database) == before
    with pytest.raises(ServiceError):
        service.batch(request)
    assert fingerprint(database) == before
    # 两条不同观察共用证据合法；单条重复证据拒绝。
    raw["marks"] = [
        {**annotation_fields([ref]), "note": "不同观察一"},
        {**annotation_fields([ref]), "note": "不同观察二"},
    ]
    valid = PrepareBatch.model_validate(raw)
    assert service.validate(valid).valid and fingerprint(database) == before
    assert len(service.batch(valid).annotations) == 2
    committed = fingerprint(database)
    replay = service.validate(valid)
    assert replay.valid and replay.already_committed
    changed = valid.model_copy(update={"outcome_note": "不同输入"})
    conflict = service.validate(changed)
    assert not conflict.valid and conflict.issues[0].code == "REQUEST_CONFLICT"
    assert fingerprint(database) == committed


def test_history_diff_and_missing_versions_are_explicit(database: Database, tmp_path: Path) -> None:
    service = PreparationService(database, 1024 * 1024)
    access = AnnotationAccessService(database)
    book = import_book(service, tmp_path)
    raw = batch_input(service, book)
    raw["marks"] = [{**annotation_fields([raw["source_range"]]), "note": "旧版本"}]
    first = service.batch(PrepareBatch.model_validate(raw))
    ann = first.annotations[0]
    raw.update(request_id=str(uuid4()), expected_version=first.version)
    raw["marks"] = [
        {
            "operation": "patch",
            "annotation_id": str(ann.id),
            "expected_annotation_version": ann.version,
            "note": "新版本",
        }
    ]
    service.batch(PrepareBatch.model_validate(raw))
    history = access.history(AnnotationHistory(work_id=book.work.id, annotation_id=ann.id, limit=1))
    assert history.history_complete and history.available_version_count == 2 and history.next_cursor
    old = access.history(AnnotationHistory(work_id=book.work.id, annotation_id=ann.id, version=1))
    assert old.items[0].snapshot == ann
    diff = access.diff(
        AnnotationDiff(work_id=book.work.id, annotation_id=ann.id, from_version=1, to_version=2)
    )
    assert [(c.field, c.before, c.after) for c in diff.changes] == [("note", "旧版本", "新版本")]
    with database.engine.begin() as conn:
        conn.execute(
            text(
                "DELETE FROM asset_write_requests WHERE response->'result'->>'id'=:id "
                "AND response->'result'->>'version'='1'"
            ),
            {"id": str(ann.id)},
        )
    gap = access.history(AnnotationHistory(work_id=book.work.id, annotation_id=ann.id))
    assert not gap.history_complete
    with pytest.raises(ServiceError) as missing:
        access.history(AnnotationHistory(work_id=book.work.id, annotation_id=ann.id, version=1))
    assert missing.value.code == "VERSION_NOT_AVAILABLE"


def test_batch_history_export_snapshot_and_work_isolation(
    database: Database, tmp_path: Path
) -> None:
    service = PreparationService(database, 1024 * 1024)
    access = AnnotationAccessService(database)
    book = import_book(service, tmp_path)
    raw = batch_input(service, book)
    raw["marks"] = [
        {**annotation_fields([raw["source_range"]]), "note": "一"},
        {**annotation_fields([raw["source_range"]]), "note": "二"},
    ]
    first = service.batch(PrepareBatch.model_validate(raw))
    raw.update(request_id=str(uuid4()), expected_version=first.version, marks=[])
    second = service.batch(PrepareBatch.model_validate(raw))
    listing = access.batches(PrepareBatches(work_id=book.work.id, job_id=book.job.id, limit=1))
    assert listing.next_cursor and listing.items[0].request_id == first.request_id
    assert listing.items[0].source_range == SourceRange.model_validate(raw["source_range"])
    assert listing.items[0].metadata_available
    raw.update(request_id=str(uuid4()), expected_version=second.version, marks=[])
    service.batch(PrepareBatch.model_validate(raw))
    final = access.batches(
        PrepareBatches(
            work_id=book.work.id, job_id=book.job.id, limit=1, cursor=listing.next_cursor
        )
    )
    assert [b.request_id for b in final.items] == [second.request_id] and final.next_cursor is None
    ids = [a.id for a in reversed(first.annotations)]
    details = access.many(AnnotationMany(work_id=book.work.id, annotation_ids=ids))
    assert [d.annotation.id for d in details.items] == ids
    export = access.export(AnnotationExport(work_id=book.work.id, job_id=book.job.id, limit=1))
    assert export.total == 2 and export.next_cursor
    other = import_book(service, tmp_path)
    with pytest.raises(ServiceError) as scope:
        access.many(AnnotationMany(work_id=other.work.id, annotation_ids=ids))
    assert scope.value.code == "ANNOTATION_NOT_FOUND"
    raw.update(
        request_id=str(uuid4()),
        expected_version=second.version + 1,
        marks=[
            {
                "operation": "patch",
                "annotation_id": str(ids[0]),
                "expected_annotation_version": 1,
                "note": "变化",
            }
        ],
    )
    service.batch(PrepareBatch.model_validate(raw))
    with pytest.raises(ServiceError) as changed:
        access.export(
            AnnotationExport(
                work_id=book.work.id, job_id=book.job.id, limit=1, cursor=export.next_cursor
            )
        )
    assert changed.value.code == "EXPORT_CHANGED"


def test_read_progress_replay_and_no_processed_downgrade(
    database: Database, tmp_path: Path
) -> None:
    service = PreparationService(database, 1024 * 1024)
    book = import_book(service, tmp_path, "甲\n乙\n丙")
    initial = batch_input(service, book)
    section = initial["source_range"]["section_id"]
    raw = dict(
        work_id=str(book.work.id),
        job_id=str(book.job.id),
        request_id=str(uuid4()),
        expected_job_version=1,
        source_range=dict(
            work_id=str(book.work.id), section_id=section, start_ordinal=1, end_ordinal=2
        ),
        recovery={"next_action": "已读前两段，尚未完成观察"},
    )
    saved = service.read_progress(PrepareRead.model_validate(raw))
    assert saved.job.counts.read == 2 and saved.job.counts.processed == 0
    assert service.read_progress(PrepareRead.model_validate(raw)).replayed
    receipt = service.inspect(PreparationInspect(request_id=saved.request_id))
    assert isinstance(receipt, PreparedRead) and receipt.job.version == saved.job.version
    initial.update(expected_version=saved.job.version, source_range=raw["source_range"])
    finished = service.batch(PrepareBatch.model_validate(initial))
    raw.update(request_id=str(uuid4()), expected_job_version=finished.version)
    reread = service.read_progress(PrepareRead.model_validate(raw))
    assert reread.job.counts.processed == 2 and reread.job.counts.read == 0
    assert reread.job.recovery.next_action == "已读前两段，尚未完成观察"


def test_http_mcp_ai_contract_and_safe_parameter_errors(postgres_url: str, tmp_path: Path) -> None:
    source = tmp_path / "AI接口样例.txt"
    source.write_text("分部：样例\n标题：首章\n第一段。\n第二段。", encoding="utf-8")

    async def exercise(http: httpx.Client) -> None:
        async with Client(str(http.base_url).rstrip("/") + "/mcp") as mcp:
            tools = (await mcp.list_tools()).tools
            schema = http.get("/ai/openapi.json").json()
            assert {op["post"]["operationId"] for op in schema["paths"].values()} == {
                t.name for t in tools
            }

            def references(value: Any) -> None:
                if isinstance(value, dict):
                    if "$ref" in value:
                        assert value["$ref"].rsplit("/", 1)[1] in schema["components"]["schemas"]
                    for child in value.values():
                        references(child)
                elif isinstance(value, list):
                    for child in value:
                        references(child)

            references(schema)
            imported = await call(
                mcp,
                "prepare_import",
                dict(request_id=str(uuid4()), file_path=str(source), name=str(uuid4())),
            )
            wid, jid = imported["work"]["id"], imported["job"]["id"]
            state = await call(mcp, "prepare_status", dict(work_id=wid, job_id=jid))
            ref = state["remaining"]["items"][0]["source_range"]
            args = dict(
                work_id=wid, section_id=ref["section_id"], start_ordinal=1, end_ordinal=2, limit=1
            )
            read = await call(mcp, "source_read", args)
            assert http.post("/source/read", json=args).json() == read
            assert (await call(mcp, "source_read", read["next_request"]))["items"][0][
                "ordinal"
            ] == 2
            batch = dict(
                work_id=wid,
                job_id=jid,
                request_id=str(uuid4()),
                expected_job_version=1,
                source_range=ref,
                marks=[dict(**annotation_fields([ref]), note="样例观察")],
                recovery={"next_action": "继续"},
                outcome_note="已核对",
            )
            validated = await call(mcp, "prepare_validate", batch)
            assert (
                validated["valid"]
                and http.post("/preparation/validate", json=batch).json() == validated
            )
            result = http.post("/preparation/batch", json=batch).json()
            aid = result["annotations"][0]["id"]
            params = dict(work_id=wid, annotation_ids=[aid])
            assert http.post("/annotations/details", json=params).json() == await call(
                mcp, "annotation_get_many", params
            )
            for tool, path, params in [
                (
                    "annotation_history",
                    "/annotations/history",
                    dict(work_id=wid, annotation_id=aid, version=1),
                ),
                (
                    "annotation_diff",
                    "/annotations/diff",
                    dict(work_id=wid, annotation_id=aid, from_version=1, to_version=1),
                ),
                ("annotation_export", "/annotations/export", dict(work_id=wid)),
                ("prepare_batches", "/preparation/batches", dict(work_id=wid, job_id=jid)),
            ]:
                assert http.post(path, json=params).json() == await call(mcp, tool, params)
            bad = {
                "work_id": wid,
                "section_id": ref["section_id"],
                "private-secret-as-field-name": "private-body",
            }
            error = await mcp.call_tool("source_read", bad)
            rest = http.post("/source/read", json=bad)
            assert error.is_error and rest.status_code == 422
            for serialized in [json.dumps(error.structured_content), rest.text]:
                assert "private-secret" not in serialized and "private-body" not in serialized
                assert "extra_forbidden" in serialized

    with running_server(postgres_url, tmp_path, "ai-contract", mcp_profile="business") as http:
        asyncio.run(exercise(http))
