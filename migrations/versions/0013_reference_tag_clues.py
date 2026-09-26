"""标签定义和别名进入独立线索表示，重新登记同步；原文向量无需重建。"""

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """线索指纹由当前字段计算；待办使既有完成代也能刷新新表示。"""
    op.execute("SELECT enqueue_reference(id) FROM works")


def downgrade() -> None:
    """回退应用时也重新计算线索，不删除原文或标注。"""
    op.execute("SELECT enqueue_reference(id) FROM works")
