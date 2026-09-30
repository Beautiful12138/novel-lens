"""统一具体观察与作品认识；开发阶段清理旧分析，不迁移猜测的文学内容。

这是不可逆的数据清理升级。操作者须事先备份并验证恢复；本脚本不替代备份。
原文、任务目标、实体关系和原文层索引保留，旧标注、导航及相关回执失效。
"""

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """DDL 与清理同一 PostgreSQL 事务，失败不得留下部分重置状态。"""
    op.execute("""
-- 只清理标注层派生向量；原文层的代、向量与回执不变。
DELETE FROM semantic_build_receipts WHERE index_id IN
 (SELECT id FROM semantic_indexes WHERE kind='annotation');
DELETE FROM semantic_index_heads WHERE kind='annotation';
DELETE FROM semantic_index_items WHERE kind='annotation';
DELETE FROM semantic_annotation_snapshots;
DELETE FROM semantic_indexes WHERE kind='annotation';
DELETE FROM reference_clues;
DELETE FROM reference_searches;
DELETE FROM annotation_entities;
DELETE FROM annotation_tags;
DELETE FROM annotation_ranges;
DELETE FROM annotations;
DROP TABLE style_guide_ranges;
DROP TABLE style_guide_entries;
DROP TABLE style_guides;
-- 旧回执不能再恢复已删除的资产或旧覆盖；保留导入与其他独立资产回执。
DELETE FROM asset_write_requests WHERE response->>'operation' IN
 ('annotation_create','annotation_update','annotation_set_status',
  'style_guide_create','style_guide_update','analysis_checkpoint',
  'analysis_job_create','analysis_job_update','analysis_job_complete','coverage_mark');
DELETE FROM catalog_requests WHERE operation IN ('prepare_batch','prepare_finish');
DELETE FROM analysis_coverage;
UPDATE analysis_jobs SET status='running', version=version+1,
 recovery='{"next_action":"旧分析已清理，请按新标注契约从任务目标开始重新阅读分析。"}'::jsonb,
 completion=NULL, updated_at=clock_timestamp();
-- 普通词表保留。只删除已无人引用的旧导航用途标签。
DELETE FROM preparation_tags WHERE tag_id IN
 (SELECT id FROM tags WHERE namespace='导航' AND name='作品写法'
  AND NOT EXISTS (SELECT 1 FROM relation_tags r WHERE r.tag_id=tags.id));
DELETE FROM asset_write_requests WHERE response->>'operation' IN ('tag_create','tag_update')
 AND response->'result'->>'id' IN
 (SELECT id::text FROM tags WHERE namespace='导航' AND name='作品写法'
  AND NOT EXISTS (SELECT 1 FROM relation_tags r WHERE r.tag_id=tags.id));
DELETE FROM tags WHERE namespace='导航' AND name='作品写法'
 AND NOT EXISTS (SELECT 1 FROM relation_tags r WHERE r.tag_id=tags.id);
COMMENT ON TABLE annotations IS
 '具体观察与作品认识；版本条件防止并发覆盖，不代表分析进度';
COMMENT ON TABLE analysis_jobs IS
 '独立深读任务；接续信息不替代进度，完成说明记录当前认识的限制';
ALTER TABLE annotations ADD COLUMN kind varchar(16) NOT NULL;
ALTER TABLE annotations ADD COLUMN title varchar(256) NOT NULL;
ALTER TABLE annotations ADD COLUMN scope_note text NOT NULL;
ALTER TABLE annotations ALTER COLUMN note SET NOT NULL;
ALTER TABLE annotations ADD CONSTRAINT ck_annotations_kind
 CHECK(kind IN ('observation','comparison'));
ALTER TABLE annotations ADD CONSTRAINT ck_annotations_content
 CHECK(length(btrim(title))>0 AND length(btrim(scope_note))>0 AND length(btrim(note))>0);
ALTER TABLE annotation_ranges ADD COLUMN reading_start_paragraph_id
 uuid NOT NULL REFERENCES paragraphs(id);
ALTER TABLE annotation_ranges ADD COLUMN reading_end_paragraph_id
 uuid NOT NULL REFERENCES paragraphs(id);
ALTER TABLE annotation_ranges ADD COLUMN role_note text NOT NULL;
ALTER TABLE annotation_ranges ADD CONSTRAINT ck_annotation_ranges_role
 CHECK(length(btrim(role_note))>0);
COMMENT ON COLUMN annotations.kind
 IS '具体观察 observation 或作品认识 comparison；不代表质量和覆盖';
COMMENT ON COLUMN annotations.scope_note IS 'AI 声明的适用范围与限制，不替代实际分析进度';
COMMENT ON COLUMN annotation_ranges.role_note IS '该处证据在当前认识中的用途';
COMMENT ON COLUMN annotation_ranges.reading_start_paragraph_id
 IS '同章连续阅读起点，业务事务核验证据包含关系';
""")


def downgrade() -> None:
    """旧分析不可由 DDL 恢复，明确要求使用升级前备份。"""
    raise RuntimeError("0016 不支持降级恢复旧分析，请恢复升级前已验证的数据库备份")
