"""移除废弃语义索引与统一召回表，保留原文和当前分析资产。

旧向量属于派生数据，执行前须备份并验证恢复。本迁移不操作磁盘模型。
vector 扩展只在没有其他依赖时删除；若有其他用途，整次迁移失败回滚，禁止级联误删。
"""

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """按外键依赖删除派生表；与版本记录在同一 PostgreSQL 事务提交。"""
    op.execute("""
        DROP TRIGGER reference_parts ON parts;
        DROP TRIGGER reference_annotations ON annotations;
        DROP TRIGGER reference_ranges ON annotation_ranges;
        DROP TRIGGER reference_tags ON annotation_tags;
        DROP TRIGGER reference_tag_updates ON tags;
        DROP TRIGGER reference_work_version ON works;
        DROP TRIGGER reference_part_name ON parts;
        DROP FUNCTION touch_reference();
        DROP FUNCTION enqueue_reference(uuid);
        DROP FUNCTION touch_reference_version();
        DROP FUNCTION touch_reference_part_name();
        ALTER TABLE works DROP COLUMN reference_version;
    """)
    for table in (
        "reference_searches",
        "reference_clues",
        "reference_queue",
        "semantic_index_heads",
        "semantic_build_receipts",
        "semantic_index_items",
        "semantic_annotation_snapshots",
        "semantic_indexes",
    ):
        op.drop_table(table)
    # 不使用 CASCADE；独立于 NovelLens 的向量对象不能被本迁移删除。
    op.execute("DROP EXTENSION IF EXISTS vector")


def downgrade() -> None:
    """删除的索引和构建回执不能由 DDL 重建，恢复旧服务应使用备份。"""
    raise RuntimeError("0017 不支持恢复旧语义索引，请恢复升级前已验证的数据库备份")
