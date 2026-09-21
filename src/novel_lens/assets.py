"""分析资产的事务、定位与查询；不执行文学判断，不修改原文或分析进度。"""

import json
from collections.abc import Callable
from contextlib import nullcontext
from datetime import datetime
from hashlib import sha256
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel
from sqlalchemy import Connection, Select, Table, func, literal, or_, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import IntegrityError

from novel_lens.asset_contracts import (
    MAX_ASSET_RESULT_BYTES,
    AnalysisCheckpoint,
    AnalysisJobComplete,
    AnalysisJobCreate,
    AnalysisJobModify,
    AnnotationCreate,
    AnnotationGet,
    AnnotationList,
    AnnotationOut,
    AnnotationSummary,
    AnnotationUpdate,
    AssetWriteOut,
    EntityCreate,
    EntityGet,
    EntityList,
    EntityOut,
    EntitySearch,
    EntityUpdate,
    RelationCreate,
    RelationExpand,
    RelationGet,
    RelationNodeOut,
    RelationNodePage,
    RelationOut,
    RelationSearch,
    RelationSetStatus,
    RelationSnapshot,
    RelationSummary,
    RelationUpdate,
    StyleGuideCreate,
    StyleGuideEntry,
    StyleGuideGet,
    StyleGuideOut,
    StyleGuideUpdate,
    TagCreate,
    TagList,
    TagOut,
    TagSearch,
)
from novel_lens.contracts import ListRequest, Page, SourceRange
from novel_lens.cursors import decode_cursor, encode_cursor, ordinal_cursor
from novel_lens.database import Database
from novel_lens.errors import ServiceError
from novel_lens.reading import paragraph_at, section_at, work_at
from novel_lens.schema import (
    annotation_entities,
    annotations,
    entities,
    paragraphs,
    relation_entities,
    relation_nodes,
    relation_tags,
    relations,
    style_guide_entries,
    style_guide_ranges,
    style_guides,
    tags,
)
from novel_lens.schema import (
    annotation_ranges as ranges,
)
from novel_lens.schema import (
    annotation_tags as links,
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


def require_entities(connection: Connection, work_id: UUID, ids: list[UUID]) -> None:
    """实体不可跨作品；名称相同不影响按稳定 ID 校验归属。"""
    if ids and len(
        connection.execute(
            select(entities.c.id).where(entities.c.work_id == work_id, entities.c.id.in_(ids))
        ).all()
    ) != len(ids):
        raise ServiceError("ENTITY_NOT_FOUND", "指定作品中不存在引用的实体", 404)


def related_ids(
    connection: Connection, table: Table, owner: str, key: str, identifier: UUID
) -> list[UUID]:
    """在调用方事务内读取有序的关联集合。"""
    return list(
        connection.execute(
            select(table.c[key]).where(table.c[owner] == identifier).order_by(table.c[key])
        ).scalars()
    )


def scoped_row(
    connection: Connection,
    table: Table,
    work_id: UUID,
    identifier: UUID,
    code: str,
    *,
    lock: bool = False,
) -> RowMapping:
    """读取同作品对象；写操作先锁主记录，使内容和关联修改共享版本竞争。"""
    query = select(table).where(table.c.work_id == work_id, table.c.id == identifier)
    if lock:
        # 业务更新不改主键；NO KEY UPDATE 与外键检查的 KEY SHARE 兼容，
        # 避免跨批次引用实体后再修订另一实体时形成外键锁环。
        query = query.with_for_update(key_share=True)
    row = connection.execute(query).mappings().first()
    if row is None:
        raise ServiceError(code, "指定作品中不存在该对象", 404)
    return row


def check_version(row: RowMapping, expected: int) -> None:
    if row["version"] != expected:
        raise ServiceError(
            "VERSION_CONFLICT", "对象已被修改，请重新读取", 409, {"current_version": row["version"]}
        )


def literal_pattern(query: str) -> str:
    """只把用户输入作为字面子串，转义 SQL LIKE 模式字符。"""
    return "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def node_out(row: RowMapping) -> RelationNodeOut:
    return RelationNodeOut(
        ordinal=row["ordinal"],
        role=row["role"],
        source_range=SourceRange(**{key: row[key] for key in SourceRange.model_fields}),
    )


def relation_out(connection: Connection, row: RowMapping) -> RelationOut:
    """调用方须持有关系写锁或同一读取快照；不加载节点正文或节点列表。"""
    return RelationOut.model_validate(
        dict(row)
        | {
            "tag_ids": related_ids(connection, relation_tags, "relation_id", "tag_id", row["id"]),
            "entity_ids": related_ids(
                connection, relation_entities, "relation_id", "entity_id", row["id"]
            ),
            "node_count": connection.execute(
                select(func.count()).where(relation_nodes.c.relation_id == row["id"])
            ).scalar_one(),
        }
    )


def relation_snapshot(connection: Connection, row: RowMapping) -> RelationSnapshot:
    """写事务中保存全部节点，后续重放无需查询可能已变更的当前关系。"""
    nodes = connection.execute(
        select(relation_nodes)
        .where(relation_nodes.c.relation_id == row["id"])
        .order_by(relation_nodes.c.ordinal)
    ).mappings()
    return RelationSnapshot(
        **relation_out(connection, row).model_dump(), nodes=[node_out(node) for node in nodes]
    )


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
        dict(row)
        | {
            "source_ranges": [dict(r) for r in references],
            "tag_ids": ids,
            "entity_ids": related_ids(
                connection, annotation_entities, "annotation_id", "entity_id", row["id"]
            ),
        }
    )


def style_guide_out(connection: Connection, row: RowMapping) -> StyleGuideOut:
    """在写锁或一致读取快照内组装导航；批量读引用，不加载原文。"""
    references: dict[int, list[SourceRange]] = {}
    for ref in connection.execute(
        select(style_guide_ranges)
        .where(style_guide_ranges.c.work_id == row["work_id"])
        .order_by(style_guide_ranges.c.entry_ordinal, style_guide_ranges.c.ordinal)
    ).mappings():
        references.setdefault(ref["entry_ordinal"], []).append(
            SourceRange(**{key: ref[key] for key in SourceRange.model_fields})
        )
    entries = [
        StyleGuideEntry(
            title=entry["title"],
            kind=entry["kind"],
            description=entry["description"],
            applicability=entry["applicability"],
            source_ranges=references.get(entry["ordinal"], []),
        )
        for entry in connection.execute(
            select(style_guide_entries)
            .where(style_guide_entries.c.work_id == row["work_id"])
            .order_by(style_guide_entries.c.ordinal)
        ).mappings()
    ]
    return StyleGuideOut(**dict(row), entries=entries)


def replace_style_guide_entries(connection: Connection, request: StyleGuideCreate) -> None:
    """调用方持有主记录写锁；整体替换条目及证据，失败由外层事务回滚。"""
    for entry in request.entries:
        for ref in entry.source_ranges:
            range_bounds(connection, request.work_id, ref)
    connection.execute(
        style_guide_ranges.delete().where(style_guide_ranges.c.work_id == request.work_id)
    )
    connection.execute(
        style_guide_entries.delete().where(style_guide_entries.c.work_id == request.work_id)
    )
    if not request.entries:
        return
    connection.execute(
        style_guide_entries.insert(),
        [
            dict(
                work_id=request.work_id,
                ordinal=index,
                **entry.model_dump(exclude={"source_ranges"}),
            )
            for index, entry in enumerate(request.entries, 1)
        ],
    )
    connection.execute(
        style_guide_ranges.insert(),
        [
            dict(entry_ordinal=index, ordinal=ordinal, **ref.model_dump())
            for index, entry in enumerate(request.entries, 1)
            for ordinal, ref in enumerate(entry.source_ranges, 1)
        ],
    )


def page_rows(
    connection: Connection,
    table: Table,
    query: Select[Any],
    request: ListRequest,
) -> tuple[list[RowMapping], str | None]:
    """创建顺序游标绑定查询类型和全部过滤条件，分页大小不属于过滤条件。"""
    filters = request.model_dump(mode="json", exclude={"limit", "cursor"})
    # 空实体过滤保留 0002 游标摘要，非空过滤必须参与范围绑定。
    if isinstance(request, AnnotationList) and not request.entity_ids:
        filters.pop("entity_ids")
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


type WriteRequest = (
    TagCreate
    | AnnotationCreate
    | EntityCreate
    | RelationCreate
    | RelationSetStatus
    | StyleGuideCreate
    | AnalysisJobCreate
    | AnalysisJobModify
)


def lock_write_batch(connection: Connection, request: WriteRequest) -> None:
    """统一先锁请求键、再锁资源，批次预先排序以避免反向写入产生锁环。

    两个 advisory namespace 分别用于请求键和资源；哈希碰撞只增加串行等待。
    子操作重复取得事务已持有的锁，不创建新事务，也不释放外层锁。
    """
    values = [request]
    if isinstance(request, AnalysisCheckpoint):
        values.extend(item.input for item in request.writes)
    keys = [str(value.request_id) for value in values]
    resources: list[str] = []
    for value in values:
        if isinstance(value, TagCreate):
            resources.append(f"tag:{value.namespace}:{value.name}")
        for field in ("annotation_id", "entity_id", "relation_id", "job_id"):
            identifier = getattr(value, field, None)
            if identifier is not None:
                resources.append(f"{field}:{identifier}")
        if isinstance(value, (StyleGuideCreate, AnalysisJobComplete)):
            resources.append(f"style:{value.work_id}")
    for namespace, names in ((71001, keys), (71002, resources)):
        hashes = {int.from_bytes(sha256(name.encode()).digest()[:4], signed=True) for name in names}
        for key in sorted(hashes):
            connection.execute(select(func.pg_advisory_xact_lock(namespace, key)))


class AssetService:
    """复用应用数据库；事务中保存资产与结果，读取使用同一数据库快照。

    可绑定 checkpoint 的连接，仅用于子写入，由外层负责提交和回滚；实例不共享临时连接。
    """

    def __init__(self, database: Database, connection: Connection | None = None) -> None:
        self.database = database
        self.connection = connection

    def _write(
        self,
        request: TagCreate
        | AnnotationCreate
        | EntityCreate
        | RelationCreate
        | RelationSetStatus
        | StyleGuideCreate
        | AnalysisJobCreate
        | AnalysisJobModify,
        operation: str,
        action: Callable[[Connection], BaseModel],
    ) -> AssetWriteOut:
        """唯一请求键先参与事务竞争，再执行业务，避免同键修改被误判为版本冲突。

        占键行只在当前事务内存在，提交前填入完整快照；任何异常均回滚占键与全部业务写入。
        PostgreSQL 的 ON CONFLICT 等待竞争事务结束；失败事务不消耗请求键。
        """
        inputs = request.model_dump(mode="json", exclude={"request_id"})
        if isinstance(request, AnalysisCheckpoint):
            # 子操作省略 entity_ids 的保留语义须参与批次指纹，不能与显式清空等同。
            for item, payload in zip(request.writes, inputs["writes"], strict=True):
                if (
                    isinstance(item.input, AnnotationUpdate)
                    and "entity_ids" not in item.input.model_fields_set
                ):
                    payload["input"].pop("entity_ids")
        contract = "asset-v2"
        if isinstance(request, TagCreate):
            contract = "asset-v1"
        elif isinstance(request, AnnotationCreate):
            legacy = (
                "entity_ids" not in request.model_fields_set
                if isinstance(request, AnnotationUpdate)
                else not request.entity_ids
            )
            if legacy:
                inputs.pop("entity_ids")
                contract = "asset-v1"
        fingerprint = sha256(
            compact(
                {
                    "contract": contract,
                    "operation": operation,
                    "input": inputs,
                }
            ).encode()
        ).hexdigest()
        try:
            with (
                nullcontext(self.connection)
                if self.connection is not None
                else self.database.engine.begin()
            ) as connection:
                lock_write_batch(connection, request)
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

    def create_style_guide(self, request: StyleGuideCreate) -> AssetWriteOut:
        """作品主键防止异键并发创建多份导航；同键重试由共享写入流程恢复。"""

        def action(connection: Connection) -> StyleGuideOut:
            work_at(connection, request.work_id)
            row = (
                connection.execute(
                    insert(style_guides)
                    .values(work_id=request.work_id, scope_note=request.scope_note, version=1)
                    .on_conflict_do_nothing(index_elements=[style_guides.c.work_id])
                    .returning(style_guides)
                )
                .mappings()
                .first()
            )
            if row is None:
                raise ServiceError("STYLE_GUIDE_EXISTS", "该作品已有风格导航，请读取后修订", 409)
            replace_style_guide_entries(connection, request)
            return style_guide_out(connection, row)

        return self._write(request, "style_guide_create", action)

    def get_style_guide(self, request: StyleGuideGet) -> StyleGuideOut:
        """跨表读取同一快照；范围说明和条目始终属于同一导航版本。"""
        with self.database.engine.connect().execution_options(
            isolation_level="REPEATABLE READ"
        ) as connection:
            work_at(connection, request.work_id)
            row = (
                connection.execute(
                    select(style_guides).where(style_guides.c.work_id == request.work_id)
                )
                .mappings()
                .first()
            )
            if row is None:
                raise ServiceError("STYLE_GUIDE_NOT_FOUND", "该作品尚无风格导航", 404)
            return style_guide_out(connection, row)

    def update_style_guide(self, request: StyleGuideUpdate) -> AssetWriteOut:
        """主记录锁覆盖版本检查、条目替换和快照；不自动合并文学结论。"""

        def action(connection: Connection) -> StyleGuideOut:
            work_at(connection, request.work_id)
            old = (
                connection.execute(
                    select(style_guides)
                    .where(style_guides.c.work_id == request.work_id)
                    .with_for_update()
                )
                .mappings()
                .first()
            )
            if old is None:
                raise ServiceError("STYLE_GUIDE_NOT_FOUND", "该作品尚无风格导航", 404)
            check_version(old, request.expected_version)
            replace_style_guide_entries(connection, request)
            row = (
                connection.execute(
                    style_guides.update()
                    .where(style_guides.c.work_id == request.work_id)
                    .values(
                        scope_note=request.scope_note,
                        version=old["version"] + 1,
                        updated_at=func.clock_timestamp(),
                    )
                    .returning(style_guides)
                )
                .mappings()
                .one()
            )
            return style_guide_out(connection, row)

        return self._write(request, "style_guide_update", action)

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

        # 旧修改不认识 entity_ids；省略时必须保留当前关联，显式 [] 才清空。
        if not isinstance(request, AnnotationUpdate) or "entity_ids" in request.model_fields_set:
            connection.execute(
                annotation_entities.delete().where(
                    annotation_entities.c.annotation_id == annotation_id
                )
            )
            if request.entity_ids:
                connection.execute(
                    annotation_entities.insert(),
                    [
                        {"annotation_id": annotation_id, "entity_id": identifier}
                        for identifier in request.entity_ids
                    ],
                )

    def _validate_references(self, connection: Connection, request: AnnotationCreate) -> None:
        work_at(connection, request.work_id)
        for value in request.source_ranges:
            range_bounds(connection, request.work_id, value)
        require_tags(connection, request.tag_ids)
        require_entities(connection, request.work_id, request.entity_ids)

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
        # 主记录及全部关联须属于同一快照，避免逐语句快照混入新版本关联。
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
        entity_count = (
            select(func.count())
            .where(annotation_entities.c.annotation_id == annotations.c.id)
            .scalar_subquery()
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
            entity_count.label("entity_count"),
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
            require_entities(connection, request.work_id, request.entity_ids)
            if request.entity_ids:
                query = query.where(
                    annotations.c.id.in_(
                        select(annotation_entities.c.annotation_id)
                        .where(annotation_entities.c.entity_id.in_(request.entity_ids))
                        .group_by(annotation_entities.c.annotation_id)
                        .having(func.count() == len(request.entity_ids))
                    )
                )
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

    def create_entity(self, request: EntityCreate) -> AssetWriteOut:
        """创建独立身份；同名允许并存，重复请求由资产请求键处理。"""

        def action(connection: Connection) -> EntityOut:
            work_at(connection, request.work_id)
            row = (
                connection.execute(
                    entities.insert()
                    .values(id=uuid4(), version=1, **request.model_dump(exclude={"request_id"}))
                    .returning(entities)
                )
                .mappings()
                .one()
            )
            return EntityOut.model_validate(row)

        return self._write(request, "entity_create", action)

    def update_entity(self, request: EntityUpdate) -> AssetWriteOut:
        """修改身份描述而不改动关联资产版本；身份 ID 和作品归属保持不变。"""

        def action(connection: Connection) -> EntityOut:
            old = scoped_row(
                connection,
                entities,
                request.work_id,
                request.entity_id,
                "ENTITY_NOT_FOUND",
                lock=True,
            )
            check_version(old, request.expected_version)
            row = (
                connection.execute(
                    entities.update()
                    .where(entities.c.id == request.entity_id)
                    .values(
                        type=request.type,
                        canonical_name=request.canonical_name,
                        aliases=request.aliases,
                        note=request.note,
                        version=old["version"] + 1,
                        updated_at=func.clock_timestamp(),
                    )
                    .returning(entities)
                )
                .mappings()
                .one()
            )
            return EntityOut.model_validate(row)

        return self._write(request, "entity_update", action)

    def get_entity(self, request: EntityGet) -> EntityOut:
        with self.database.engine.connect() as connection:
            return EntityOut.model_validate(
                scoped_row(
                    connection, entities, request.work_id, request.entity_id, "ENTITY_NOT_FOUND"
                )
            )

    def list_entities(self, request: EntityList) -> Page[EntityOut]:
        """按作品、类型及可选字面查询列出全部身份候选，不按名称自动消歧。"""
        query = select(entities).where(entities.c.work_id == request.work_id)
        if request.type is not None:
            query = query.where(entities.c.type == request.type)
        if isinstance(request, EntitySearch):
            pattern = literal_pattern(request.query)
            aliases = (
                func.jsonb_array_elements_text(entities.c.aliases)
                .table_valued("value")
                .render_derived(name="entity_aliases")
            )
            alias_match = (
                select(literal(1))
                .select_from(aliases)
                .where(aliases.c.value.ilike(pattern, escape="\\"))
                .correlate(entities)
                .exists()
            )
            query = query.where(
                or_(
                    entities.c.canonical_name.ilike(pattern, escape="\\"),
                    entities.c.note.ilike(pattern, escape="\\"),
                    alias_match,
                )
            )
        with (
            self.database.engine.connect().execution_options(
                isolation_level="REPEATABLE READ"
            ) as connection,
            connection.begin(),
        ):
            work_at(connection, request.work_id)
            rows, cursor = page_rows(connection, entities, query, request)
            return Page(items=[EntityOut.model_validate(row) for row in rows], next_cursor=cursor)

    def _validate_relation(self, connection: Connection, request: RelationCreate) -> None:
        """事务内校验不可变原文归属和实体身份；不判断文学关系是否成立。"""
        work_at(connection, request.work_id)
        for node in request.nodes:
            range_bounds(connection, request.work_id, node.source_range)
        require_tags(connection, request.tag_ids)
        require_entities(connection, request.work_id, request.entity_ids)

    def _save_relation_references(
        self, connection: Connection, identifier: UUID, request: RelationCreate
    ) -> None:
        """主记录持锁期间原子替换有序节点和集合；任一写入失败整体回滚。"""
        for table in (relation_nodes, relation_tags, relation_entities):
            connection.execute(table.delete().where(table.c.relation_id == identifier))
        connection.execute(
            relation_nodes.insert(),
            [
                dict(
                    node.source_range.model_dump(),
                    relation_id=identifier,
                    ordinal=index,
                    role=node.role,
                )
                for index, node in enumerate(request.nodes, 1)
            ],
        )
        if request.tag_ids:
            connection.execute(
                relation_tags.insert(),
                [{"relation_id": identifier, "tag_id": key} for key in request.tag_ids],
            )
        if request.entity_ids:
            connection.execute(
                relation_entities.insert(),
                [{"relation_id": identifier, "entity_id": key} for key in request.entity_ids],
            )

    def create_relation(self, request: RelationCreate) -> AssetWriteOut:
        """直接连接原文；创建时有效，是否具有文学依据由调用方负责。"""

        def action(connection: Connection) -> RelationSnapshot:
            self._validate_relation(connection, request)
            row = (
                connection.execute(
                    relations.insert()
                    .values(
                        id=uuid4(),
                        work_id=request.work_id,
                        title=request.title,
                        relation_type=request.relation_type,
                        note=request.note,
                        status="active",
                        version=1,
                    )
                    .returning(relations)
                )
                .mappings()
                .one()
            )
            self._save_relation_references(connection, row["id"], request)
            return relation_snapshot(connection, row)

        return self._write(request, "relation_create", action)

    def update_relation(self, request: RelationUpdate) -> AssetWriteOut:
        """完整替换内容及关联，保留现有撤回状态；与状态写入竞争同一版本。"""

        def action(connection: Connection) -> RelationSnapshot:
            old = scoped_row(
                connection,
                relations,
                request.work_id,
                request.relation_id,
                "RELATION_NOT_FOUND",
                lock=True,
            )
            check_version(old, request.expected_version)
            self._validate_relation(connection, request)
            row = (
                connection.execute(
                    relations.update()
                    .where(relations.c.id == request.relation_id)
                    .values(
                        title=request.title,
                        relation_type=request.relation_type,
                        note=request.note,
                        version=old["version"] + 1,
                        updated_at=func.clock_timestamp(),
                    )
                    .returning(relations)
                )
                .mappings()
                .one()
            )
            self._save_relation_references(connection, row["id"], request)
            return relation_snapshot(connection, row)

        return self._write(request, "relation_update", action)

    def set_relation_status(self, request: RelationSetStatus) -> AssetWriteOut:
        """撤回或恢复保留全部证据；新请求增加版本，重放返回原始状态快照。"""

        def action(connection: Connection) -> RelationSnapshot:
            old = scoped_row(
                connection,
                relations,
                request.work_id,
                request.relation_id,
                "RELATION_NOT_FOUND",
                lock=True,
            )
            check_version(old, request.expected_version)
            row = (
                connection.execute(
                    relations.update()
                    .where(relations.c.id == request.relation_id)
                    .values(
                        status=request.status,
                        version=old["version"] + 1,
                        updated_at=func.clock_timestamp(),
                    )
                    .returning(relations)
                )
                .mappings()
                .one()
            )
            return relation_snapshot(connection, row)

        return self._write(request, "relation_set_status", action)

    def get_relation(self, request: RelationGet) -> RelationOut:
        """按 ID 可读取已撤回对象；同一快照返回说明、关联及节点计数。"""
        with (
            self.database.engine.connect().execution_options(
                isolation_level="REPEATABLE READ"
            ) as connection,
            connection.begin(),
        ):
            row = scoped_row(
                connection, relations, request.work_id, request.relation_id, "RELATION_NOT_FOUND"
            )
            return relation_out(connection, row)

    def expand_relation(self, request: RelationExpand) -> RelationNodePage:
        """节点概览绑定当前版本；发生修改时拒绝继续拼接跨版本节点。"""
        scope = f"RelationExpand:{request.work_id}:{request.relation_id}:{request.expected_version}"
        # 先拒绝串用游标，再检查当前版本；旧游标配原版本应返回 VERSION_CONFLICT。
        decode_cursor(request.cursor, scope)
        with (
            self.database.engine.connect().execution_options(
                isolation_level="REPEATABLE READ"
            ) as connection,
            connection.begin(),
        ):
            row = scoped_row(
                connection, relations, request.work_id, request.relation_id, "RELATION_NOT_FOUND"
            )
            check_version(row, request.expected_version)
            count = connection.execute(
                select(func.count()).where(relation_nodes.c.relation_id == request.relation_id)
            ).scalar_one()
            after = ordinal_cursor(request.cursor, scope, count)
            nodes = list(
                connection.execute(
                    select(relation_nodes)
                    .where(
                        relation_nodes.c.relation_id == request.relation_id,
                        relation_nodes.c.ordinal > after,
                    )
                    .order_by(relation_nodes.c.ordinal)
                    .limit(request.limit + 1)
                ).mappings()
            )
            cursor = (
                encode_cursor(scope, str(nodes[request.limit - 1]["ordinal"]))
                if len(nodes) > request.limit
                else None
            )
            return RelationNodePage(
                work_id=request.work_id,
                relation_id=request.relation_id,
                version=row["version"],
                status=row["status"],
                items=[node_out(n) for n in nodes[: request.limit]],
                next_cursor=cursor,
            )

    def search_relations(self, request: RelationSearch) -> Page[RelationSummary]:
        """数据库中筛选并投影摘要；每条关系只出现一次，默认排除已撤回对象。"""
        nodes = relation_nodes
        first_range = (
            select(
                func.jsonb_build_object(
                    "work_id",
                    nodes.c.work_id,
                    "section_id",
                    nodes.c.section_id,
                    "start_paragraph_id",
                    nodes.c.start_paragraph_id,
                    "end_paragraph_id",
                    nodes.c.end_paragraph_id,
                )
            )
            .where(nodes.c.relation_id == relations.c.id)
            .order_by(nodes.c.ordinal)
            .limit(1)
            .scalar_subquery()
        )
        counts = [
            select(func.count())
            .where(table.c.relation_id == relations.c.id)
            .scalar_subquery()
            .label(label)
            for table, label in (
                (nodes, "node_count"),
                (relation_tags, "tag_count"),
                (relation_entities, "entity_count"),
            )
        ]
        query = select(
            relations.c.id,
            relations.c.work_id,
            relations.c.title,
            relations.c.relation_type,
            relations.c.status,
            relations.c.version,
            relations.c.created_at,
            relations.c.updated_at,
            first_range.label("first_source_range"),
            *counts,
            func.substr(relations.c.note, 1, 200).label("note_preview"),
            (func.length(relations.c.note) > 200).label("note_truncated"),
        ).where(relations.c.work_id == request.work_id)
        if request.status is not None:
            query = query.where(relations.c.status == request.status)
        if request.relation_type is not None:
            query = query.where(relations.c.relation_type == request.relation_type)
        if request.query is not None:
            pattern = literal_pattern(request.query)
            query = query.where(
                or_(
                    relations.c.title.ilike(pattern, escape="\\"),
                    relations.c.note.ilike(pattern, escape="\\"),
                )
            )
        with (
            self.database.engine.connect().execution_options(
                isolation_level="REPEATABLE READ"
            ) as connection,
            connection.begin(),
        ):
            work_at(connection, request.work_id)
            require_tags(connection, request.tag_ids)
            require_entities(connection, request.work_id, request.entity_ids)
            for table, field, ids in (
                (relation_tags, "tag_id", request.tag_ids),
                (relation_entities, "entity_id", request.entity_ids),
            ):
                if ids:
                    query = query.where(
                        relations.c.id.in_(
                            select(table.c.relation_id)
                            .where(table.c[field].in_(ids))
                            .group_by(table.c.relation_id)
                            .having(func.count() == len(ids))
                        )
                    )
            if request.source_range is not None:
                first, last = range_bounds(connection, request.work_id, request.source_range)
                start, end = paragraphs.alias("start"), paragraphs.alias("end")
                overlap = (
                    select(nodes.c.relation_id)
                    .select_from(
                        nodes.join(start, nodes.c.start_paragraph_id == start.c.id).join(
                            end, nodes.c.end_paragraph_id == end.c.id
                        )
                    )
                    .where(
                        nodes.c.section_id == request.source_range.section_id,
                        start.c.ordinal <= last,
                        end.c.ordinal >= first,
                    )
                )
                query = query.where(relations.c.id.in_(overlap))
            rows, cursor = page_rows(connection, relations, query, request)
            return Page(
                items=[RelationSummary.model_validate(row) for row in rows], next_cursor=cursor
            )
