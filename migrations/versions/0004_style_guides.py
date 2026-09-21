"""增加作品级风格导航；保留原文、既有资产及写入快照。"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """固定三张新增表；作品主键裁决创建竞争，复合外键保持条目证据归属。"""
    op.execute("""
        CREATE TABLE style_guides (
            work_id uuid PRIMARY KEY REFERENCES works(id), scope_note text NOT NULL,
            version integer NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_style_guides_version CHECK (version > 0)
        );
        COMMENT ON TABLE style_guides IS
            '每作品一份风格导航；范围说明是调用方声明，不代表分析进度';
        CREATE TABLE style_guide_entries (
            work_id uuid NOT NULL REFERENCES style_guides(work_id), ordinal integer NOT NULL,
            title varchar(256) NOT NULL, kind varchar(16) NOT NULL,
            description text NOT NULL, applicability text NOT NULL,
            PRIMARY KEY (work_id, ordinal),
            CONSTRAINT ck_style_guide_entries_ordinal CHECK (ordinal > 0),
            CONSTRAINT ck_style_guide_entries_kind CHECK
                (kind IN ('baseline', 'variation', 'exception'))
        );
        COMMENT ON TABLE style_guide_entries IS
            '有序写法判断及适用边界；整体修订时替换，顺序不是稳定身份';
        CREATE TABLE style_guide_ranges (
            work_id uuid NOT NULL, entry_ordinal integer NOT NULL, ordinal integer NOT NULL,
            section_id uuid NOT NULL REFERENCES sections(id),
            start_paragraph_id uuid NOT NULL REFERENCES paragraphs(id),
            end_paragraph_id uuid NOT NULL REFERENCES paragraphs(id),
            PRIMARY KEY (work_id, entry_ordinal, ordinal),
            FOREIGN KEY (work_id, entry_ordinal) REFERENCES style_guide_entries(work_id, ordinal),
            CONSTRAINT uq_style_guide_ranges_ref UNIQUE
                (work_id, entry_ordinal, section_id, start_paragraph_id, end_paragraph_id),
            CONSTRAINT ck_style_guide_ranges_ordinal CHECK (ordinal > 0)
        );
        COMMENT ON TABLE style_guide_ranges IS
            '条目有序证据；事务内核验同作品、同章节及端点顺序，不复制原文';
    """)


def downgrade() -> None:
    """显式回退删除本期导航，保留其他资产及独立存储的历史请求快照。"""
    for name in ("style_guide_ranges", "style_guide_entries", "style_guides"):
        op.drop_table(name)
