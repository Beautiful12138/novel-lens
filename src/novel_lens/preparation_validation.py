"""准备批次的只读解析与预检；正式提交在持锁后使用相同规则重新解析。"""

from dataclasses import dataclass
from uuid import UUID, uuid5

from pydantic import ValidationError
from sqlalchemy import Connection

from novel_lens.analysis import selected_paragraphs, validate_recovery
from novel_lens.asset_contracts import (
    AnalysisCheckpoint,
    AnnotationContent,
    AnnotationGet,
    AnnotationOut,
    AnnotationReference,
    AnnotationStatus,
)
from novel_lens.assets import AssetService, check_version, range_bounds, scoped_row
from novel_lens.database import Database
from novel_lens.errors import ServiceError
from novel_lens.input_errors import validation_issues
from novel_lens.reference_contracts import (
    BatchIssue,
    BatchValidation,
    MarkInput,
    MarkPatch,
    PrepareBatch,
    ReferenceInput,
    TagInput,
)
from novel_lens.schema import analysis_jobs
from novel_lens.source_coordinates import resolve_range


@dataclass
class ResolvedMark:
    """完整修订或补丁解析出的规范标记；不在数据库外缓存当前状态。"""

    annotation_id: UUID | None
    expected_version: int | None
    content: AnnotationContent
    tags: list[TagInput]
    entity_ids: list[UUID]
    preserved_tag_ids: list[UUID] | None
    original: AnnotationOut | None
    status_only: bool
    status: AnnotationStatus


def add_issue(issues: list[BatchIssue], path: list[str | int], error: ServiceError) -> None:
    details = error.details if isinstance(error.details, dict) else {}
    issues.append(
        BatchIssue(
            path=path,
            code=error.code,
            message=error.message,
            current_version=details.get("current_version"),
        )
    )


def resolve_references(
    conn: Connection,
    work_id: UUID,
    values: list[ReferenceInput],
    path: list[str | int],
    issues: list[BatchIssue],
) -> list[AnnotationReference]:
    """逐处核验归属、端点和包含关系；每个失败引用都有固定字段路径。"""
    output = []
    for index, value in enumerate(values):
        evidence = reading = None
        for field, ref in (
            ("evidence_range", value.evidence_range),
            ("reading_range", value.reading_range or value.evidence_range),
        ):
            try:
                resolved = resolve_range(conn, work_id, ref)
                if field == "evidence_range":
                    evidence = resolved
                else:
                    reading = resolved
            except ServiceError as error:
                add_issue(issues, [*path, index, field], error)
        if evidence is None or reading is None:
            continue
        first, last = range_bounds(conn, work_id, evidence)
        read_first, read_last = range_bounds(conn, work_id, reading)
        if reading.section_id != evidence.section_id or read_first > first or read_last < last:
            add_issue(
                issues,
                [*path, index, "reading_range"],
                ServiceError(
                    "INVALID_RANGE",
                    "连续阅读范围须与证据同章并包含证据",
                ),
            )
            continue
        output.append(
            AnnotationReference(
                evidence_range=evidence,
                reading_range=reading,
                role_note=value.role_note,
            )
        )
    return output


def resolve_mark(
    conn: Connection,
    database: Database,
    work_id: UUID,
    mark: MarkInput | MarkPatch,
    index: int,
    issues: list[BatchIssue],
) -> ResolvedMark | None:
    """补丁以当前版本为前提合并，显式移除按规范证据坐标匹配，引用顺序保留。"""
    path: list[str | int] = ["marks", index]
    original: AnnotationOut | None = None
    if mark.annotation_id is not None:
        try:
            original = AssetService(database, conn).get_annotation(
                AnnotationGet(
                    work_id=work_id,
                    annotation_id=mark.annotation_id,
                )
            )
            assert mark.expected_version is not None
            if original.version != mark.expected_version:
                raise ServiceError(
                    "VERSION_CONFLICT",
                    "标注已被修改，请重新读取",
                    409,
                    {"current_version": original.version},
                )
        except ServiceError as error:
            add_issue(
                issues,
                [
                    *path,
                    "expected_annotation_version"
                    if error.code == "VERSION_CONFLICT"
                    else "annotation_id",
                ],
                error,
            )
            return None
    if isinstance(mark, MarkPatch):
        assert original is not None
        values = original.model_dump(include={"kind", "title", "scope_note", "note"})
        for field in ("kind", "title", "scope_note", "note"):
            if field in mark.model_fields_set:
                values[field] = getattr(mark, field)
        references = list(original.references)
        if mark.references is not None:
            references = resolve_references(
                conn, work_id, mark.references, [*path, "references"], issues
            )
        else:
            removed: set[str] = set()
            for position, raw in enumerate(mark.remove_references):
                try:
                    ref = resolve_range(conn, work_id, raw)
                    key = ref.model_dump_json()
                    if key in removed or not any(r.evidence_range == ref for r in references):
                        raise ServiceError("REFERENCE_NOT_FOUND", "待移除引用不存在或重复移除")
                    removed.add(key)
                    references = [r for r in references if r.evidence_range != ref]
                except ServiceError as error:
                    add_issue(issues, [*path, "remove_references", position], error)
            references.extend(
                resolve_references(
                    conn,
                    work_id,
                    mark.add_references,
                    [*path, "add_references"],
                    issues,
                )
            )
        tag_values = mark.tags or []
        status = mark.status if mark.status is not None else original.status
    else:
        values = mark.model_dump(include={"kind", "title", "scope_note", "note"})
        references = resolve_references(
            conn, work_id, mark.references, [*path, "references"], issues
        )
        tag_values = mark.tags
        status = mark.status
    try:
        content = AnnotationContent.model_validate(values | {"references": references})
    except ValidationError as error:
        for issue in validation_issues(error):
            issues.append(BatchIssue(**(issue | {"path": [*path, *issue["path"]]})))
        return None
    return ResolvedMark(
        annotation_id=mark.annotation_id,
        expected_version=mark.expected_version,
        content=content,
        tags=tag_values,
        entity_ids=original.entity_ids if original else [],
        preserved_tag_ids=(
            original.tag_ids
            if isinstance(mark, MarkPatch) and mark.tags is None and original is not None
            else None
        ),
        original=original,
        status_only=isinstance(mark, MarkPatch)
        and mark.model_fields_set <= {"operation", "annotation_id", "expected_version", "status"},
        status=status,
    )


def inspect_batch(
    conn: Connection,
    database: Database,
    request: PrepareBatch,
) -> tuple[BatchValidation, AnalysisCheckpoint | None, list[ResolvedMark]]:
    """只读预检与正式事务共享；不会创建 UUID、写入回执、锁定资源或推进任务。"""
    issues: list[BatchIssue] = []
    version = None
    try:
        job = scoped_row(conn, analysis_jobs, request.work_id, request.job_id, "JOB_NOT_FOUND")
        version = job["version"]
        check_version(job, request.expected_version)
        if not (
            (job["status"] == "running" and request.reopen_reason is None)
            or (job["status"] == "completed" and request.reopen_reason is not None)
        ):
            raise ServiceError("JOB_STATE_CONFLICT", "任务状态与重开请求不匹配", 409)
    except ServiceError as error:
        add_issue(
            issues,
            ["expected_job_version" if error.code == "VERSION_CONFLICT" else "job_id"],
            error,
        )
    checkpoint = None
    try:
        source = resolve_range(conn, request.work_id, request.source_range)
        checkpoint = AnalysisCheckpoint(
            request_id=uuid5(request.request_id, "checkpoint"),
            work_id=request.work_id,
            job_id=request.job_id,
            expected_version=request.expected_version,
            source_range=source,
            writes=[],
            recovery=request.recovery,
            outcome_note=request.outcome_note,
        )
        selected_paragraphs(conn, checkpoint)
    except ServiceError as error:
        add_issue(issues, ["source_range"], error)
    try:
        validate_recovery(conn, request.work_id, request.recovery)
    except ServiceError as error:
        add_issue(issues, ["recovery"], error)
    resolved = []
    for index, mark in enumerate(request.marks):
        value = resolve_mark(conn, database, request.work_id, mark, index, issues)
        if value is not None:
            resolved.append(value)
    return (
        BatchValidation(valid=not issues, issues=issues, checked_job_version=version),
        checkpoint,
        resolved,
    )
