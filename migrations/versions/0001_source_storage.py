"""保存作品、完整文件及稳定章节段落；唯一键支持并发幂等。

Revision ID: 0001
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 此迁移固定当时的 SQL，不导入可能随业务演进变化的运行时表定义。
    op.execute("""
        CREATE TABLE works (
            id uuid PRIMARY KEY, name varchar(256) COLLATE "C" NOT NULL,
            request_id uuid NOT NULL, fingerprint varchar(64) NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            section_count integer NOT NULL, paragraph_count integer NOT NULL,
            character_count integer NOT NULL,
            source_sha256 varchar(64) NOT NULL, source_bytes integer NOT NULL,
            CONSTRAINT uq_works_name UNIQUE (name),
            CONSTRAINT uq_works_request_id UNIQUE (request_id),
            CONSTRAINT ck_works_counts CHECK (
                section_count > 0 AND paragraph_count > 0 AND character_count > 0)
        );
        COMMENT ON TABLE works IS '作品与成功请求；名称唯一防止覆盖，请求键唯一防止重试重复创建';
        COMMENT ON COLUMN works.fingerprint IS '导入协议和输入内容摘要；同键不同输入返回冲突';
        CREATE INDEX ix_works_created_id ON works (created_at, id);
        CREATE TABLE work_sources (
            work_id uuid PRIMARY KEY REFERENCES works(id),
            content bytea NOT NULL,
            rule_version varchar(32) NOT NULL, layout jsonb NOT NULL
        );
        COMMENT ON TABLE work_sources IS '完整输入字节及核验依据，与作品和所有段落同事务保存';
        CREATE TABLE sections (
            id uuid PRIMARY KEY, work_id uuid NOT NULL REFERENCES works(id),
            ordinal integer NOT NULL, title text NOT NULL, paragraph_count integer NOT NULL,
            CONSTRAINT uq_sections_order UNIQUE (work_id, ordinal),
            CONSTRAINT ck_sections_counts CHECK (ordinal > 0 AND paragraph_count >= 0)
        );
        COMMENT ON TABLE sections IS '平铺目录；作品内顺序唯一，标题可重复，允许空结构单元';
        CREATE TABLE paragraphs (
            id uuid PRIMARY KEY, section_id uuid NOT NULL REFERENCES sections(id),
            ordinal integer NOT NULL, text text NOT NULL,
            source_position jsonb NOT NULL,
            CONSTRAINT uq_paragraphs_order UNIQUE (section_id, ordinal),
            CONSTRAINT ck_paragraphs_content CHECK (ordinal > 0 AND length(text) > 0)
        );
        COMMENT ON TABLE paragraphs IS '保真自然段及稳定 ID；Section 内顺序唯一，不按文学场景切分';
        COMMENT ON COLUMN paragraphs.source_position
            IS '上传文件字节半开区间及一基行号，不含行终止符';
    """)


def downgrade() -> None:
    """显式回退会删除导入数据，只应在允许销毁数据的环境执行。"""
    op.drop_table("paragraphs")
    op.drop_table("sections")
    op.drop_table("work_sources")
    op.drop_table("works")
