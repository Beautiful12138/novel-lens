"""派生索引元数据与固定维度 SQL 类型；与 0007 保持一致用于迁移差异检查。"""

from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql.base import ischema_names
from sqlalchemy.types import UserDefinedType


class Vector(UserDefinedType[str]):
    """业务 SQL 显式绑定 JSON 数组并 cast；元数据仅声明与反射维度，不引入第二份向量。"""

    cache_ok = True

    def __init__(self, dimensions: int = 1024) -> None:
        self.dimensions = dimensions

    def get_col_spec(self, **kw: Any) -> str:
        return f"vector({self.dimensions})"


ischema_names["vector"] = Vector


def register_semantic_tables(metadata: MetaData) -> None:
    """按依赖顺序声明四张表；跨作品端点仍在写入短事务中核验。"""
    Table(
        "semantic_indexes",
        metadata,
        Column("id", Uuid, primary_key=True),
        Column("work_id", Uuid, ForeignKey("works.id"), nullable=False),
        Column("kind", String(16), nullable=False),
        Column("request_id", Uuid, nullable=False),
        Column("fingerprint", String(64), nullable=False),
        Column("contract_id", String(64), nullable=False),
        Column("source_sha256", String(64), nullable=False),
        Column("status", String(16), nullable=False),
        Column("cursor", JSONB, nullable=False),
        Column("scanned", Boolean, nullable=False, server_default=text("false")),
        Column("last_error", String(64)),
        Column("create_response", JSONB, nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        UniqueConstraint("work_id", "kind", "id", name="uq_semantic_indexes_scope"),
        UniqueConstraint("work_id", "kind", "request_id", name="uq_semantic_indexes_request"),
        CheckConstraint("kind='fulltext'", name="ck_semantic_indexes_kind"),
        CheckConstraint(
            "status IN ('building','ready','partial','failed','superseded')",
            name="ck_semantic_indexes_status",
        ),
        comment="显式创建的全文索引代；游标只表示规划位置，创建回执保存原结果快照",
    )
    Table(
        "semantic_index_heads",
        metadata,
        Column("work_id", Uuid, ForeignKey("works.id"), primary_key=True),
        Column("kind", String(16), primary_key=True),
        Column("active_index_id", Uuid),
        Column("target_index_id", Uuid),
        ForeignKeyConstraint(
            ["work_id", "kind", "active_index_id"],
            ["semantic_indexes.work_id", "semantic_indexes.kind", "semantic_indexes.id"],
        ),
        ForeignKeyConstraint(
            ["work_id", "kind", "target_index_id"],
            ["semantic_indexes.work_id", "semantic_indexes.kind", "semantic_indexes.id"],
        ),
        comment="同作品同层的可查询完整代与构建目标；完整发布时原子切换 active",
    )
    items = Table(
        "semantic_index_items",
        metadata,
        Column("id", Uuid, primary_key=True),
        Column("index_id", Uuid, nullable=False),
        Column("work_id", Uuid, nullable=False),
        Column("kind", String(16), nullable=False),
        Column("source_key", String(64), nullable=False),
        Column("section_id", Uuid, ForeignKey("sections.id"), nullable=False),
        Column("start_paragraph_id", Uuid, ForeignKey("paragraphs.id"), nullable=False),
        Column("end_paragraph_id", Uuid, ForeignKey("paragraphs.id"), nullable=False),
        Column("start_ordinal", Integer, nullable=False),
        Column("end_ordinal", Integer, nullable=False),
        Column("text_sha256", String(64), nullable=False),
        Column("tokens", Integer),
        Column("bytes", Integer, nullable=False),
        Column("embedding", Vector()),
        Column("blocked_reason", String(64)),
        ForeignKeyConstraint(
            ["work_id", "kind", "index_id"],
            ["semantic_indexes.work_id", "semantic_indexes.kind", "semantic_indexes.id"],
        ),
        UniqueConstraint("index_id", "source_key", name="uq_semantic_items_source"),
        CheckConstraint(
            "start_ordinal > 0 AND end_ordinal >= start_ordinal AND bytes > 0",
            name="ck_semantic_items_range",
        ),
        CheckConstraint(
            """
            ((embedding IS NOT NULL AND blocked_reason IS NULL AND tokens BETWEEN 1 AND 4095)
            OR (embedding IS NULL AND blocked_reason IS NULL AND tokens BETWEEN 1 AND 4095)
            OR (embedding IS NULL AND blocked_reason='INPUT_TOO_LONG' AND tokens > 4095)
            OR (embedding IS NULL AND blocked_reason='TOKENIZATION_INPUT_TOO_LARGE'
                AND tokens IS NULL AND bytes > 1048576)) IS TRUE
        """,
            name="ck_semantic_items_state",
        ),
        CheckConstraint(
            "embedding IS NULL OR abs(vector_norm(embedding)-1) <= 0.001",
            name="ck_semantic_items_norm",
        ),
        comment="连续完整段落切片；短事务校验原文归属及端点，ready 向量或明确阻塞，不复制正文",
    )
    Index("ix_semantic_items_scope", items.c.work_id, items.c.index_id, items.c.section_id)
    Table(
        "semantic_build_receipts",
        metadata,
        Column("index_id", Uuid, ForeignKey("semantic_indexes.id"), primary_key=True),
        Column("request_id", Uuid, primary_key=True),
        Column("fingerprint", String(64), nullable=False),
        Column("response", JSONB, nullable=False),
        comment="与切片和游标原子提交的批次回执；同请求重放原快照，不执行下一批",
    )
