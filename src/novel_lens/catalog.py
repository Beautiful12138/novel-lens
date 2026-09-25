"""作品及分部目录写入；幂等快照和业务变更在同一事务内提交。"""

import json
from collections.abc import Callable
from hashlib import sha256
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Connection, select, text
from sqlalchemy.exc import IntegrityError

from novel_lens.contracts import CatalogWrite, PartOut, PartUpdate, WorkCreate, WorkOut, WorkUpdate
from novel_lens.database import Database
from novel_lens.errors import ServiceError
from novel_lens.schema import catalog_requests, parts, works


def part_at(connection: Connection, work_id: UUID, part_id: UUID) -> PartOut:
    """显式验证作品隔离，分部不存在或归属错误统一返回不存在。"""
    row = (
        connection.execute(select(parts).where(parts.c.id == part_id, parts.c.work_id == work_id))
        .mappings()
        .first()
    )
    if row is None:
        raise ServiceError("PART_NOT_FOUND", "指定作品中不存在该分部", 404)
    return PartOut.model_validate(row)


def lock_work(connection: Connection, work_id: UUID) -> WorkOut:
    """目录修改持行锁；与删除互斥，并串行追加顺序和版本变更。"""
    row = (
        connection.execute(
            select(works).where(works.c.id == work_id).with_for_update(key_share=True)
        )
        .mappings()
        .first()
    )
    if row is None:
        raise ServiceError("WORK_NOT_FOUND", "作品不存在", 404)
    return WorkOut.model_validate(row)


class CatalogService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def write(
        self,
        request_id: UUID,
        operation: str,
        payload: dict[str, Any],
        action: Callable[[Connection], tuple[UUID, dict[str, Any]]],
    ) -> dict[str, Any]:
        """请求键锁早于作品锁；重放发生在当前版本、文件名等可变条件校验之前。"""
        fingerprint = sha256(
            json.dumps([operation, payload], sort_keys=True, ensure_ascii=True).encode()
        ).hexdigest()
        key = int.from_bytes(sha256(f"catalog:{request_id}".encode()).digest()[:8], signed=True)
        try:
            with self.database.engine.begin() as connection:
                connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
                old = (
                    connection.execute(
                        select(catalog_requests).where(catalog_requests.c.request_id == request_id)
                    )
                    .mappings()
                    .first()
                )
                if old is not None:
                    if old["fingerprint"] != fingerprint:
                        raise ServiceError("REQUEST_CONFLICT", "请求键已用于另一操作或输入", 409)
                    return {**old["response"], "replayed": True}
                work_id, result = action(connection)
                connection.execute(
                    catalog_requests.insert().values(
                        request_id=request_id,
                        work_id=work_id,
                        operation=operation,
                        fingerprint=fingerprint,
                        response=result,
                    )
                )
                return result
        except IntegrityError as exc:
            constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
            if constraint in {"uq_works_name", "uq_parts_name"}:
                code = (
                    "WORK_NAME_CONFLICT" if constraint == "uq_works_name" else "PART_NAME_CONFLICT"
                )
                raise ServiceError(code, "名称已存在", 409) from None
            raise

    def create(self, request: WorkCreate) -> CatalogWrite:
        """先创建空作品，再通过独立的分部导入追加原文。"""

        def action(connection: Connection) -> tuple[UUID, dict[str, Any]]:
            identifier = uuid4()
            row = (
                connection.execute(
                    works.insert()
                    .values(
                        id=identifier,
                        name=request.name,
                        version=1,
                        part_count=0,
                        section_count=0,
                        paragraph_count=0,
                        character_count=0,
                        source_bytes=0,
                        source_sha256=sha256(b"").hexdigest(),
                    )
                    .returning(works)
                )
                .mappings()
                .one()
            )
            result = CatalogWrite(request_id=request.request_id, result=WorkOut.model_validate(row))
            return identifier, result.model_dump(mode="json")

        return CatalogWrite.model_validate(
            self.write(request.request_id, "work_create", request.model_dump(mode="json"), action)
        )

    def update(self, request: WorkUpdate | PartUpdate) -> CatalogWrite:
        """只修改目录名称；原文件、顺序和所有证据坐标保持不变。"""
        is_part = isinstance(request, PartUpdate)

        def action(connection: Connection) -> tuple[UUID, dict[str, Any]]:
            work = lock_work(connection, request.work_id)
            current: WorkOut | PartOut = (
                part_at(connection, request.work_id, request.part_id)
                if isinstance(request, PartUpdate)
                else work
            )
            if current.version != request.expected_version:
                raise ServiceError(
                    "VERSION_CONFLICT", "目录版本已变化", 409, {"current_version": current.version}
                )
            table = parts if is_part else works
            row = (
                connection.execute(
                    table.update()
                    .where(table.c.id == current.id)
                    .values(name=request.name, version=current.version + 1)
                    .returning(table)
                )
                .mappings()
                .one()
            )
            result = PartOut.model_validate(row) if is_part else WorkOut.model_validate(row)
            return work.id, CatalogWrite(request_id=request.request_id, result=result).model_dump(
                mode="json"
            )

        return CatalogWrite.model_validate(
            self.write(
                request.request_id,
                "part_update" if is_part else "work_update",
                request.model_dump(mode="json"),
                action,
            )
        )
