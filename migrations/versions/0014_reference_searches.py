"""固定搜索候选及持久游标；目录世代避免可见性改变后恢复造成旧搜索复活。"""

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """容量由唯一槽位及范围约束保证，不依赖进程锁或快照中的 count。"""
    op.execute("""
ALTER TABLE works ADD COLUMN reference_version bigint NOT NULL DEFAULT 1;
COMMENT ON COLUMN works.reference_version IS '搜索快照的目录与可见性世代；不受纯索引发布影响';
CREATE FUNCTION touch_reference_version() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 NEW.reference_version:=OLD.reference_version+1;
 RETURN NEW;
END $$;
CREATE TRIGGER reference_work_version BEFORE UPDATE ON works
 FOR EACH ROW EXECUTE FUNCTION touch_reference_version();
CREATE FUNCTION touch_reference_part_name() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 UPDATE works SET reference_version=reference_version+1 WHERE id=NEW.work_id;
 RETURN NULL;
END $$;
CREATE TRIGGER reference_part_name AFTER UPDATE OF name ON parts
 FOR EACH ROW WHEN (OLD.name IS DISTINCT FROM NEW.name)
 EXECUTE FUNCTION touch_reference_part_name();
CREATE TABLE reference_searches (
 id uuid PRIMARY KEY, slot integer NOT NULL UNIQUE,
 snapshot_at timestamptz NOT NULL, expires_at timestamptz NOT NULL,
 invalidated boolean NOT NULL DEFAULT false, payload jsonb NOT NULL,
 CONSTRAINT ck_reference_searches_slot CHECK (slot BETWEEN 1 AND 256),
 CONSTRAINT ck_reference_searches_expiry CHECK (expires_at>snapshot_at),
 CONSTRAINT ck_reference_searches_size CHECK (octet_length(payload::text)<=4194304)
);
CREATE INDEX ix_reference_searches_expiry ON reference_searches(expires_at);
COMMENT ON TABLE reference_searches IS
 '有期限的候选和诊断快照；唯一有界槽位约束跨进程容量，不保存完整原文或向量';
""")


def downgrade() -> None:
    """仅删除本期派生快照，不修改作品原文及标记。"""
    op.execute("""
DROP TABLE reference_searches;
DROP TRIGGER reference_part_name ON parts;
DROP FUNCTION touch_reference_part_name();
DROP TRIGGER reference_work_version ON works;
DROP FUNCTION touch_reference_version();
ALTER TABLE works DROP COLUMN reference_version;
""")
