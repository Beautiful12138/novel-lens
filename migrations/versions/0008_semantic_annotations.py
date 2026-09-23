"""标注证据快照与两层派生索引；不修改标注正文、引用或进度。"""

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """快照限定同作品同层，切片以复合外键绑定整条标注的证据身份。"""
    op.execute("""
        ALTER TABLE semantic_indexes DROP CONSTRAINT ck_semantic_indexes_kind;
        ALTER TABLE semantic_indexes ADD CONSTRAINT ck_semantic_indexes_kind
            CHECK (kind IN ('fulltext','annotation'));
        CREATE TABLE semantic_annotation_snapshots (
            index_id uuid NOT NULL,
            annotation_id uuid NOT NULL REFERENCES annotations(id),
            work_id uuid NOT NULL, kind varchar(16) NOT NULL DEFAULT 'annotation',
            evidence_id varchar(64) NOT NULL, ranges jsonb NOT NULL,
            PRIMARY KEY (index_id, annotation_id),
            CONSTRAINT uq_semantic_annotation_evidence UNIQUE (index_id,annotation_id,evidence_id),
            FOREIGN KEY (work_id,kind,index_id) REFERENCES semantic_indexes(work_id,kind,id),
            CONSTRAINT ck_semantic_snapshot_kind CHECK (kind='annotation'),
            CONSTRAINT ck_semantic_snapshot_ranges CHECK
                (jsonb_typeof(ranges)='array' AND jsonb_array_length(ranges)>0)
        );
        COMMENT ON TABLE semantic_annotation_snapshots IS
            '创建代时的规范化引用与原文摘要；每标注一行，不保存正文，不随资产编辑变更';
        ALTER TABLE semantic_index_items
            ADD COLUMN annotation_id uuid,
            ADD COLUMN evidence_id varchar(64),
            ADD COLUMN range_ordinal integer,
            ADD CONSTRAINT fk_semantic_items_evidence
                FOREIGN KEY (index_id,annotation_id,evidence_id)
                REFERENCES semantic_annotation_snapshots(index_id,annotation_id,evidence_id),
            ADD CONSTRAINT ck_semantic_items_origin CHECK ((
                (kind='fulltext' AND annotation_id IS NULL AND evidence_id IS NULL
                    AND range_ordinal IS NULL)
                OR (kind='annotation' AND annotation_id IS NOT NULL AND evidence_id IS NOT NULL
                    AND range_ordinal IS NOT NULL AND range_ordinal>0)
            ) IS TRUE);
        CREATE INDEX ix_semantic_items_annotation
            ON semantic_index_items(index_id,annotation_id,range_ordinal);
    """)


def downgrade() -> None:
    """只移除标注层派生产物；全文代及其回执保持原样，扩展与分析资产保留。"""
    op.execute("""
        DELETE FROM semantic_index_heads WHERE kind='annotation';
        DELETE FROM semantic_build_receipts WHERE index_id IN
            (SELECT id FROM semantic_indexes WHERE kind='annotation');
        DELETE FROM semantic_index_items WHERE kind='annotation';
        ALTER TABLE semantic_index_items DROP CONSTRAINT fk_semantic_items_evidence,
            DROP CONSTRAINT ck_semantic_items_origin;
        DROP INDEX ix_semantic_items_annotation;
        ALTER TABLE semantic_index_items DROP COLUMN annotation_id,
            DROP COLUMN evidence_id, DROP COLUMN range_ordinal;
        DROP TABLE semantic_annotation_snapshots;
        DELETE FROM semantic_indexes WHERE kind='annotation';
        ALTER TABLE semantic_indexes DROP CONSTRAINT ck_semantic_indexes_kind;
        ALTER TABLE semantic_indexes ADD CONSTRAINT ck_semantic_indexes_kind
            CHECK (kind='fulltext');
    """)
