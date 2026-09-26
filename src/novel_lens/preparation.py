"""作品准备的业务入口；每次调用的成果、进度和回执同成同败。"""

from hashlib import sha256
from typing import Any
from uuid import UUID, uuid4, uuid5

from sqlalchemy import Connection, select, text, update
from sqlalchemy.dialects.postgresql import insert

from novel_lens.analysis import AnalysisService, locked_job
from novel_lens.asset_contracts import (
    MAX_ASSET_RESULT_BYTES,
    AnalysisCheckpoint,
    AnalysisCheckpointOut,
    AnalysisJobComplete,
    AnalysisJobCreate,
    AnalysisJobGet,
    AnalysisJobOut,
    AnalysisTarget,
    AnnotationCreate,
    AnnotationOut,
    AnnotationSetStatus,
    AnnotationUpdate,
    CoverageGet,
    Recovery,
)
from novel_lens.assets import AssetService, compact
from novel_lens.catalog import CatalogService, lock_work
from novel_lens.contracts import PartOut, WorkCreate, WorkOut
from novel_lens.database import Database
from novel_lens.embedding import EmbeddingClient
from novel_lens.errors import ServiceError
from novel_lens.importing import ImportService
from novel_lens.local_files import read_source
from novel_lens.reading import work_at
from novel_lens.reference_contracts import (
    CleanupResult,
    PreparationInspect,
    PreparationReceipt,
    PrepareBatch,
    PrepareCleanup,
    PreparedBatch,
    PreparedFinish,
    PreparedImport,
    PreparedStatus,
    PrepareFinish,
    PrepareImport,
    PrepareStatus,
)
from novel_lens.reference_index import index_state
from novel_lens.schema import analysis_jobs, catalog_requests, parts, tags
from novel_lens.work_management import WorkManagementService


class PreparationService:
    """复用目录、标注与任务规则；不生成文学判断，也不驱动外部 AI。"""

    def __init__(self, database: Database, maximum: int, model: EmbeddingClient) -> None:
        self.database = database
        self.maximum = maximum
        self.model = model
        self.catalog = CatalogService(database)

    def import_file(self, request: PrepareImport) -> PreparedImport:
        """读取单份规范 TXT；新建作品、正文、任务与索引待办共用一次事务。

        解析失败不会留下空作品。追加同一来源复用分部和其准备任务；
        回执是提交快照，读取最新进度须使用 status。输入文件始终只读。
        """
        data = read_source(request.file_path, self.maximum)
        digest = sha256(data).hexdigest()
        payload = request.model_dump(mode="json", exclude={"file_path"}) | {"sha256": digest}

        def action(conn: Connection) -> tuple[UUID, dict[str, Any]]:
            if request.work_id is None:
                assert request.name is not None
                created = (
                    CatalogService(self.database, conn)
                    .create(
                        WorkCreate(
                            request_id=uuid5(request.request_id, "work"),
                            name=request.name,
                        )
                    )
                    .result
                )
                assert isinstance(created, WorkOut)
                work_id = created.id
            else:
                work_id = request.work_id
            # 与普通分部导入采用同一顺序，防止异请求重复导入同一来源。
            conn.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:scope,0))"),
                {"scope": f"part-import:{work_id}"},
            )
            existing = (
                conn.execute(
                    select(parts).where(
                        parts.c.work_id == work_id,
                        parts.c.source_sha256 == digest,
                    )
                )
                .mappings()
                .first()
            )
            if existing is None:
                imported = ImportService(self.database, self.maximum, conn).import_source(
                    data,
                    uuid5(request.request_id, "source"),
                    work_id,
                )
                part = imported.part
            else:
                part = PartOut.model_validate(existing)
            job = (
                AnalysisService(self.database, conn)
                .create(
                    AnalysisJobCreate(
                        request_id=uuid5(part.id, "preparation-job"),
                        work_id=work_id,
                        title="原文阅读与标记",
                        goal="阅读全部目标原文，保存帮助创作召回的必要标记",
                        target=AnalysisTarget(kind="part", part_id=part.id),
                        recovery=Recovery(
                            next_action="读取准备状态中的剩余范围，阅读原文后分批保存标记"
                        ),
                    )
                )
                .result
            )
            assert isinstance(job, AnalysisJobOut)
            result = PreparedImport(
                request_id=request.request_id, work=work_at(conn, work_id), part=part, job=job
            )
            return work_id, result.model_dump(mode="json")

        return PreparedImport.model_validate(
            self.catalog.write(
                request.request_id,
                "prepare_import",
                payload,
                action,
            )
        )

    def batch(self, request: PrepareBatch) -> PreparedBatch:
        """完整替换本批标记并推进进度；空标记批次也须提供阅读处理结论。"""
        checkpoint = AnalysisCheckpoint(
            **request.model_dump(exclude={"marks", "request_id", "reopen_reason"}),
            request_id=uuid5(request.request_id, "checkpoint"),
            writes=[],
        )

        def action(conn: Connection) -> tuple[UUID, dict[str, Any]]:
            lock_work(conn, request.work_id)
            # 先排序锁定全部涉及的资源，与旧资产入口共用 advisory namespace。
            resources = [f"job_id:{request.job_id}"]
            resources += [
                f"annotation_id:{m.annotation_id}" for m in request.marks if m.annotation_id
            ]
            resources += [f"tag:{t.namespace}:{t.name}" for m in request.marks for t in m.tags]
            for key in sorted(
                {int.from_bytes(sha256(v.encode()).digest()[:4], signed=True) for v in resources}
            ):
                conn.execute(text("SELECT pg_advisory_xact_lock(71002,:k)"), {"k": key})
            job = locked_job(conn, checkpoint)
            if job["status"] == "completed" and request.reopen_reason is not None:
                # 先在本事务恢复运行态，版本仅由后面的 checkpoint 推进一次。
                # 后续任何标记、范围或回执校验失败都会恢复旧完成态及其说明。
                conn.execute(
                    update(analysis_jobs)
                    .where(analysis_jobs.c.id == request.job_id)
                    .values(status="running", completion=None)
                )
            elif job["status"] != "running" or request.reopen_reason is not None:
                raise ServiceError(
                    "JOB_STATE_CONFLICT",
                    "已完成任务须提供 reopen_reason 修订；运行中任务不接受重开原因",
                    409,
                )
            writer = AssetService(self.database, conn)
            tag_ids: dict[tuple[str, str], UUID] = {}
            tag_values = {(t.namespace, t.name): t for m in request.marks for t in m.tags}
            for tag_key, tag in sorted(tag_values.items()):
                identifier = conn.execute(
                    insert(tags)
                    .values(
                        id=uuid4(),
                        namespace=tag.namespace,
                        name=tag.name,
                        description=tag.description,
                        aliases=[],
                    )
                    .on_conflict_do_nothing(index_elements=[tags.c.namespace, tags.c.name])
                    .returning(tags.c.id)
                ).scalar_one_or_none()
                if identifier is not None:
                    conn.execute(
                        text("INSERT INTO preparation_tags(work_id,tag_id) VALUES(:w,:t)"),
                        {"w": request.work_id, "t": identifier},
                    )
                else:
                    identifier = conn.execute(
                        select(tags.c.id)
                        .where(
                            tags.c.namespace == tag.namespace,
                            tags.c.name == tag.name,
                        )
                        .with_for_update(read=True, key_share=True)
                    ).scalar_one()
                tag_ids[tag_key] = identifier
            annotations: list[AnnotationOut] = []
            for ordinal, mark in enumerate(request.marks):
                values = dict(
                    request_id=uuid5(request.request_id, f"mark:{ordinal}"),
                    work_id=request.work_id,
                    source_ranges=mark.source_ranges,
                    note=mark.note,
                    tag_ids=[tag_ids[(t.namespace, t.name)] for t in mark.tags],
                )
                if mark.annotation_id is None:
                    annotation = writer.create_annotation(
                        AnnotationCreate.model_validate(values)
                    ).result
                else:
                    annotation = writer.update_annotation(
                        AnnotationUpdate.model_validate(
                            values
                            | dict(
                                annotation_id=mark.annotation_id,
                                expected_version=mark.expected_version,
                            )
                        )
                    ).result
                assert isinstance(annotation, AnnotationOut)
                if annotation.status != mark.status:
                    annotation = writer.set_annotation_status(
                        AnnotationSetStatus(
                            request_id=uuid5(request.request_id, f"status:{ordinal}"),
                            work_id=request.work_id,
                            annotation_id=annotation.id,
                            expected_version=annotation.version,
                            status=mark.status,
                        )
                    ).result
                    assert isinstance(annotation, AnnotationOut)
                annotations.append(annotation)
            progress = AnalysisService(self.database, conn).checkpoint(checkpoint).result
            assert isinstance(progress, AnalysisCheckpointOut)
            result = PreparedBatch(
                request_id=request.request_id,
                work_id=request.work_id,
                job_id=request.job_id,
                version=progress.version,
                annotations=annotations,
            )
            payload = result.model_dump(mode="json")
            if len(compact(payload).encode()) > MAX_ASSET_RESULT_BYTES:
                raise ServiceError("ASSET_TOO_LARGE", "完整批次回执超过 1 MiB", 413)
            return request.work_id, payload

        return PreparedBatch.model_validate(
            self.catalog.write(
                request.request_id,
                "prepare_batch",
                request.model_dump(mode="json"),
                action,
            )
        )

    def status(self, request: PrepareStatus) -> PreparedStatus:
        """在同一快照返回剩余阅读范围、当前任务版本和索引覆盖；不推进任务。"""
        with self.database.engine.connect().execution_options(
            isolation_level="REPEATABLE READ"
        ) as conn:
            analysis = AnalysisService(self.database, conn)
            indexes = index_state(conn, request.work_id, self.model.contract_id)
            unfinished_parts = conn.execute(
                text("""
                SELECT count(*) FROM parts p WHERE p.work_id=:w AND NOT EXISTS (
                  SELECT 1 FROM analysis_jobs j WHERE j.work_id=p.work_id
                    AND j.part_id=p.id AND j.target_kind='part' AND j.status='completed'
                )
            """),
                {"w": request.work_id},
            ).scalar_one()
            return PreparedStatus(
                job=analysis.get(AnalysisJobGet(work_id=request.work_id, job_id=request.job_id)),
                remaining=analysis.coverage(
                    CoverageGet(**request.model_dump(), remaining_only=True)
                ),
                indexes=indexes,
                work_ready=indexes.state == "ready" and unfinished_parts == 0,
            )

    def finish(self, request: PrepareFinish) -> PreparedFinish:
        """锁定任务后核验索引与阅读覆盖；未完成时整次拒绝，成功回执可重放。"""
        completion = AnalysisJobComplete(
            request_id=uuid5(request.request_id, "complete"),
            work_id=request.work_id,
            job_id=request.job_id,
            expected_version=request.expected_version,
            recovery=request.recovery,
            calibration_note=request.note,
            limitations=None,
        )

        def action(conn: Connection) -> tuple[UUID, dict[str, Any]]:
            lock_work(conn, request.work_id)
            # 先取得资产入口的资源锁，再锁任务，最后锁队列，保持批次相同顺序。
            from novel_lens.assets import lock_write_batch

            lock_write_batch(conn, completion)
            locked_job(conn, completion, running=True)
            conn.execute(
                text("SELECT revision FROM reference_queue WHERE work_id=:w FOR UPDATE"),
                {"w": request.work_id},
            )
            state = index_state(conn, request.work_id, self.model.contract_id)
            if state.state != "ready":
                raise ServiceError(
                    "PREPARATION_INDEX_PENDING",
                    "原文或标记索引尚未就绪",
                    409,
                    state.model_dump(mode="json"),
                )
            job = AnalysisService(self.database, conn).complete(completion).result
            assert isinstance(job, AnalysisJobOut)
            return request.work_id, PreparedFinish(
                request_id=request.request_id, job=job
            ).model_dump(mode="json")

        return PreparedFinish.model_validate(
            self.catalog.write(
                request.request_id,
                "prepare_finish",
                request.model_dump(mode="json"),
                action,
            )
        )

    def receipt(
        self, request: PreparationReceipt
    ) -> PreparedImport | PreparedBatch | PreparedFinish:
        """查询已提交快照；未找到不证明正在执行的请求失败，应原键原输入重试。"""
        with self.database.engine.connect() as conn:
            row = (
                conn.execute(
                    select(catalog_requests).where(
                        catalog_requests.c.request_id == request.request_id,
                        catalog_requests.c.operation.in_(
                            ["prepare_import", "prepare_batch", "prepare_finish"]
                        ),
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                raise ServiceError(
                    "PREPARATION_NOT_COMMITTED", "尚无已提交回执，原请求可能仍在执行", 404
                )
            models: dict[str, type[PreparedImport] | type[PreparedBatch] | type[PreparedFinish]] = {
                "prepare_import": PreparedImport,
                "prepare_batch": PreparedBatch,
                "prepare_finish": PreparedFinish,
            }
            return models[row["operation"]].model_validate(row["response"] | {"replayed": True})

    def inspect(
        self, request: PreparationInspect
    ) -> PreparedStatus | PreparedImport | PreparedBatch | PreparedFinish:
        """日常工具内统一查询当前状态或历史回执，二者保持各自的时间语义。"""
        if request.request_id is not None:
            return self.receipt(PreparationReceipt(request_id=request.request_id))
        return self.status(PrepareStatus.model_validate(request.model_dump(exclude={"request_id"})))

    def cleanup(self, request: PrepareCleanup) -> CleanupResult:
        """明确放弃时删除整部作品；普通批次失败不调用本操作。"""
        WorkManagementService(self.database).delete(
            request.work_id, confirm_name=request.confirm_work_name, remove_preparation_tags=True
        )
        return CleanupResult(work_id=request.work_id, deleted=True)
