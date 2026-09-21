"""直接索引原文和写法说明；无内容复制或异步同步过程。"""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """实例需先安装 PGroonga；缺少扩展时整次迁移失败并回滚。"""
    op.execute("CREATE EXTENSION IF NOT EXISTS pgroonga")
    for name, table, column in (
        ("ix_paragraphs_text_search", "paragraphs", "text"),
        ("ix_annotations_note_search", "annotations", "note"),
    ):
        op.execute(f"""
            CREATE INDEX {name} ON {table} USING pgroonga ({column})
            WITH (tokenizer='TokenBigram', normalizers='NormalizerAuto')
        """)
        op.execute(f"COMMENT ON INDEX {name} IS '直接索引当前字段，事务提交后可检索修订内容'")


def downgrade() -> None:
    """仅移除搜索索引；不删除原文、标注或可能被其他对象使用的扩展。"""
    op.drop_index("ix_annotations_note_search")
    op.drop_index("ix_paragraphs_text_search")
