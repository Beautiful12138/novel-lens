"""为资产行为测试提供明确的新标注内容，正文范围由各用例给定。"""

from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text

from novel_lens.asset_contracts import AnnotationCreate, AnnotationOut
from novel_lens.assets import AssetService
from novel_lens.contracts import SourceRange
from novel_lens.database import Database


def annotation_fields(ranges: list[Any]) -> dict[str, Any]:
    return {
        "kind": "observation",
        "title": "样例写法观察",
        "scope_note": "限于本次给定的原文范围",
        "references": [
            {"evidence_range": ref, "role_note": "支撑这项观察的原文"} for ref in ranges
        ],
    }


def references(database: Database, work: UUID) -> list[SourceRange]:
    with database.engine.connect() as conn:
        rows = conn.execute(
            text("""
            SELECT p.id,p.section_id FROM paragraphs p JOIN sections s ON s.id=p.section_id
            WHERE s.work_id=:work ORDER BY s.ordinal,p.ordinal
        """),
            {"work": work},
        ).mappings()
        return [
            SourceRange(
                work_id=work,
                section_id=r["section_id"],
                start_paragraph_id=r["id"],
                end_paragraph_id=r["id"],
            )
            for r in rows
        ]


def annotation(database: Database, work: UUID, refs: list[SourceRange]) -> AnnotationOut:
    result = (
        AssetService(database)
        .create_annotation(
            AnnotationCreate(
                work_id=work, request_id=uuid4(), **annotation_fields(refs), note="说明"
            )
        )
        .result
    )
    assert isinstance(result, AnnotationOut)
    return result
