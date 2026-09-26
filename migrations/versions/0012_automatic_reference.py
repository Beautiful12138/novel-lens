"""原子登记索引待办，新增独立说明向量；不改变任何原文。"""

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """业务变更触发器和数据同事务提交，不依赖调用者记得登记后台任务。"""
    op.execute("""
CREATE TABLE reference_queue (
 work_id uuid PRIMARY KEY REFERENCES works(id) ON DELETE CASCADE,
 revision integer NOT NULL DEFAULT 1, indexed_revision integer NOT NULL DEFAULT 0,
 last_error varchar(64), retry_at timestamptz NOT NULL DEFAULT now(),
 CONSTRAINT ck_reference_queue_revision CHECK
 (revision>0 AND indexed_revision>=0 AND indexed_revision<=revision)
);
COMMENT ON TABLE reference_queue IS
 '作品检索更新待办；业务变更推进 revision，完整同步才推进 indexed_revision';
CREATE TABLE reference_clues (
 annotation_id uuid PRIMARY KEY REFERENCES annotations(id) ON DELETE CASCADE,
 work_id uuid NOT NULL REFERENCES works(id) ON DELETE CASCADE,
 fingerprint varchar(64) NOT NULL, contract_id varchar(64) NOT NULL,
 body text NOT NULL, embedding vector(1024), blocked_reason varchar(64),
 CONSTRAINT ck_reference_clues_state CHECK
 ((embedding IS NULL) = (blocked_reason IS NOT NULL)),
 CONSTRAINT ck_reference_clues_norm CHECK
 (embedding IS NULL OR abs(vector_norm(embedding)-1)<=0.001)
);
COMMENT ON TABLE reference_clues IS '标记说明与标签的独立检索表示；不拼入原文向量，不替代原文证据';
CREATE INDEX ix_reference_clues_body ON reference_clues USING pgroonga(body);
CREATE TABLE preparation_tags (
 work_id uuid REFERENCES works(id) ON DELETE CASCADE,
 tag_id uuid REFERENCES tags(id) ON DELETE CASCADE,
 PRIMARY KEY(work_id,tag_id)
);
COMMENT ON TABLE preparation_tags IS
 '准备批次新建标签的归属记录；清理仅移除未被其他资产引用的新建标签';

CREATE FUNCTION enqueue_reference(w uuid) RETURNS void LANGUAGE plpgsql AS $$
BEGIN
 IF w IS NOT NULL AND EXISTS(SELECT 1 FROM works WHERE id=w) THEN
  INSERT INTO reference_queue(work_id) VALUES(w)
  ON CONFLICT(work_id) DO UPDATE SET revision=reference_queue.revision+1,
    last_error=NULL,retry_at=now();
 END IF;
END $$;
CREATE FUNCTION touch_reference() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE w uuid;
BEGIN
 IF TG_TABLE_NAME='annotation_tags' THEN
  SELECT work_id INTO w FROM annotations
   WHERE id=CASE WHEN TG_OP='DELETE' THEN OLD.annotation_id ELSE NEW.annotation_id END;
 ELSIF TG_TABLE_NAME='tags' THEN
  FOR w IN SELECT DISTINCT a.work_id FROM annotations a
    JOIN annotation_tags t ON t.annotation_id=a.id WHERE t.tag_id=NEW.id
  LOOP PERFORM enqueue_reference(w); END LOOP;
  RETURN NULL;
 ELSE
  w:=CASE WHEN TG_OP='DELETE' THEN OLD.work_id ELSE NEW.work_id END;
 END IF;
 PERFORM enqueue_reference(w);
 RETURN NULL;
END $$;
-- 提交时才竞争队列行，避免标注批次在持其他业务锁之前提前串行化。
-- 延迟触发器仍处在同一事务中；触发失败会回滚所有正文、标记与进度。
CREATE CONSTRAINT TRIGGER reference_parts AFTER INSERT ON parts
 DEFERRABLE INITIALLY DEFERRED
 FOR EACH ROW EXECUTE FUNCTION touch_reference();
CREATE CONSTRAINT TRIGGER reference_annotations AFTER INSERT OR UPDATE OR DELETE ON annotations
 DEFERRABLE INITIALLY DEFERRED
 FOR EACH ROW EXECUTE FUNCTION touch_reference();
CREATE CONSTRAINT TRIGGER reference_ranges AFTER INSERT OR UPDATE OR DELETE ON annotation_ranges
 DEFERRABLE INITIALLY DEFERRED
 FOR EACH ROW EXECUTE FUNCTION touch_reference();
CREATE CONSTRAINT TRIGGER reference_tags AFTER INSERT OR DELETE ON annotation_tags
 DEFERRABLE INITIALLY DEFERRED
 FOR EACH ROW EXECUTE FUNCTION touch_reference();
CREATE CONSTRAINT TRIGGER reference_tag_updates AFTER UPDATE ON tags
 DEFERRABLE INITIALLY DEFERRED
 FOR EACH ROW EXECUTE FUNCTION touch_reference();
INSERT INTO reference_queue(work_id) SELECT id FROM works;
""")


def downgrade() -> None:
    """仅移除本增量派生能力，正文和已提交标记保持不变。"""
    op.execute("""
DROP TRIGGER reference_parts ON parts;
DROP TRIGGER reference_annotations ON annotations;
DROP TRIGGER reference_ranges ON annotation_ranges;
DROP TRIGGER reference_tags ON annotation_tags;
DROP TRIGGER reference_tag_updates ON tags;
DROP FUNCTION touch_reference(); DROP FUNCTION enqueue_reference(uuid);
DROP TABLE preparation_tags; DROP TABLE reference_clues; DROP TABLE reference_queue;
""")
