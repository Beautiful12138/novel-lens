"""空库切换作品分部模型；拒绝转换旧数据或隐式删除共享数据。"""

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """固定 SQL 定义新结构；原文、资产存在时由运维另选空库。"""
    op.execute("""
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM works)
        OR EXISTS (SELECT 1 FROM tags)
        OR EXISTS (SELECT 1 FROM asset_write_requests) THEN
        RAISE EXCEPTION '0011 requires an empty database; no legacy data migration.';
    END IF;
END $$;
DROP TABLE work_sources;
ALTER TABLE works DROP CONSTRAINT ck_works_counts;
ALTER TABLE works DROP COLUMN request_id, DROP COLUMN fingerprint;
ALTER TABLE works ADD COLUMN version integer NOT NULL, ADD COLUMN part_count integer NOT NULL;
ALTER TABLE works ADD CONSTRAINT ck_works_counts
    CHECK (part_count >= 0 AND section_count >= 0
        AND paragraph_count >= 0
        AND character_count >= 0 AND source_bytes >= 0 AND version > 0);
COMMENT ON TABLE works IS '作品分析归属及聚合统计；原文由分部保存';

CREATE TABLE parts (
	id UUID NOT NULL,
	work_id UUID NOT NULL,
	name VARCHAR(256) COLLATE "C" NOT NULL,
	ordinal INTEGER NOT NULL,
	version INTEGER NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	section_count INTEGER NOT NULL,
	paragraph_count INTEGER NOT NULL,
	character_count INTEGER NOT NULL,
	source_sha256 VARCHAR(64) NOT NULL,
	source_bytes INTEGER NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_parts_order UNIQUE (work_id, ordinal),
	CONSTRAINT uq_parts_name UNIQUE (work_id, name),
	CONSTRAINT uq_parts_owner UNIQUE (work_id, id),
	CONSTRAINT ck_parts_counts
    CHECK (ordinal > 0 AND version > 0 AND section_count > 0
        AND paragraph_count > 0
        AND character_count > 0 AND source_bytes > 0),
	FOREIGN KEY(work_id) REFERENCES works (id)
)

;
COMMENT ON TABLE parts IS '不可变原文的分部；名称可修改，顺序仅追加';

CREATE TABLE part_sources (
	part_id UUID NOT NULL,
	content BYTEA NOT NULL,
	rule_version VARCHAR(32) NOT NULL,
	layout JSONB NOT NULL,
	PRIMARY KEY (part_id),
	FOREIGN KEY(part_id) REFERENCES parts (id)
)

;
COMMENT ON TABLE part_sources IS '完整分部上传字节与字节定位；不因改名而修改原文件';

CREATE TABLE catalog_requests (
	request_id UUID NOT NULL,
	work_id UUID NOT NULL,
	operation VARCHAR(32) NOT NULL,
	fingerprint VARCHAR(64) NOT NULL,
	response JSONB NOT NULL,
	PRIMARY KEY (request_id),
	FOREIGN KEY(work_id) REFERENCES works (id)
)

;
COMMENT ON TABLE catalog_requests IS '作品创建、改名和分部导入的原子幂等回执；随作品删除';
ALTER TABLE sections ADD COLUMN part_id uuid NOT NULL;
ALTER TABLE sections ADD CONSTRAINT fk_sections_part
    FOREIGN KEY (work_id, part_id) REFERENCES parts(work_id, id);
ALTER TABLE analysis_jobs ADD COLUMN part_id uuid;
ALTER TABLE analysis_jobs ADD CONSTRAINT fk_jobs_part
    FOREIGN KEY (work_id, part_id) REFERENCES parts(work_id, id);
ALTER TABLE analysis_jobs DROP CONSTRAINT ck_analysis_jobs_target;
ALTER TABLE analysis_jobs ADD CONSTRAINT ck_analysis_jobs_target
    CHECK (target_kind IN ('whole_work', 'part', 'ranges')
        AND ((target_kind = 'part') = (part_id IS NOT NULL)));
    """)


def downgrade() -> None:
    """破坏性模型不提供旧结构转换；回退使用独立旧库。"""
    raise RuntimeError("0011 does not support downgrade; use a separate database")
