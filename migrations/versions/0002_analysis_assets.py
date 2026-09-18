"""增加标签、原文标注与事务内请求恢复；不改动既有原文字节或坐标。"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """固定本期结构，不依赖后续运行时模型；关联和请求结果随资产一起提交。"""
    op.execute("""
        CREATE TABLE tags (
            id uuid PRIMARY KEY, namespace varchar(64) COLLATE "C" NOT NULL,
            name varchar(256) COLLATE "C" NOT NULL, description text NOT NULL,
            aliases jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_tags_name UNIQUE (namespace, name)
        );
        COMMENT ON TABLE tags IS '跨作品共享的标签词表；名称精确唯一，别名允许跨标签重复';
        CREATE INDEX ix_tags_created_id ON tags (created_at, id);
        CREATE TABLE annotations (
            id uuid PRIMARY KEY, work_id uuid NOT NULL REFERENCES works(id), note text,
            version integer NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_annotations_version CHECK (version > 0)
        );
        COMMENT ON TABLE annotations IS
            '原文入口及可选写法说明；版本条件防止并发覆盖，不代表分析进度';
        CREATE INDEX ix_annotations_work_created_id ON annotations (work_id, created_at, id);
        CREATE TABLE annotation_ranges (
            annotation_id uuid NOT NULL REFERENCES annotations(id), ordinal integer NOT NULL,
            work_id uuid NOT NULL REFERENCES works(id),
            section_id uuid NOT NULL REFERENCES sections(id),
            start_paragraph_id uuid NOT NULL REFERENCES paragraphs(id),
            end_paragraph_id uuid NOT NULL REFERENCES paragraphs(id),
            PRIMARY KEY (annotation_id, ordinal),
            CONSTRAINT uq_annotation_ranges_ref UNIQUE
                (annotation_id, work_id, section_id, start_paragraph_id, end_paragraph_id),
            CONSTRAINT ck_annotation_ranges_ordinal CHECK (ordinal > 0)
        );
        COMMENT ON TABLE annotation_ranges IS
            '有序原文范围；不复制正文，事务内核验作品归属及端点顺序';
        CREATE INDEX ix_annotation_ranges_section ON annotation_ranges (section_id);
        CREATE TABLE annotation_tags (
            annotation_id uuid NOT NULL REFERENCES annotations(id),
            tag_id uuid NOT NULL REFERENCES tags(id), PRIMARY KEY (annotation_id, tag_id)
        );
        COMMENT ON TABLE annotation_tags IS '标注标签集合；复合主键拒绝重复关联';
        CREATE INDEX ix_annotation_tags_tag ON annotation_tags (tag_id);
        CREATE TABLE asset_write_requests (
            request_id uuid PRIMARY KEY, fingerprint varchar(64) NOT NULL, response jsonb NOT NULL
        );
        COMMENT ON TABLE asset_write_requests IS
            '资产写入的共享请求键与结果；同键并发由唯一约束串行裁决';
        COMMENT ON COLUMN asset_write_requests.response IS
            '提交时完整结果快照；事务内先占请求键再填写，失败整体回滚';
    """)


def downgrade() -> None:
    """仅供明确授权的回退使用；删除本期分析资产，保留原文表。"""
    for name in (
        "asset_write_requests",
        "annotation_tags",
        "annotation_ranges",
        "annotations",
        "tags",
    ):
        op.drop_table(name)
