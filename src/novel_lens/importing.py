"""追加分部原文；请求幂等和作品内分部名称冲突由 PostgreSQL 事务裁决。"""

from hashlib import sha256
from uuid import UUID, uuid4

from sqlalchemy import Connection, select, text

from novel_lens.catalog import CatalogService, lock_work
from novel_lens.contracts import ImportOut, Issue, PartOut, ValidationReport, WorkOut
from novel_lens.database import Database
from novel_lens.errors import ServiceError
from novel_lens.reading import work_at
from novel_lens.schema import catalog_requests, paragraphs, parts, sections, sources, works
from novel_lens.semantic import lock_key
from novel_lens.source import RULE_VERSION, SourceFailure, parse_canonical, report


class ImportService:
    """同一事务追加分部及完整文件；不自动转换旧数据或修改已有原文。"""

    def __init__(self, database: Database, max_file_bytes: int) -> None:
        self.database = database
        self.max_file_bytes = max_file_bytes
        self.catalog = CatalogService(database)

    def _check_size(self, data: bytes) -> None:
        if len(data) > self.max_file_bytes:
            raise ServiceError("FILE_TOO_LARGE", "文件超过允许大小", 413)

    def validate(self, data: bytes, work_id: UUID) -> ValidationReport:
        self._check_size(data)
        try:
            parsed = parse_canonical(data)
        except SourceFailure as exc:
            return report(
                data, Issue(code=exc.code, message=exc.message, source_position=exc.position)
            )
        with self.database.engine.connect() as connection:
            work_at(connection, work_id)
            exists = connection.execute(
                select(parts.c.id).where(
                    parts.c.work_id == work_id, parts.c.name == parsed.name.text
                )
            ).first()
        if exists:
            return report(
                data,
                Issue(
                    code="PART_NAME_CONFLICT",
                    message="作品内分部名称已存在",
                    source_position=parsed.name.position,
                ),
            )
        return report(data)

    def result(self, request_id: UUID) -> ImportOut:
        """返回提交时的分部导入快照；目录改名不重写回执。"""
        with self.database.engine.connect() as connection:
            row = connection.execute(
                select(catalog_requests.c.response).where(
                    catalog_requests.c.request_id == request_id,
                    catalog_requests.c.operation == "part_import",
                )
            ).scalar_one_or_none()
        if row is None:
            raise ServiceError(
                "IMPORT_NOT_COMMITTED", "尚无已提交的导入结果", 404, {"status": "not_committed"}
            )
        return ImportOut.model_validate({**row, "replayed": True})

    def import_source(self, data: bytes, request_id: UUID, work_id: UUID) -> ImportOut:
        """原子追加完整分部；同作品串行分配顺序，失败不留下正文或成功回执。"""
        self._check_size(data)
        digest = sha256(data).hexdigest()

        def action(connection: Connection) -> tuple[UUID, dict[str, object]]:
            # 同作品的导入先排队，再探测模型锁；普通并发追加不误报模型忙碌。
            connection.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:scope, 0))"),
                {"scope": f"part-import:{work_id}"},
            )
            # 与索引会话锁和整作品删除采用相同顺序；不等待跨事务的模型计算。
            for kind in ("fulltext", "annotation"):
                if not connection.execute(
                    text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": lock_key(work_id, kind)}
                ).scalar_one():
                    raise ServiceError("WORK_BUSY", "作品索引正在创建或构建，请稍后导入", 409)
            work = lock_work(connection, work_id)
            try:
                parsed = parse_canonical(data)
            except SourceFailure as exc:
                invalid = report(
                    data, Issue(code=exc.code, message=exc.message, source_position=exc.position)
                )
                raise ServiceError(
                    "IMPORT_VALIDATION_ERROR",
                    "上传文件不符合分部导入格式",
                    422,
                    invalid.model_dump(mode="json"),
                ) from None
            part_id = uuid4()
            section_count = len(parsed.sections)
            paragraph_count = sum(len(s.paragraphs) for s in parsed.sections)
            character_count = sum(len(p.text) for s in parsed.sections for p in s.paragraphs)
            row = (
                connection.execute(
                    parts.insert()
                    .values(
                        id=part_id,
                        work_id=work_id,
                        name=parsed.name.text,
                        ordinal=work.part_count + 1,
                        version=1,
                        section_count=section_count,
                        paragraph_count=paragraph_count,
                        character_count=character_count,
                        source_sha256=digest,
                        source_bytes=len(data),
                    )
                    .returning(parts)
                )
                .mappings()
                .one()
            )
            part = PartOut.model_validate(row)
            connection.execute(
                sources.insert().values(
                    part_id=part_id,
                    content=data,
                    rule_version=RULE_VERSION,
                    layout={
                        "name": parsed.name.position.model_dump(),
                        "sections": [s.heading.position.model_dump() for s in parsed.sections],
                    },
                )
            )
            for ordinal, section in enumerate(parsed.sections, work.section_count + 1):
                section_id = uuid4()
                connection.execute(
                    sections.insert().values(
                        id=section_id,
                        work_id=work_id,
                        part_id=part_id,
                        ordinal=ordinal,
                        title=section.heading.text,
                        paragraph_count=len(section.paragraphs),
                    )
                )
                for start in range(0, len(section.paragraphs), 1000):
                    connection.execute(
                        paragraphs.insert(),
                        [
                            {
                                "id": uuid4(),
                                "section_id": section_id,
                                "ordinal": number,
                                "text": paragraph.text,
                                "source_position": paragraph.position.model_dump(),
                            }
                            for number, paragraph in enumerate(
                                section.paragraphs[start : start + 1000], start + 1
                            )
                        ],
                    )
            manifest = "".join(
                connection.execute(
                    select(parts.c.source_sha256)
                    .where(parts.c.work_id == work_id)
                    .order_by(parts.c.ordinal)
                ).scalars()
            )
            updated = (
                connection.execute(
                    works.update()
                    .where(works.c.id == work_id)
                    .values(
                        version=work.version + 1,
                        part_count=work.part_count + 1,
                        section_count=work.section_count + section_count,
                        paragraph_count=work.paragraph_count + paragraph_count,
                        character_count=work.character_count + character_count,
                        source_bytes=work.source_bytes + len(data),
                        source_sha256=sha256(manifest.encode("ascii")).hexdigest(),
                    )
                    .returning(works)
                )
                .mappings()
                .one()
            )
            result = ImportOut(
                request_id=request_id,
                replayed=False,
                work=WorkOut.model_validate(updated),
                part=part,
            )
            return work_id, result.model_dump(mode="json")

        return ImportOut.model_validate(
            self.catalog.write(
                request_id, "part_import", {"work_id": str(work_id), "sha256": digest}, action
            )
        )
