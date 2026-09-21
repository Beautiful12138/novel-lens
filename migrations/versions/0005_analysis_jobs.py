"""新增任务、固定目标及稀疏进度；不修改原文或历史资产请求。"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """冻结本次 DDL；外键保证引用存在，版本和状态约束保护任务记录。"""
    op.execute("""
        CREATE TABLE analysis_jobs (
            id uuid PRIMARY KEY, work_id uuid NOT NULL REFERENCES works(id),
            title varchar(256) NOT NULL, goal text NOT NULL, target_kind varchar(16) NOT NULL,
            status varchar(16) NOT NULL, recovery jsonb NOT NULL, completion jsonb,
            version integer NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_analysis_jobs_version CHECK (version > 0),
            CONSTRAINT ck_analysis_jobs_target CHECK (target_kind IN ('whole_work', 'ranges')),
            CONSTRAINT ck_analysis_jobs_status CHECK (status IN ('running', 'paused', 'completed')),
            CONSTRAINT ck_analysis_jobs_completion
                CHECK ((status = 'completed') = (completion IS NOT NULL))
        );
        COMMENT ON TABLE analysis_jobs IS
            '独立深读任务；接续信息不替代进度，完成说明记录当时导航版本';
        CREATE INDEX ix_analysis_jobs_work_created_id ON analysis_jobs (work_id, created_at, id);
        CREATE TABLE analysis_targets (
            job_id uuid NOT NULL REFERENCES analysis_jobs(id), ordinal integer NOT NULL,
            section_id uuid NOT NULL REFERENCES sections(id),
            start_paragraph_id uuid NOT NULL REFERENCES paragraphs(id),
            end_paragraph_id uuid NOT NULL REFERENCES paragraphs(id),
            start_ordinal integer NOT NULL, end_ordinal integer NOT NULL,
            PRIMARY KEY (job_id, ordinal),
            CONSTRAINT ck_analysis_targets_order CHECK
                (ordinal > 0 AND start_ordinal > 0 AND end_ordinal >= start_ordinal),
            CONSTRAINT uq_analysis_targets_start UNIQUE (job_id, section_id, start_ordinal)
        );
        COMMENT ON TABLE analysis_targets IS
            '固定且已合并的任务范围；端点序号来自不可变原文，事务内核验归属及不重叠';
        CREATE TABLE analysis_coverage (
            job_id uuid NOT NULL REFERENCES analysis_jobs(id),
            paragraph_id uuid NOT NULL REFERENCES paragraphs(id),
            status varchar(16) NOT NULL, reason text, PRIMARY KEY (job_id, paragraph_id),
            CONSTRAINT ck_analysis_coverage_status
                CHECK (status IN ('read', 'processed', 'needs_revisit')),
            CONSTRAINT ck_analysis_coverage_reason
                CHECK ((status = 'needs_revisit') = (reason IS NOT NULL))
        );
        COMMENT ON TABLE analysis_coverage IS
            '任务内稀疏段落进度；缺省为未处理，归属和目标包含关系由事务校验';
    """)


def downgrade() -> None:
    """显式回退删除任务进度，保留原文、分析资产及独立请求快照。"""
    for name in ("analysis_coverage", "analysis_targets", "analysis_jobs"):
        op.drop_table(name)
