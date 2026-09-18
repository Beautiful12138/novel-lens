"""导入原文；请求幂等和作品名称冲突由 PostgreSQL 事务裁决。"""

from hashlib import sha256
from uuid import UUID, uuid4

from sqlalchemy import Connection, select
from sqlalchemy.exc import IntegrityError

from novel_lens.contracts import ImportOut, Issue, ValidationReport, WorkOut
from novel_lens.database import Database
from novel_lens.errors import ServiceError
from novel_lens.schema import paragraphs, sections, sources, works
from novel_lens.source import RULE_VERSION, SourceFailure, parse_canonical, report


class ImportService:
    """同一事务保存文件及全部结构；不自动迁移，也不修改已有作品。"""

    def __init__(self, database: Database, max_file_bytes: int) -> None:
        self.database = database
        self.max_file_bytes = max_file_bytes

    def _check_size(self, data: bytes) -> None:
        if len(data) > self.max_file_bytes:
            raise ServiceError("FILE_TOO_LARGE", "文件超过允许大小", 413)

    def validate(self, data: bytes) -> ValidationReport:
        self._check_size(data)
        try:
            parsed = parse_canonical(data)
        except SourceFailure as exc:
            return report(
                data, Issue(code=exc.code, message=exc.message, source_position=exc.position)
            )
        with self.database.engine.connect() as connection:
            exists = connection.execute(
                select(works.c.id).where(works.c.name == parsed.name.text)
            ).first()
        if exists:
            return report(
                data,
                Issue(
                    code="WORK_NAME_CONFLICT",
                    message="作品名称已存在",
                    source_position=parsed.name.position,
                ),
            )
        return report(data)

    def _existing(
        self, connection: Connection, request_id: UUID, fingerprint: str | None = None
    ) -> ImportOut | None:
        row = (
            connection.execute(select(works).where(works.c.request_id == request_id))
            .mappings()
            .first()
        )
        if row is None:
            return None
        if fingerprint is not None and row["fingerprint"] != fingerprint:
            raise ServiceError("REQUEST_CONFLICT", "请求键已用于另一份输入", 409)
        return ImportOut(request_id=request_id, replayed=True, work=WorkOut.model_validate(row))

    def result(self, request_id: UUID) -> ImportOut:
        with self.database.engine.connect() as connection:
            result = self._existing(connection, request_id)
        if result is None:
            raise ServiceError(
                "IMPORT_NOT_COMMITTED", "尚无已提交的导入结果", 404, {"status": "not_committed"}
            )
        return result

    def import_source(self, data: bytes, request_id: UUID) -> ImportOut:
        """提交后响应；响应丢失时，调用方使用原请求键查询或重试。"""
        self._check_size(data)
        digest = sha256(data).hexdigest()
        fingerprint = sha256(f"import-v1:{digest}".encode("ascii")).hexdigest()
        with self.database.engine.connect() as connection:
            previous = self._existing(connection, request_id, fingerprint)
        if previous is not None:
            return previous
        try:
            parsed = parse_canonical(data)
        except SourceFailure as exc:
            invalid = report(
                data, Issue(code=exc.code, message=exc.message, source_position=exc.position)
            )
            raise ServiceError(
                "IMPORT_VALIDATION_ERROR",
                "上传文件不符合导入格式",
                422,
                invalid.model_dump(mode="json"),
            ) from None
        work_id = uuid4()
        try:
            with self.database.engine.begin() as connection:
                row = (
                    connection.execute(
                        works.insert()
                        .values(
                            id=work_id,
                            name=parsed.name.text,
                            request_id=request_id,
                            fingerprint=fingerprint,
                            source_sha256=digest,
                            source_bytes=len(data),
                            section_count=len(parsed.sections),
                            paragraph_count=sum(len(s.paragraphs) for s in parsed.sections),
                            character_count=sum(
                                len(p.text) for s in parsed.sections for p in s.paragraphs
                            ),
                        )
                        .returning(works)
                    )
                    .mappings()
                    .one()
                )
                work = WorkOut.model_validate(row)
                connection.execute(
                    sources.insert().values(
                        work_id=work_id,
                        content=data,
                        rule_version=RULE_VERSION,
                        layout={
                            "name": parsed.name.position.model_dump(),
                            "sections": [s.heading.position.model_dump() for s in parsed.sections],
                        },
                    )
                )
                for ordinal, section in enumerate(parsed.sections, 1):
                    section_id = uuid4()
                    connection.execute(
                        sections.insert().values(
                            id=section_id,
                            work_id=work_id,
                            ordinal=ordinal,
                            title=section.heading.text,
                            paragraph_count=len(section.paragraphs),
                        )
                    )
                    # 有界批次避免构造整部小说的 SQL 参数副本；事务仍覆盖所有批次。
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
        except IntegrityError as exc:
            constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
            if constraint not in {"uq_works_request_id", "uq_works_name"}:
                raise
            # 上一事务已回滚；在新快照读取获胜请求，避免失败连接继续执行。
            with self.database.engine.connect() as connection:
                previous = self._existing(connection, request_id, fingerprint)
            if previous is not None:
                return previous
            raise ServiceError("WORK_NAME_CONFLICT", "作品名称已存在", 409) from None
        return ImportOut(request_id=request_id, replayed=False, work=work)
