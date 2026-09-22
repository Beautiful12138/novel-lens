"""全文语义派生索引；不复制正文，不修改分析进度。"""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """先核对扩展，再原子创建代、同作品指针、切片及批次回执。"""
    op.execute("CREATE EXTENSION IF NOT EXISTS vector VERSION '0.8.6'")
    op.execute("""
        DO $$ BEGIN
            IF (SELECT extversion FROM pg_extension WHERE extname='vector') <> '0.8.6' THEN
                RAISE EXCEPTION 'pgvector 0.8.6 required';
            END IF;
        END $$;
        CREATE TABLE semantic_indexes (
            id uuid PRIMARY KEY,
            work_id uuid NOT NULL REFERENCES works(id), kind varchar(16) NOT NULL,
            request_id uuid NOT NULL, fingerprint varchar(64) NOT NULL,
            contract_id varchar(64) NOT NULL, source_sha256 varchar(64) NOT NULL,
            status varchar(16) NOT NULL, cursor jsonb NOT NULL,
            scanned boolean NOT NULL DEFAULT false, last_error varchar(64),
            create_response jsonb NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_semantic_indexes_scope UNIQUE (work_id, kind, id),
            CONSTRAINT uq_semantic_indexes_request UNIQUE (work_id, kind, request_id),
            CONSTRAINT ck_semantic_indexes_kind CHECK (kind='fulltext'),
            CONSTRAINT ck_semantic_indexes_status CHECK
                (status IN ('building','ready','partial','failed','superseded'))
        );
        COMMENT ON TABLE semantic_indexes IS
            '显式创建的全文索引代；游标只表示规划位置，创建回执保存原结果快照';
        CREATE TABLE semantic_index_heads (
            work_id uuid NOT NULL REFERENCES works(id), kind varchar(16) NOT NULL,
            active_index_id uuid, target_index_id uuid,
            PRIMARY KEY (work_id, kind),
            FOREIGN KEY (work_id, kind, active_index_id)
                REFERENCES semantic_indexes(work_id, kind, id),
            FOREIGN KEY (work_id, kind, target_index_id)
                REFERENCES semantic_indexes(work_id, kind, id)
        );
        COMMENT ON TABLE semantic_index_heads IS
            '同作品同层的可查询完整代与构建目标；完整发布时原子切换 active';
        CREATE TABLE semantic_index_items (
            id uuid PRIMARY KEY, index_id uuid NOT NULL,
            work_id uuid NOT NULL, kind varchar(16) NOT NULL,
            source_key varchar(64) NOT NULL,
            section_id uuid NOT NULL REFERENCES sections(id),
            start_paragraph_id uuid NOT NULL REFERENCES paragraphs(id),
            end_paragraph_id uuid NOT NULL REFERENCES paragraphs(id),
            start_ordinal integer NOT NULL, end_ordinal integer NOT NULL,
            text_sha256 varchar(64) NOT NULL, tokens integer, bytes integer NOT NULL,
            embedding vector(1024), blocked_reason varchar(64),
            FOREIGN KEY (work_id, kind, index_id) REFERENCES semantic_indexes(work_id, kind, id),
            CONSTRAINT uq_semantic_items_source UNIQUE (index_id, source_key),
            CONSTRAINT ck_semantic_items_range CHECK
                (start_ordinal > 0 AND end_ordinal >= start_ordinal AND bytes > 0),
            CONSTRAINT ck_semantic_items_state CHECK ((
                (embedding IS NOT NULL AND blocked_reason IS NULL AND tokens BETWEEN 1 AND 4095)
                OR (embedding IS NULL AND blocked_reason IS NULL AND tokens BETWEEN 1 AND 4095)
                OR (embedding IS NULL AND blocked_reason='INPUT_TOO_LONG' AND tokens > 4095)
                OR (embedding IS NULL AND blocked_reason='TOKENIZATION_INPUT_TOO_LARGE'
                    AND tokens IS NULL AND bytes > 1048576)
            ) IS TRUE),
            CONSTRAINT ck_semantic_items_norm CHECK
                (embedding IS NULL OR abs(vector_norm(embedding)-1) <= 0.001)
        );
        CREATE INDEX ix_semantic_items_scope ON semantic_index_items(work_id, index_id, section_id);
        COMMENT ON TABLE semantic_index_items IS
            '连续完整段落切片；短事务校验原文归属及端点，ready 向量或明确阻塞，不复制正文';
        CREATE TABLE semantic_build_receipts (
            index_id uuid NOT NULL REFERENCES semantic_indexes(id), request_id uuid NOT NULL,
            fingerprint varchar(64) NOT NULL, response jsonb NOT NULL,
            PRIMARY KEY (index_id, request_id)
        );
        COMMENT ON TABLE semantic_build_receipts IS
            '与切片和游标原子提交的批次回执；同请求重放原快照，不执行下一批';
    """)


def downgrade() -> None:
    """只删本增量派生数据；保留原文、分析资产与可能被其他对象使用的扩展。"""
    for name in (
        "semantic_build_receipts",
        "semantic_index_items",
        "semantic_index_heads",
        "semantic_indexes",
    ):
        op.drop_table(name)
