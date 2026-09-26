"""自动检索待办和说明向量；原文向量继续复用语义索引表。"""

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

from novel_lens.semantic_schema import Vector


def register_reference_tables(metadata: MetaData) -> None:
    """待办与业务事务一起提交；说明向量只在身份仍匹配时参与召回。"""
    searches = Table(
        "reference_searches",
        metadata,
        Column("id", Uuid, primary_key=True),
        Column("slot", Integer, nullable=False, unique=True),
        Column("snapshot_at", DateTime(timezone=True), nullable=False),
        Column("expires_at", DateTime(timezone=True), nullable=False),
        Column("invalidated", Boolean, nullable=False, server_default=text("false")),
        Column("payload", JSONB, nullable=False),
        CheckConstraint("slot BETWEEN 1 AND 256", name="ck_reference_searches_slot"),
        CheckConstraint("expires_at > snapshot_at", name="ck_reference_searches_expiry"),
        CheckConstraint(
            "octet_length(payload::text) <= 4194304", name="ck_reference_searches_size"
        ),
        comment="有期限的候选和诊断快照；唯一有界槽位约束跨进程容量，不保存完整原文或向量",
    )
    Index("ix_reference_searches_expiry", searches.c.expires_at)
    Table(
        "reference_queue",
        metadata,
        Column("work_id", Uuid, ForeignKey("works.id", ondelete="CASCADE"), primary_key=True),
        Column("revision", Integer, nullable=False, server_default=text("1")),
        Column("indexed_revision", Integer, nullable=False, server_default=text("0")),
        Column("last_error", String(64)),
        Column("retry_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(
            "revision > 0 AND indexed_revision >= 0 AND indexed_revision <= revision",
            name="ck_reference_queue_revision",
        ),
        comment="作品检索更新待办；业务变更推进 revision，完整同步才推进 indexed_revision",
    )
    clues = Table(
        "reference_clues",
        metadata,
        Column(
            "annotation_id",
            Uuid,
            ForeignKey("annotations.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        Column("work_id", Uuid, ForeignKey("works.id", ondelete="CASCADE"), nullable=False),
        Column("fingerprint", String(64), nullable=False),
        Column("contract_id", String(64), nullable=False),
        Column("body", Text, nullable=False),
        Column("embedding", Vector()),
        Column("blocked_reason", String(64)),
        CheckConstraint(
            "(embedding IS NULL) = (blocked_reason IS NOT NULL)",
            name="ck_reference_clues_state",
        ),
        CheckConstraint(
            "embedding IS NULL OR abs(vector_norm(embedding)-1) <= 0.001",
            name="ck_reference_clues_norm",
        ),
        comment="标记说明与标签的独立检索表示；不拼入原文向量，不替代原文证据",
    )
    Index("ix_reference_clues_body", clues.c.body, postgresql_using="pgroonga")
    Table(
        "preparation_tags",
        metadata,
        Column("work_id", Uuid, ForeignKey("works.id", ondelete="CASCADE"), primary_key=True),
        Column("tag_id", Uuid, ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
        comment="准备批次新建标签的归属记录；清理仅移除未被其他资产引用的新建标签",
    )
