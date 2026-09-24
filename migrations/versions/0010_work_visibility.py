"""作品检索可见性；屏蔽不改变原文、资产、坐标或名称唯一性。"""

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """既有作品保持可检索，数据库约束拒绝未知状态。"""
    op.execute("""
        ALTER TABLE works ADD COLUMN visibility varchar(16) NOT NULL DEFAULT 'visible';
        ALTER TABLE works ADD CONSTRAINT ck_works_visibility
            CHECK (visibility IN ('visible', 'hidden'));
        COMMENT ON COLUMN works.visibility IS '检索可见性；屏蔽保留全部数据及名称占用';
    """)


def downgrade() -> None:
    """移除屏蔽状态，回退后所有保留作品重新可检索。"""
    op.execute("""
        ALTER TABLE works DROP CONSTRAINT ck_works_visibility;
        ALTER TABLE works DROP COLUMN visibility;
    """)
