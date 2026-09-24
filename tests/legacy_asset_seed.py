"""在旧数据库中构造历史形状的样例；不调用要求 0009 字段的当前资产读取。"""

from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Column, Connection, Table

from novel_lens.asset_contracts import (
    AnnotationCreate,
    LegacyEntityAnnotationOut,
    LegacyTagOut,
    TagCreate,
)
from novel_lens.assets import AssetService
from novel_lens.contracts import SourceRange
from novel_lens.schema import annotations, tags


def legacy_columns(table: Table) -> list[Column[Any]]:
    """旧规格验证比较当时已有字段，新增字段另由 0009 迁移测试核验。"""
    added = {"tags": {"version", "updated_at"}, "annotations": {"status"}}
    return [c for c in table.c if c.name not in added.get(table.name, set())]


def create_tag(assets: AssetService, namespace: str, name: str) -> LegacyTagOut:
    request = TagCreate(
        request_id=uuid4(), namespace=namespace, name=name, description="迁移样例定义"
    )

    def action(conn: Connection) -> LegacyTagOut:
        row = (
            conn.execute(
                tags.insert()
                .values(id=uuid4(), **request.model_dump(exclude={"request_id"}))
                .returning(*legacy_columns(tags))
            )
            .mappings()
            .one()
        )
        return LegacyTagOut.model_validate(dict(row) | {"full_name": f"{namespace}/{name}"})

    result = assets._write(request, "tag_create", action).result
    assert isinstance(result, LegacyTagOut)
    return result


def create_annotation(
    assets: AssetService,
    source: tuple[UUID, list[SourceRange]],
    **kwargs: Any,
) -> LegacyEntityAnnotationOut:
    request = AnnotationCreate(
        request_id=uuid4(), work_id=source[0], source_ranges=source[1], **kwargs
    )

    def action(conn: Connection) -> LegacyEntityAnnotationOut:
        assets._validate_references(conn, request)
        row = (
            conn.execute(
                annotations.insert()
                .values(id=uuid4(), work_id=source[0], note=request.note, version=1)
                .returning(*legacy_columns(annotations))
            )
            .mappings()
            .one()
        )
        assets._save_references(conn, row["id"], request)
        return LegacyEntityAnnotationOut.model_validate(
            dict(row)
            | {
                "source_ranges": request.source_ranges,
                "tag_ids": request.tag_ids,
                "entity_ids": request.entity_ids,
            }
        )

    result = assets._write(request, "annotation_create", action).result
    assert isinstance(result, LegacyEntityAnnotationOut)
    return result
