"""分析资产的事务、定位与查询；不执行文学判断，不修改原文或分析进度。"""

import json
from collections.abc import Callable
from datetime import datetime
from hashlib import sha256
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Connection, Select, Table, func, literal, or_, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import IntegrityError

from novel_lens.asset_contracts import (
    MAX_ASSET_RESULT_BYTES,
    AnnotationCreate,
    AnnotationGet,
    AnnotationList,
    AnnotationOut,
    AnnotationSummary,
    AnnotationUpdate,
    AssetWriteOut,
    TagCreate,
    TagList,
    TagOut,
    TagSearch,
)
from novel_lens.contracts import ListRequest, Page, SourceRange
from novel_lens.cursors import decode_cursor, encode_cursor
from novel_lens.database import Database
from novel_lens.errors import ServiceError
from novel_lens.reading import paragraph_at, section_at, work_at
from novel_lens.schema import (
    annotation_ranges as ranges,
)
from novel_lens.schema import (
    annotation_tags as links,
)
from novel_lens.schema import (
    annotations,
    paragraphs,
    tags,
)
from novel_lens.schema import (
    asset_write_requests as requests,
)


def compact(value: Any) -> str:
    """与 MCP 结果容量一致的紧凑 UTF-8 JSON 表示，并固定字典键顺序。"""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def range_bounds(connection: Connection, work_id: UUID, value: SourceRange) -> tuple[int, int]:
    """校验引用归属和顺序；返回端点序号用于位置相交过滤。"""
    if value.work_id != work_id:
        raise ServiceError("INVALID_RANGE", "引用范围必须属于指定作品")
    section_at(connection, work_id, value.section_id)
    first = paragraph_at(connection, value.section_id, value.start_paragraph_id)
    last = paragraph_at(connection, value.section_id, value.end_paragraph_id)
    if first.ordinal > last.ordinal:
        raise ServiceError("INVALID_RANGE", "范围起点不能晚于终点")
    return first.ordinal, last.ordinal


def require_tags(connection: Connection, ids: list[UUID]) -> None:
    if ids and len(connection.execute(select(tags.c.id).where(tags.c.id.in_(ids))).all()) != len(
        ids
    ):
        raise ServiceError("TAG_NOT_FOUND", "引用的标签不存在", 404)


def tag_out(row: RowMapping) -> TagOut:
    return TagOut.model_validate(dict(row) | {"full_name": f"{row['namespace']}/{row['name']}"})


def annotation_out(connection: Connection, row: RowMapping) -> AnnotationOut:
    """调用方须提供事务快照或持有该标注的写锁，避免拼接不同版本的关联。"""
    references = (
        connection.execute(
            select(
                ranges.c.work_id,
                ranges.c.section_id,
                ranges.c.start_paragraph_id,
                ranges.c.end_paragraph_id,
            )
            .where(ranges.c.annotation_id == row["id"])
            .order_by(ranges.c.ordinal)
        )
        .mappings()
        .all()
    )
    ids = (
        connection.execute(
            select(links.c.tag_id)
            .where(links.c.annotation_id == row["id"])
            .order_by(links.c.tag_id)
        )
        .scalars()
        .all()
    )
    return AnnotationOut.model_validate(
        dict(row) | {"source_ranges": [dict(r) for r in references], "tag_ids": ids}
    )


def page_rows(
    connection: Connection,
    table: Table,
    query: Select[Any],
    request: ListRequest,
) -> tuple[list[RowMapping], str | None]:
    """创建顺序游标绑定查询类型和全部过滤条件，分页大小不属于过滤条件。"""
    filters = request.model_dump(mode="json", exclude={"limit", "cursor"})
    scope = type(request).__name__ + ":" + sha256(compact(filters).encode()).hexdigest()
    after = decode_cursor(request.cursor, scope)
    if after is not None:
        try:
            timestamp, identifier = after.split("|")
            time = datetime.fromisoformat(timestamp)
            if time.tzinfo is None:
                raise ValueError
            query = query.where(
                tuple_(table.c.created_at, table.c.id)
                > tuple_(literal(time), literal(UUID(identifier)))
            )
        except ValueError:
            raise ServiceError("INVALID_CURSOR", "资产查询游标无效") from None
    rows = list(
        connection.execute(
            query.order_by(table.c.created_at, table.c.id).limit(request.limit + 1)
        ).mappings()
    )
    cursor = None
    if len(rows) > request.limit:
        last = rows[request.limit - 1]
        cursor = encode_cursor(scope, f"{last['created_at'].isoformat()}|{last['id']}")
    return rows[: request.limit], cursor


class AssetService:
    """复用应用数据库；事务中保存资产与结果，读取使用同一数据库快照。"""

    def __init__(self, database: Database) -> None:
        self.database = database

    def _write(
        self,
        request: TagCreate | AnnotationCreate,
        operation: str,
        action: Callable[[Connection], TagOut | AnnotationOut],
    ) -> AssetWriteOut:
        """唯一请求键先参与事务竞争，再执行业务，避免同键修改被误判为版本冲突。

        占键行只在当前事务内存在，提交前填入完整快照；任何异常均回滚占键与全部业务写入。
        PostgreSQL 的 ON CONFLICT 等待竞争事务结束；失败事务不消耗请求键。
        """
        fingerprint = sha256(
            compact(
                {
                    "contract": "asset-v1",
                    "operation": operation,
                    "input": request.model_dump(mode="json", exclude={"request_id"}),
                }
            ).encode()
        ).hexdigest()
        try:
            with self.database.engine.begin() as connection:
                claimed = connection.execute(
                    insert(requests)
                    .values(request_id=request.request_id, fingerprint=fingerprint, response={})
                    .on_conflict_do_nothing(index_elements=[requests.c.request_id])
                    .returning(requests.c.request_id)
                ).scalar_one_or_none()
                if claimed is None:
                    row = (
                        connection.execute(
                            select(requests).where(requests.c.request_id == request.request_id)
                        )
                        .mappings()
                        .one()
                    )
                    if row["fingerprint"] != fingerprint:
                        raise ServiceError("REQUEST_CONFLICT", "请求键已用于另一项资产写入", 409)
                    return AssetWriteOut.model_validate(row["response"]).model_copy(
                        update={"replayed": True}
                    )
                result = AssetWriteOut.model_validate(
                    {
                        "request_id": request.request_id,
                        "operation": operation,
                        "result": action(connection),
                    }
                )
                payload = result.model_dump(mode="json")
                if len(compact(payload).encode()) > MAX_ASSET_RESULT_BYTES:
                    raise ServiceError("ASSET_TOO_LARGE", "完整资产写入结果超过 1 MiB", 413)
                connection.execute(
                    requests.update()
                    .where(requests.c.request_id == request.request_id)
                    .values(response=payload)
                )
                return result
        except IntegrityError as exc:
            if getattr(getattr(exc.orig, "diag", None), "constraint_name", None) == "uq_tags_name":
                raise ServiceError(
                    "TAG_NAME_CONFLICT", "标签名称已存在，请查询后复用", 409
                ) from None
            raise

    def write_result(self, request_id: UUID) -> AssetWriteOut:
        with self.database.engine.connect() as connection:
            payload = connection.execute(
                select(requests.c.response).where(requests.c.request_id == request_id)
            ).scalar_one_or_none()
        if payload is None:
            raise ServiceError("WRITE_NOT_COMMITTED", "查询时尚无已提交资产写入", 404)
        return AssetWriteOut.model_validate(payload).model_copy(update={"replayed": True})

    def create_tag(self, request: TagCreate) -> AssetWriteOut:
        def action(connection: Connection) -> TagOut:
            row = (
                connection.execute(
                    tags.insert()
                    .values(id=uuid4(), **request.model_dump(exclude={"request_id"}))
                    .returning(tags)
                )
                .mappings()
                .one()
            )
            return tag_out(row)

        return self._write(request, "tag_create", action)

    def get_tag(self, tag_id: UUID) -> TagOut:
        with self.database.engine.connect() as connection:
            row = connection.execute(select(tags).where(tags.c.id == tag_id)).mappings().first()
            if row is None:
                raise ServiceError("TAG_NOT_FOUND", "标签不存在", 404)
            return tag_out(row)

    def list_tags(self, request: TagList) -> Page[TagOut]:
        query = select(tags)
        if request.namespace is not None:
            query = query.where(tags.c.namespace == request.namespace)
        if isinstance(request, TagSearch):
            # 先转义 SQL 模式字符，别名逐项匹配，不能在 JSON 序列化边界上产生假命中。
            pattern = (
                "%"
                + request.query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                + "%"
            )
            alias_values = (
                func.jsonb_array_elements_text(tags.c.aliases)
                .table_valued("value")
                .render_derived(name="alias_values")
            )
            alias_match = (
                select(literal(1))
                .select_from(alias_values)
                .where(alias_values.c.value.collate("default").ilike(pattern, escape="\\"))
                .correlate(tags)
                .exists()
            )
            full_name = (tags.c.namespace + "/" + tags.c.name).collate("default")
            query = query.where(
                or_(
                    full_name.ilike(pattern, escape="\\"),
                    tags.c.description.ilike(pattern, escape="\\"),
                    alias_match,
                )
            )
        with self.database.engine.connect() as connection:
            rows, cursor = page_rows(connection, tags, query, request)
            return Page(items=[tag_out(row) for row in rows], next_cursor=cursor)

    def _save_references(
        self,
        connection: Connection,
        annotation_id: UUID,
        request: AnnotationCreate,
    ) -> None:
        """与主记录同事务替换关联；范围至少一项，失败时旧关联随事务回滚恢复。"""
        connection.execute(ranges.delete().where(ranges.c.annotation_id == annotation_id))
        connection.execute(links.delete().where(links.c.annotation_id == annotation_id))
        connection.execute(
            ranges.insert(),
            [
                dict(value.model_dump(), annotation_id=annotation_id, ordinal=index)
                for index, value in enumerate(request.source_ranges, 1)
            ],
        )
        if request.tag_ids:
            connection.execute(
                links.insert(),
                [
                    {"annotation_id": annotation_id, "tag_id": identifier}
                    for identifier in request.tag_ids
                ],
            )

    def _validate_references(self, connection: Connection, request: AnnotationCreate) -> None:
        work_at(connection, request.work_id)
        for value in request.source_ranges:
            range_bounds(connection, request.work_id, value)
        require_tags(connection, request.tag_ids)

    def create_annotation(self, request: AnnotationCreate) -> AssetWriteOut:
        def action(connection: Connection) -> AnnotationOut:
            self._validate_references(connection, request)
            row = (
                connection.execute(
                    annotations.insert()
                    .values(id=uuid4(), work_id=request.work_id, note=request.note, version=1)
                    .returning(annotations)
                )
                .mappings()
                .one()
            )
            self._save_references(connection, row["id"], request)
            return annotation_out(connection, row)

        return self._write(request, "annotation_create", action)

    def update_annotation(self, request: AnnotationUpdate) -> AssetWriteOut:
        def action(connection: Connection) -> AnnotationOut:
            row = (
                connection.execute(
                    select(annotations)
                    .where(
                        annotations.c.id == request.annotation_id,
                        annotations.c.work_id == request.work_id,
                    )
                    .with_for_update()
                )
                .mappings()
                .first()
            )
            if row is None:
                raise ServiceError("ANNOTATION_NOT_FOUND", "指定作品中不存在该标注", 404)
            if row["version"] != request.expected_version:
                raise ServiceError(
                    "VERSION_CONFLICT",
                    "标注已被修改，请重新读取",
                    409,
                    {"current_version": row["version"]},
                )
            self._validate_references(connection, request)
            row = (
                connection.execute(
                    annotations.update()
                    .where(
                        annotations.c.id == request.annotation_id,
                        annotations.c.version == request.expected_version,
                    )
                    .values(
                        note=request.note,
                        version=request.expected_version + 1,
                        updated_at=func.clock_timestamp(),
                    )
                    .returning(annotations)
                )
                .mappings()
                .one()
            )
            self._save_references(connection, row["id"], request)
            return annotation_out(connection, row)

        return self._write(request, "annotation_update", action)

    def get_annotation(self, request: AnnotationGet) -> AnnotationOut:
        # 三组读取必须属于同一快照；READ COMMITTED 的逐语句快照会混入新版本关联。
        with (
            self.database.engine.connect().execution_options(
                isolation_level="REPEATABLE READ"
            ) as connection,
            connection.begin(),
        ):
            row = (
                connection.execute(
                    select(annotations).where(
                        annotations.c.id == request.annotation_id,
                        annotations.c.work_id == request.work_id,
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                raise ServiceError("ANNOTATION_NOT_FOUND", "指定作品中不存在该标注", 404)
            return annotation_out(connection, row)

    def list_annotations(self, request: AnnotationList) -> Page[AnnotationSummary]:
        """在数据库投影摘要，不把整页完整 Note 或正文加载后再截断。"""
        first_range = (
            select(
                func.jsonb_build_object(
                    "work_id",
                    ranges.c.work_id,
                    "section_id",
                    ranges.c.section_id,
                    "start_paragraph_id",
                    ranges.c.start_paragraph_id,
                    "end_paragraph_id",
                    ranges.c.end_paragraph_id,
                )
            )
            .where(ranges.c.annotation_id == annotations.c.id)
            .order_by(ranges.c.ordinal)
            .limit(1)
            .scalar_subquery()
        )
        range_count = (
            select(func.count()).where(ranges.c.annotation_id == annotations.c.id).scalar_subquery()
        )
        tag_count = (
            select(func.count()).where(links.c.annotation_id == annotations.c.id).scalar_subquery()
        )
        query = select(
            annotations.c.id,
            annotations.c.work_id,
            annotations.c.version,
            annotations.c.created_at,
            annotations.c.updated_at,
            first_range.label("first_source_range"),
            range_count.label("source_range_count"),
            tag_count.label("tag_count"),
            func.substr(annotations.c.note, 1, 200).label("note_preview"),
            func.coalesce(func.length(annotations.c.note) > 200, False).label("note_truncated"),
        ).where(annotations.c.work_id == request.work_id)
        with (
            self.database.engine.connect().execution_options(
                isolation_level="REPEATABLE READ"
            ) as connection,
            connection.begin(),
        ):
            work_at(connection, request.work_id)
            require_tags(connection, request.tag_ids)
            if request.tag_ids:
                query = query.where(
                    annotations.c.id.in_(
                        select(links.c.annotation_id)
                        .where(links.c.tag_id.in_(request.tag_ids))
                        .group_by(links.c.annotation_id)
                        .having(func.count() == len(request.tag_ids))
                    )
                )
            if request.source_range is not None:
                first, last = range_bounds(connection, request.work_id, request.source_range)
                start, end = paragraphs.alias("start"), paragraphs.alias("end")
                overlap = (
                    select(ranges.c.annotation_id)
                    .select_from(
                        ranges.join(start, ranges.c.start_paragraph_id == start.c.id).join(
                            end, ranges.c.end_paragraph_id == end.c.id
                        )
                    )
                    .where(
                        ranges.c.section_id == request.source_range.section_id,
                        start.c.ordinal <= last,
                        end.c.ordinal >= first,
                    )
                )
                query = query.where(annotations.c.id.in_(overlap))
            rows, cursor = page_rows(connection, annotations, query, request)
            return Page(
                items=[AnnotationSummary.model_validate(row) for row in rows], next_cursor=cursor
            )
