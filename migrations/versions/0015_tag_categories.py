"""增加固定分类集合；保留全部标签身份和引用，旧标签归入未分类。"""

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
ALTER TABLE tags ADD COLUMN categories jsonb NOT NULL DEFAULT '[]';
ALTER TABLE tags ADD CONSTRAINT ck_tags_categories CHECK (
 jsonb_typeof(categories)='array' AND categories <@
 '["content","perspective","interaction","language","structure","emotion"]'::jsonb
);
COMMENT ON COLUMN tags.categories IS '固定写法分类的集合；空数组表示未分类';
-- 统一召回已移除，不再产生无人消费的同步待办；保留历史派生数据供独立清理。
ALTER TABLE parts DISABLE TRIGGER reference_parts;
ALTER TABLE annotations DISABLE TRIGGER reference_annotations;
ALTER TABLE annotation_ranges DISABLE TRIGGER reference_ranges;
ALTER TABLE annotation_tags DISABLE TRIGGER reference_tags;
ALTER TABLE tags DISABLE TRIGGER reference_tag_updates;
ALTER TABLE works DISABLE TRIGGER reference_work_version;
ALTER TABLE parts DISABLE TRIGGER reference_part_name;
""")


def downgrade() -> None:
    """只撤销分类元数据，不删除标签或分析成果。"""
    op.execute("ALTER TABLE tags DROP COLUMN categories")
    op.execute("""
ALTER TABLE parts ENABLE TRIGGER reference_parts;
ALTER TABLE annotations ENABLE TRIGGER reference_annotations;
ALTER TABLE annotation_ranges ENABLE TRIGGER reference_ranges;
ALTER TABLE annotation_tags ENABLE TRIGGER reference_tags;
ALTER TABLE tags ENABLE TRIGGER reference_tag_updates;
ALTER TABLE works ENABLE TRIGGER reference_work_version;
ALTER TABLE parts ENABLE TRIGGER reference_part_name;
SELECT enqueue_reference(id) FROM works;
""")
