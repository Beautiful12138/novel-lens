"""分析任务与段落进度；文学判断由调用方提交，checkpoint 共用资产事务。"""

import json
from contextlib import nullcontext
from hashlib import sha256
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Connection, Select, and_, func, literal, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import RowMapping
from sqlalchemy.sql.selectable import Join

from novel_lens.asset_contracts import (
    MAX_ASSET_RESULT_BYTES,
    AnalysisCheckpoint,
    AnalysisCheckpointOut,
    AnalysisJobComplete,
    AnalysisJobCreate,
    AnalysisJobGet,
    AnalysisJobList,
    AnalysisJobModify,
    AnalysisJobOut,
    AnalysisJobSummary,
    AnalysisJobUpdate,
    AnalysisTarget,
    AssetWriteOut,
    CheckpointReceipt,
    CoverageCounts,
    CoverageGet,
    CoverageItem,
    CoverageMark,
    CoveragePage,
    Recovery,
)
from novel_lens.assets import (
    AssetService,
    check_version,
    compact,
    page_rows,
    range_bounds,
    scoped_row,
)
from novel_lens.catalog import part_at
from novel_lens.contracts import Page, SourceRange
from novel_lens.cursors import decode_cursor, encode_cursor
from novel_lens.database import Database
from novel_lens.errors import ServiceError
from novel_lens.reading import section_at, work_at
from novel_lens.schema import (
    analysis_coverage as coverage,
)
from novel_lens.schema import (
    analysis_jobs as jobs,
)
from novel_lens.schema import (
    analysis_targets as targets,
)
from novel_lens.schema import (
    paragraphs,
    sections,
    style_guides,
)


def validate_recovery(connection: Connection, work_id: UUID, value: Recovery) -> None:
    """接续证据可在目标之外，但不能跨作品或引用无效坐标。"""
    for collection in (value.facts, value.open_questions):
        for item in collection:
            for ref in item.source_ranges:
                range_bounds(connection, work_id, ref)
    if value.next_range is not None:
        range_bounds(connection, work_id, value.next_range)


def normalize_target(
    connection: Connection, work_id: UUID, target: AnalysisTarget
) -> list[dict[str, Any]]:
    """固定任务目标，按原文顺序合并同章节重叠或相邻区间。"""
    values: list[dict[str, Any]] = []
    if target.kind in ("whole_work", "part"):
        if target.part_id is not None:
            part_at(connection, work_id, target.part_id)
        first, last = paragraphs.alias(), paragraphs.alias()
        values = [
            dict(row)
            for row in connection.execute(
                select(
                    sections.c.id.label("section_id"),
                    sections.c.ordinal.label("section_order"),
                    first.c.id.label("start_paragraph_id"),
                    last.c.id.label("end_paragraph_id"),
                    first.c.ordinal.label("start_ordinal"),
                    last.c.ordinal.label("end_ordinal"),
                )
                .select_from(
                    sections.join(
                        first, and_(first.c.section_id == sections.c.id, first.c.ordinal == 1)
                    ).join(
                        last,
                        and_(
                            last.c.section_id == sections.c.id,
                            last.c.ordinal == sections.c.paragraph_count,
                        ),
                    )
                )
                .where(
                    sections.c.work_id == work_id,
                    literal(True)
                    if target.part_id is None
                    else sections.c.part_id == target.part_id,
                )
            ).mappings()
        ]
    else:
        for ref in target.source_ranges:
            start, end = range_bounds(connection, work_id, ref)
            values.append(
                dict(
                    section_id=ref.section_id,
                    section_order=section_at(connection, work_id, ref.section_id).ordinal,
                    start_paragraph_id=ref.start_paragraph_id,
                    end_paragraph_id=ref.end_paragraph_id,
                    start_ordinal=start,
                    end_ordinal=end,
                )
            )
    values.sort(key=lambda v: (v["section_order"], v["start_ordinal"]))
    merged: list[dict[str, Any]] = []
    for value in values:
        if (
            merged
            and merged[-1]["section_id"] == value["section_id"]
            and value["start_ordinal"] <= merged[-1]["end_ordinal"] + 1
        ):
            if value["end_ordinal"] > merged[-1]["end_ordinal"]:
                merged[-1].update(
                    end_ordinal=value["end_ordinal"], end_paragraph_id=value["end_paragraph_id"]
                )
        else:
            merged.append(value.copy())
    if not merged:
        raise ServiceError("INVALID_TARGET", "分析目标须包含至少一个原文段落")
    return [{k: v for k, v in value.items() if k != "section_order"} for value in merged]


def target_membership() -> Join:
    """目标区间不重叠，每个任务段落在连接结果中恰好出现一次。"""
    return targets.join(
        paragraphs,
        and_(
            paragraphs.c.section_id == targets.c.section_id,
            paragraphs.c.ordinal.between(targets.c.start_ordinal, targets.c.end_ordinal),
        ),
    ).outerjoin(
        coverage,
        and_(coverage.c.job_id == targets.c.job_id, coverage.c.paragraph_id == paragraphs.c.id),
    )


def counts_for(connection: Connection, ids: list[UUID]) -> dict[UUID, CoverageCounts]:
    """在同一快照中批量统计任务目标，不按资产数量推断处理进度。"""
    result = {identifier: CoverageCounts() for identifier in ids}
    state = func.coalesce(coverage.c.status, "unprocessed")
    for row in connection.execute(
        select(targets.c.job_id, state.label("state"), func.count().label("count"))
        .select_from(target_membership())
        .where(targets.c.job_id.in_(ids))
        .group_by(targets.c.job_id, state)
    ).mappings():
        setattr(result[row["job_id"]], row["state"], row["count"])
    return result


def job_out(connection: Connection, row: RowMapping) -> AnalysisJobOut:
    """组装完整接续信息并检查可读取性，容量错误交由外层写事务回滚。"""
    refs = []
    if row["target_kind"] == "ranges":
        refs = [
            SourceRange(
                work_id=row["work_id"],
                section_id=ref["section_id"],
                start_paragraph_id=ref["start_paragraph_id"],
                end_paragraph_id=ref["end_paragraph_id"],
            )
            for ref in connection.execute(
                select(targets).where(targets.c.job_id == row["id"]).order_by(targets.c.ordinal)
            ).mappings()
        ]
    counts = counts_for(connection, [row["id"]])[row["id"]]
    result = AnalysisJobOut(
        **dict(row),
        target=AnalysisTarget(kind=row["target_kind"], part_id=row["part_id"], source_ranges=refs),
        counts=counts,
        target_paragraph_count=sum(counts.model_dump().values()),
    )
    if len(compact(result.model_dump(mode="json")).encode()) > MAX_ASSET_RESULT_BYTES:
        raise ServiceError("ASSET_TOO_LARGE", "完整任务详情超过 1 MiB", 413)
    return result


def locked_job(
    connection: Connection, request: AnalysisJobModify, *, running: bool = False
) -> RowMapping:
    """任务锁覆盖后续资产、进度和状态写入；重复请求已在进入前恢复。"""
    row = scoped_row(connection, jobs, request.work_id, request.job_id, "JOB_NOT_FOUND", lock=True)
    check_version(row, request.expected_version)
    if running and row["status"] != "running":
        raise ServiceError("JOB_STATE_CONFLICT", "当前任务状态不允许推进进度", 409)
    return row


def selected_paragraphs(
    connection: Connection, request: CoverageMark | AnalysisCheckpoint
) -> Select[Any]:
    """校验范围完整落在目标内，返回不加载正文的段落选择语句。"""
    start, end = range_bounds(connection, request.work_id, request.source_range)
    ref = request.source_range
    contained = connection.execute(
        select(targets.c.ordinal).where(
            targets.c.job_id == request.job_id,
            targets.c.section_id == ref.section_id,
            targets.c.start_ordinal <= start,
            targets.c.end_ordinal >= end,
        )
    ).first()
    if contained is None:
        raise ServiceError("RANGE_OUTSIDE_TARGET", "进度范围必须完整包含在任务目标内")
    return select(paragraphs.c.id).where(
        paragraphs.c.section_id == ref.section_id, paragraphs.c.ordinal.between(start, end)
    )


def advance_job(connection: Connection, row: RowMapping, **changes: Any) -> AnalysisJobOut:
    """调用方持有任务锁；每次新写入只推进一次版本并检查详情容量。"""
    updated = (
        connection.execute(
            jobs.update()
            .where(jobs.c.id == row["id"])
            .values(version=row["version"] + 1, updated_at=func.clock_timestamp(), **changes)
            .returning(jobs)
        )
        .mappings()
        .one()
    )
    return job_out(connection, updated)


def store_coverage(
    connection: Connection, job_id: UUID, ids: Select[Any], status: str, reason: str | None = None
) -> None:
    """read 只补缺省行；其余转换在持有任务锁并验证后整体覆盖。"""
    # INSERT SELECT 避免大章节生成逐段绑定参数或触及 PostgreSQL 参数数量上限。
    statement = insert(coverage).from_select(
        ["paragraph_id", "job_id", "status", "reason"],
        ids.add_columns(literal(job_id), literal(status), literal(reason)),
    )
    if status == "read":
        statement = statement.on_conflict_do_nothing(
            index_elements=[coverage.c.job_id, coverage.c.paragraph_id]
        )
    else:
        statement = statement.on_conflict_do_update(
            index_elements=[coverage.c.job_id, coverage.c.paragraph_id],
            set_=dict(status=status, reason=reason),
        )
    connection.execute(statement)


class AnalysisService:
    """任务的所有新写入经共享请求账本提交；读取不会改变进度。"""

    def __init__(self, database: Database, connection: Connection | None = None) -> None:
        self.database = database
        self.connection = connection
        self.assets = AssetService(database, connection)

    def create(self, request: AnalysisJobCreate) -> AssetWriteOut:
        """固定去重后的目标，任务进度从零开始，已有作品资产不影响初值。"""

        def action(connection: Connection) -> AnalysisJobOut:
            work_at(connection, request.work_id)
            refs = normalize_target(connection, request.work_id, request.target)
            validate_recovery(connection, request.work_id, request.recovery)
            row = (
                connection.execute(
                    jobs.insert()
                    .values(
                        id=uuid4(),
                        work_id=request.work_id,
                        title=request.title,
                        goal=request.goal,
                        target_kind=request.target.kind,
                        part_id=request.target.part_id,
                        status="running",
                        recovery=request.recovery.model_dump(mode="json"),
                        completion=None,
                        version=1,
                    )
                    .returning(jobs)
                )
                .mappings()
                .one()
            )
            connection.execute(
                targets.insert(),
                [dict(job_id=row["id"], ordinal=i, **ref) for i, ref in enumerate(refs, 1)],
            )
            return job_out(connection, row)

        return self.assets._write(request, "analysis_job_create", action)

    def get(self, request: AnalysisJobGet) -> AnalysisJobOut:
        with (
            nullcontext(self.connection)
            if self.connection is not None
            else self.database.engine.connect().execution_options(isolation_level="REPEATABLE READ")
        ) as connection:
            return job_out(
                connection,
                scoped_row(connection, jobs, request.work_id, request.job_id, "JOB_NOT_FOUND"),
            )

    def list(self, request: AnalysisJobList) -> Page[AnalysisJobSummary]:
        with self.database.engine.connect().execution_options(
            isolation_level="REPEATABLE READ"
        ) as connection:
            work_at(connection, request.work_id)
            query = select(
                jobs.c.id,
                jobs.c.work_id,
                jobs.c.title,
                jobs.c.status,
                jobs.c.version,
                jobs.c.created_at,
                jobs.c.updated_at,
                func.left(jobs.c.goal, 200).label("goal_preview"),
                (func.length(jobs.c.goal) > 200).label("goal_truncated"),
            ).where(jobs.c.work_id == request.work_id)
            if request.status is not None:
                query = query.where(jobs.c.status == request.status)
            rows, cursor = page_rows(connection, jobs, query, request)
            counts = counts_for(connection, [row["id"] for row in rows])
            return Page(
                items=[
                    AnalysisJobSummary(
                        **dict(row),
                        counts=counts[row["id"]],
                        target_paragraph_count=sum(counts[row["id"]].model_dump().values()),
                    )
                    for row in rows
                ],
                next_cursor=cursor,
            )

    def update(self, request: AnalysisJobUpdate) -> AssetWriteOut:
        """完整替换接续信息；已完成任务只允许说明原因后重开并清除完成说明。"""

        def action(connection: Connection) -> AnalysisJobOut:
            row = locked_job(connection, request)
            reopening = row["status"] == "completed"
            if reopening and (request.status != "running" or request.reopen_reason is None):
                raise ServiceError(
                    "JOB_STATE_CONFLICT", "完成的任务须说明重开原因并转为 running", 409
                )
            if not reopening and request.reopen_reason is not None:
                raise ServiceError(
                    "JOB_STATE_CONFLICT", "仅重开已完成任务时接受 reopen_reason", 409
                )
            validate_recovery(connection, request.work_id, request.recovery)
            return advance_job(
                connection,
                row,
                status=request.status,
                completion=None,
                recovery=request.recovery.model_dump(mode="json"),
            )

        return self.assets._write(request, "analysis_job_update", action)

    def mark(self, request: CoverageMark) -> AssetWriteOut:
        """read 不降级进度；待回看范围有未处理段落时整次拒绝。"""

        def action(connection: Connection) -> AnalysisJobOut:
            row = locked_job(connection, request, running=True)
            ids = selected_paragraphs(connection, request)
            if request.status == "needs_revisit":
                missing = connection.execute(
                    ids.where(
                        ~select(coverage.c.paragraph_id)
                        .where(
                            coverage.c.job_id == request.job_id,
                            coverage.c.paragraph_id == paragraphs.c.id,
                        )
                        .exists()
                    ).limit(1)
                ).first()
                if missing is not None:
                    raise ServiceError(
                        "INVALID_COVERAGE_TRANSITION", "未处理段落不能直接标记为待回看"
                    )
            store_coverage(connection, request.job_id, ids, request.status, request.reason)
            return advance_job(connection, row)

        return self.assets._write(request, "coverage_mark", action)

    def checkpoint(self, request: AnalysisCheckpoint) -> AssetWriteOut:
        """批次内的旧请求可重放；新资产、请求快照、覆盖和接续信息同成同败。"""

        def action(connection: Connection) -> AnalysisCheckpointOut:
            row = locked_job(connection, request, running=True)
            ids = selected_paragraphs(connection, request)
            validate_recovery(connection, request.work_id, request.recovery)
            writer = AssetService(self.database, connection)
            for item in request.writes:
                if getattr(item.input, "work_id", request.work_id) != request.work_id:
                    raise ServiceError("INVALID_RANGE", "checkpoint 子操作必须属于任务作品")
            # discriminated union 限定操作集合；字段保留原模型的 fields_set 与指纹语义。
            for item in request.writes:
                match item.operation:
                    case "tag_create":
                        writer.create_tag(item.input)
                    case "tag_update":
                        writer.update_tag(item.input)
                    case "annotation_set_status":
                        writer.set_annotation_status(item.input)
                    case "annotation_create":
                        writer.create_annotation(item.input)
                    case "annotation_update":
                        writer.update_annotation(item.input)
                    case "entity_create":
                        writer.create_entity(item.input)
                    case "entity_update":
                        writer.update_entity(item.input)
                    case "relation_create":
                        writer.create_relation(item.input)
                    case "relation_update":
                        writer.update_relation(item.input)
                    case "relation_set_status":
                        writer.set_relation_status(item.input)
                    case "style_guide_create":
                        writer.create_style_guide(item.input)
                    case "style_guide_update":
                        writer.update_style_guide(item.input)
            store_coverage(connection, request.job_id, ids, "processed")
            updated = advance_job(
                connection, row, recovery=request.recovery.model_dump(mode="json")
            )
            return AnalysisCheckpointOut(
                work_id=request.work_id,
                job_id=request.job_id,
                version=updated.version,
                source_range=request.source_range,
                writes=[
                    CheckpointReceipt(operation=w.operation, request_id=w.input.request_id)
                    for w in request.writes
                ],
                outcome_note=request.outcome_note,
            )

        return self.assets._write(request, "analysis_checkpoint", action)

    def complete(self, request: AnalysisJobComplete) -> AssetWriteOut:
        """锁定任务与导航并校验进度和版本，保存校准声明但不判断文学质量。"""

        def action(connection: Connection) -> AnalysisJobOut:
            row = locked_job(connection, request, running=True)
            counts = counts_for(connection, [request.job_id])[request.job_id]
            if counts.unprocessed or counts.read or counts.needs_revisit:
                raise ServiceError("JOB_INCOMPLETE", "目标段落尚未全部处理完成", 409)
            guide = (
                connection.execute(
                    select(style_guides)
                    .where(style_guides.c.work_id == request.work_id)
                    .with_for_update()
                )
                .mappings()
                .first()
            )
            if guide is None and request.style_guide_version is not None:
                raise ServiceError("STYLE_GUIDE_NOT_FOUND", "完成任务前须建立风格导航", 404)
            if guide is not None and request.style_guide_version is not None:
                check_version(guide, request.style_guide_version)
            validate_recovery(connection, request.work_id, request.recovery)
            return advance_job(
                connection,
                row,
                status="completed",
                recovery=request.recovery.model_dump(mode="json"),
                completion=dict(
                    style_guide_version=request.style_guide_version,
                    calibration_note=request.calibration_note,
                    limitations=request.limitations,
                ),
            )

        return self.assets._write(request, "analysis_job_complete", action)

    def coverage(self, request: CoverageGet) -> CoveragePage:
        """用窗口函数合并同状态与原因的相邻段落；游标绑定过滤条件及任务版本。"""
        with (
            nullcontext(self.connection)
            if self.connection is not None
            else self.database.engine.connect().execution_options(isolation_level="REPEATABLE READ")
        ) as connection:
            row = scoped_row(connection, jobs, request.work_id, request.job_id, "JOB_NOT_FOUND")
            if request.section_id is not None:
                section_at(connection, request.work_id, request.section_id)
            scope = (
                "coverage:"
                + sha256(
                    compact(request.model_dump(mode="json", exclude={"limit", "cursor"})).encode()
                ).hexdigest()
            )
            after = decode_cursor(request.cursor, scope)
            position = None
            if after is not None:
                try:
                    version, chapter, end = json.loads(after)
                    if any(type(n) is not int or n < 1 for n in (version, chapter, end)):
                        raise ValueError
                except (ValueError, TypeError):
                    raise ServiceError("INVALID_CURSOR", "进度游标无效") from None
                if version != row["version"]:
                    raise ServiceError(
                        "VERSION_CONFLICT",
                        "任务已变化，请从首页重新读取进度",
                        409,
                        details={"current_version": row["version"]},
                    )
                position = (chapter, end)
            state = func.coalesce(coverage.c.status, "unprocessed")
            base = (
                select(
                    paragraphs.c.section_id,
                    sections.c.ordinal.label("section_order"),
                    paragraphs.c.ordinal,
                    state.label("status"),
                    coverage.c.reason,
                    (
                        paragraphs.c.ordinal
                        - func.row_number().over(
                            partition_by=[paragraphs.c.section_id, state, coverage.c.reason],
                            order_by=paragraphs.c.ordinal,
                        )
                    ).label("island"),
                )
                .select_from(
                    target_membership().join(sections, sections.c.id == paragraphs.c.section_id)
                )
                .where(targets.c.job_id == request.job_id)
            )
            if request.section_id is not None:
                base = base.where(paragraphs.c.section_id == request.section_id)
            b = base.subquery()
            groups = (
                select(
                    b.c.section_id,
                    b.c.section_order,
                    b.c.status,
                    b.c.reason,
                    func.min(b.c.ordinal).label("start"),
                    func.max(b.c.ordinal).label("end"),
                )
                .group_by(b.c.section_id, b.c.section_order, b.c.status, b.c.reason, b.c.island)
                .subquery()
            )
            first, last = paragraphs.alias(), paragraphs.alias()
            query = select(
                groups, first.c.id.label("first_id"), last.c.id.label("last_id")
            ).select_from(
                groups.join(
                    first,
                    and_(
                        first.c.section_id == groups.c.section_id, first.c.ordinal == groups.c.start
                    ),
                ).join(
                    last,
                    and_(last.c.section_id == groups.c.section_id, last.c.ordinal == groups.c.end),
                )
            )
            if request.status is not None:
                query = query.where(groups.c.status == request.status)
            if request.remaining_only:
                query = query.where(groups.c.status != "processed")
            if position is not None:
                query = query.where(
                    tuple_(groups.c.section_order, groups.c.start)
                    > tuple_(literal(position[0]), literal(position[1]))
                )
            values = list(
                connection.execute(
                    query.order_by(groups.c.section_order, groups.c.start).limit(request.limit + 1)
                ).mappings()
            )
            more = len(values) > request.limit
            values = values[: request.limit]
            cursor = (
                encode_cursor(
                    scope, compact([row["version"], values[-1]["section_order"], values[-1]["end"]])
                )
                if more
                else None
            )
            return CoveragePage(
                work_id=request.work_id,
                job_id=request.job_id,
                version=row["version"],
                next_cursor=cursor,
                items=[
                    CoverageItem(
                        status=v["status"],
                        reason=v["reason"],
                        source_range=SourceRange(
                            work_id=request.work_id,
                            section_id=v["section_id"],
                            start_paragraph_id=v["first_id"],
                            end_paragraph_id=v["last_id"],
                        ),
                    )
                    for v in values
                ],
            )
