"""批量详情、版本和批次查读；复用提交快照，不合成不存在的编辑历史。"""

import json
from hashlib import sha256
from typing import Any
from uuid import UUID, uuid5

from pydantic import BaseModel
from sqlalchemy import Connection, select, text

from novel_lens.annotation_access_contracts import (
    AnnotationDetails,
    AnnotationDiff,
    AnnotationDifference,
    AnnotationExport,
    AnnotationExportPage,
    AnnotationHistory,
    AnnotationHistoryPage,
    AnnotationMany,
    AnnotationRevision,
    FieldChange,
    PreparationBatchPage,
    PreparationBatchRecord,
    PrepareBatches,
    WrittenAnnotation,
)
from novel_lens.asset_contracts import (
    MAX_ASSET_RESULT_BYTES,
    AnnotationGet,
    AnnotationOut,
    BrowsedAnnotationDetail,
)
from novel_lens.assets import AssetService, compact, scoped_row
from novel_lens.cursors import decode_cursor, encode_cursor
from novel_lens.database import Database
from novel_lens.errors import ServiceError
from novel_lens.reading import searchable_work
from novel_lens.schema import (
    analysis_jobs,
    annotation_tags,
    annotations,
    asset_write_requests,
    catalog_requests,
    parts,
    tags,
)


def bounded[T: BaseModel](value: T) -> T:
    """完整对象不能截断；超限调用方须减小批量或分页数量。"""
    if len(value.model_dump_json().encode()) > MAX_ASSET_RESULT_BYTES:
        raise ServiceError("RESULT_TOO_LARGE", "结果超过 1 MiB，请减小批量或分页数量", 413)
    return value


def page_position(cursor: str | None, scope: str, maximum: int) -> tuple[int, int]:
    """分页固定首请求版本上界；后续提交不混入正在阅读的历史页。"""
    raw = decode_cursor(cursor, scope)
    if raw is None:
        return maximum, 0
    try:
        ceiling, after = json.loads(raw)
        if (
            type(ceiling) is not int
            or type(after) is not int
            or not 0 <= after < ceiling <= maximum
        ):
            raise ValueError
        return ceiling, after
    except (ValueError, TypeError):
        raise ServiceError("INVALID_CURSOR", "历史分页游标无效") from None


class AnnotationAccessService:
    """一次查读使用同一数据库快照；返回正文只在协议响应中，不写入日志。"""

    def __init__(self, database: Database) -> None:
        self.database = database

    def _details(
        self, conn: Connection, work_id: UUID, ids: list[UUID]
    ) -> list[BrowsedAnnotationDetail]:
        assets = AssetService(self.database, conn)
        return [
            assets.browse_annotation_detail(
                AnnotationGet(
                    work_id=work_id,
                    annotation_id=identifier,
                )
            )
            for identifier in ids
        ]

    def many(self, request: AnnotationMany) -> AnnotationDetails:
        with (
            self.database.engine.connect().execution_options(
                isolation_level="REPEATABLE READ"
            ) as conn,
            conn.begin(),
        ):
            searchable_work(conn, request.work_id)
            return bounded(
                AnnotationDetails(
                    work_id=request.work_id,
                    items=self._details(conn, request.work_id, request.annotation_ids),
                )
            )

    def _revisions(
        self,
        conn: Connection,
        work_id: UUID,
        annotation_id: UUID,
    ) -> tuple[AnnotationOut, dict[int, AnnotationRevision]]:
        """只读取同作品同标注的真实快照；当前对象为缺失回执提供可核实的基线。"""
        searchable_work(conn, work_id)
        current = AssetService(self.database, conn).get_annotation(
            AnnotationGet(
                work_id=work_id,
                annotation_id=annotation_id,
            )
        )
        response = asset_write_requests.c.response
        records: dict[int, AnnotationRevision] = {}
        for payload in conn.execute(
            select(response).where(
                response["operation"].astext.in_(
                    ["annotation_create", "annotation_update", "annotation_set_status"]
                ),
                response["result"]["id"].astext == str(annotation_id),
                response["result"]["work_id"].astext == str(work_id),
            )
        ).scalars():
            snapshot = AnnotationOut.model_validate(payload["result"])
            existing = records.get(snapshot.version)
            if existing is not None and existing.snapshot != snapshot:
                raise ServiceError("HISTORY_CONFLICT", "同一版本存在不一致快照，不能自动选择", 409)
            records[snapshot.version] = AnnotationRevision(
                version=snapshot.version,
                title=snapshot.title,
                status=snapshot.status,
                updated_at=snapshot.updated_at,
                request_id=payload["request_id"],
                origin="receipt",
                snapshot=snapshot,
            )
        if current.version not in records:
            records[current.version] = AnnotationRevision(
                version=current.version,
                title=current.title,
                status=current.status,
                updated_at=current.updated_at,
                request_id=None,
                origin="current",
                snapshot=current,
            )
        return current, records

    def history(self, request: AnnotationHistory) -> AnnotationHistoryPage:
        with (
            self.database.engine.connect().execution_options(
                isolation_level="REPEATABLE READ"
            ) as conn,
            conn.begin(),
        ):
            current, records = self._revisions(conn, request.work_id, request.annotation_id)
            if request.version is not None:
                if request.version not in records:
                    raise ServiceError("VERSION_NOT_AVAILABLE", "该版本没有可取得的历史快照", 404)
                items, cursor = [records[request.version]], None
            else:
                scope = f"annotation-history:{request.work_id}:{request.annotation_id}"
                ceiling, after = page_position(request.cursor, scope, current.version)
                versions = sorted(v for v in records if after < v <= ceiling)
                items = [
                    records[v].model_copy(update={"snapshot": None})
                    for v in versions[: request.limit]
                ]
                cursor = (
                    encode_cursor(scope, json.dumps([ceiling, items[-1].version]))
                    if len(versions) > request.limit
                    else None
                )
            return bounded(
                AnnotationHistoryPage(
                    work_id=request.work_id,
                    annotation_id=request.annotation_id,
                    current_version=current.version,
                    available_version_count=len(records),
                    history_complete=set(records) == set(range(1, current.version + 1)),
                    items=items,
                    next_cursor=cursor,
                )
            )

    def diff(self, request: AnnotationDiff) -> AnnotationDifference:
        with (
            self.database.engine.connect().execution_options(
                isolation_level="REPEATABLE READ"
            ) as conn,
            conn.begin(),
        ):
            _, records = self._revisions(conn, request.work_id, request.annotation_id)
            if request.from_version not in records or request.to_version not in records:
                raise ServiceError("VERSION_NOT_AVAILABLE", "比较的版本缺少可取得快照", 404)
            before = records[request.from_version].snapshot
            after = records[request.to_version].snapshot
            assert before is not None and after is not None
            old, new = before.model_dump(mode="json"), after.model_dump(mode="json")
            fields = (
                "kind",
                "title",
                "scope_note",
                "note",
                "references",
                "tag_ids",
                "entity_ids",
                "status",
            )
            return bounded(
                AnnotationDifference(
                    annotation_id=request.annotation_id,
                    from_version=request.from_version,
                    to_version=request.to_version,
                    changes=[
                        FieldChange(field=k, before=old[k], after=new[k])
                        for k in fields
                        if old[k] != new[k]
                    ],
                )
            )

    def export(self, request: AnnotationExport) -> AnnotationExportPage:
        """分页返回当前完整详情；内容或目录变化令旧游标失效，不拼接混合快照。"""
        with (
            self.database.engine.connect().execution_options(
                isolation_level="REPEATABLE READ"
            ) as conn,
            conn.begin(),
        ):
            work = searchable_work(conn, request.work_id)
            query = select(annotations.c.id, annotations.c.version).where(
                annotations.c.work_id == request.work_id
            )
            if request.status is not None:
                query = query.where(annotations.c.status == request.status)
            if request.job_id is not None:
                scoped_row(conn, analysis_jobs, request.work_id, request.job_id, "JOB_NOT_FOUND")
                query = query.where(
                    annotations.c.id.in_(
                        text("""
                    SELECT DISTINCT (a->>'id')::uuid
                    FROM catalog_requests r CROSS JOIN LATERAL
                      jsonb_array_elements(r.response->'annotations') a
                    WHERE r.work_id=:export_work AND r.operation='prepare_batch'
                      AND r.response->>'job_id'=:export_job
                """)
                    ).params(export_work=request.work_id, export_job=str(request.job_id))
                )
            rows = conn.execute(query.order_by(annotations.c.id)).tuples().all()
            # 摘要只读取身份与版本；标注正文仍按页读取，标签/目录改名也会使导出失效。
            scope_tags = (
                conn.execute(
                    select(tags.c.id, tags.c.version)
                    .where(
                        tags.c.id.in_(
                            select(annotation_tags.c.tag_id).where(
                                annotation_tags.c.annotation_id.in_(
                                    query.with_only_columns(annotations.c.id)
                                )
                            )
                        )
                    )
                    .order_by(tags.c.id)
                )
                .tuples()
                .all()
            )
            directory = (
                conn.execute(
                    select(parts.c.id, parts.c.version)
                    .where(parts.c.work_id == request.work_id)
                    .order_by(parts.c.id)
                )
                .tuples()
                .all()
            )
            snapshot = sha256(
                repr((work.version, list(rows), list(scope_tags), list(directory))).encode()
            ).hexdigest()
            scope = "annotation-export:" + compact(
                request.model_dump(mode="json", exclude={"cursor", "limit"})
            )
            after: UUID | None = None
            raw = decode_cursor(request.cursor, scope)
            if raw is not None:
                try:
                    saved, identifier = json.loads(raw)
                    after = UUID(identifier)
                except (ValueError, TypeError, AttributeError):
                    raise ServiceError("INVALID_CURSOR", "导出游标无效") from None
                if saved != snapshot:
                    raise ServiceError("EXPORT_CHANGED", "导出内容已变化，请从第一页重新导出", 409)
            ids = [identifier for identifier, _ in rows if after is None or identifier > after]
            selected = ids[: request.limit]
            cursor = (
                encode_cursor(scope, json.dumps([snapshot, str(selected[-1])]))
                if len(ids) > request.limit
                else None
            )
            return bounded(
                AnnotationExportPage(
                    work_id=request.work_id,
                    items=self._details(conn, request.work_id, selected),
                    snapshot=snapshot,
                    total=len(rows),
                    next_cursor=cursor,
                )
            )

    def batches(self, request: PrepareBatches) -> PreparationBatchPage:
        """按任务版本列已提交批次；旧回执缺少的元数据明确标记，不猜测正文范围。"""
        with (
            self.database.engine.connect().execution_options(
                isolation_level="REPEATABLE READ"
            ) as conn,
            conn.begin(),
        ):
            searchable_work(conn, request.work_id)
            job = scoped_row(conn, analysis_jobs, request.work_id, request.job_id, "JOB_NOT_FOUND")
            scope = f"preparation-batches:{request.work_id}:{request.job_id}"
            ceiling, after = page_position(request.cursor, scope, job["version"])
            result = catalog_requests.c.response
            rows = conn.execute(
                select(catalog_requests.c.request_id, result)
                .where(
                    catalog_requests.c.work_id == request.work_id,
                    catalog_requests.c.operation == "prepare_batch",
                    result["job_id"].astext == str(request.job_id),
                    result["version"].as_integer() > after,
                    result["version"].as_integer() <= ceiling,
                )
                .order_by(result["version"].as_integer())
                .limit(request.limit + 1)
            ).all()
            page = rows[: request.limit]
            keys = [uuid5(row.request_id, "checkpoint") for row in page]
            checkpoints = dict(
                conn.execute(
                    select(
                        asset_write_requests.c.request_id, asset_write_requests.c.response
                    ).where(asset_write_requests.c.request_id.in_(keys))
                )
                .tuples()
                .all()
            )
            items = []
            for identifier, payload in page:
                checkpoint: dict[str, Any] = checkpoints.get(
                    uuid5(identifier, "checkpoint"), {}
                ).get("result", {})
                items.append(
                    PreparationBatchRecord(
                        request_id=identifier,
                        previous_job_version=payload["version"] - 1,
                        job_version=payload["version"],
                        source_range=checkpoint.get("source_range"),
                        outcome_note=checkpoint.get("outcome_note"),
                        metadata_available=bool(checkpoint),
                        annotations=[
                            WrittenAnnotation(annotation_id=a["id"], version=a["version"])
                            for a in payload["annotations"]
                        ],
                    )
                )
            cursor = (
                encode_cursor(scope, json.dumps([ceiling, items[-1].job_version]))
                if len(rows) > request.limit
                else None
            )
            return bounded(
                PreparationBatchPage(
                    work_id=request.work_id,
                    job_id=request.job_id,
                    through_job_version=ceiling,
                    items=items,
                    next_cursor=cursor,
                )
            )
