"""标注撤回状态与共享标签版本；旧回执和原文保持不变。"""

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """既有标注默认有效，标签从版本 1 开始；历史更新时间取创建时间。"""
    op.execute("""
        ALTER TABLE annotations ADD COLUMN status varchar(16) NOT NULL DEFAULT 'active';
        ALTER TABLE annotations ADD CONSTRAINT ck_annotations_status
            CHECK (status IN ('active','withdrawn'));
        COMMENT ON COLUMN annotations.status IS '有效或已撤回；撤回保留证据并退出默认检索';
        ALTER TABLE tags ADD COLUMN version integer NOT NULL DEFAULT 1;
        ALTER TABLE tags ADD CONSTRAINT ck_tags_version CHECK (version > 0);
        ALTER TABLE tags ADD COLUMN updated_at timestamptz;
        UPDATE tags SET updated_at=created_at;
        ALTER TABLE tags ALTER COLUMN updated_at SET DEFAULT now();
        ALTER TABLE tags ALTER COLUMN updated_at SET NOT NULL;
        COMMENT ON COLUMN tags.version IS '共享标签内容与并发修订的版本条件';
    """)


def downgrade() -> None:
    """移除本次字段，失去当前撤回状态及标签版本；历史请求回执仍保留。"""
    op.execute("""
        ALTER TABLE tags DROP CONSTRAINT ck_tags_version;
        ALTER TABLE tags DROP COLUMN updated_at, DROP COLUMN version;
        ALTER TABLE annotations DROP CONSTRAINT ck_annotations_status;
        ALTER TABLE annotations DROP COLUMN status;
    """)
