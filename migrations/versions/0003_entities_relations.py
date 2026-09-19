"""新增作品内实体、关系证据与可恢复撤回；保留旧请求和原文。"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """固定本次结构；不从运行时 metadata 导入，也不改写旧快照或请求指纹。"""
    op.execute("""
        CREATE TABLE entities (
            id uuid PRIMARY KEY, work_id uuid NOT NULL REFERENCES works(id),
            type varchar(32) NOT NULL, canonical_name varchar(256) NOT NULL,
            aliases jsonb NOT NULL, note text, version integer NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_entities_version CHECK (version > 0),
            CONSTRAINT ck_entities_type CHECK
                (type IN ('character', 'location', 'item', 'organization', 'concept'))
        );
        COMMENT ON TABLE entities IS
            '作品内稳定身份；名称和别名允许重复，关联保存 ID 而非名称副本';
        CREATE INDEX ix_entities_work_created_id ON entities (work_id, created_at, id);
        CREATE TABLE annotation_entities (
            annotation_id uuid NOT NULL REFERENCES annotations(id),
            entity_id uuid NOT NULL REFERENCES entities(id), PRIMARY KEY (annotation_id, entity_id)
        );
        COMMENT ON TABLE annotation_entities IS
            '标注实体集合；复合主键去重，事务内核验同作品归属';
        CREATE INDEX ix_annotation_entities_entity ON annotation_entities (entity_id);
        CREATE TABLE relations (
            id uuid PRIMARY KEY, work_id uuid NOT NULL REFERENCES works(id),
            title varchar(256) NOT NULL, relation_type varchar(64) NOT NULL, note text NOT NULL,
            status varchar(16) NOT NULL, version integer NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_relations_version CHECK (version > 0),
            CONSTRAINT ck_relations_status CHECK (status IN ('active', 'withdrawn'))
        );
        COMMENT ON TABLE relations IS
            '多节点原文关系；撤回保留内容，状态和内容修改竞争同一版本';
        CREATE INDEX ix_relations_work_created_id ON relations (work_id, created_at, id);
        CREATE TABLE relation_nodes (
            relation_id uuid NOT NULL REFERENCES relations(id), ordinal integer NOT NULL,
            work_id uuid NOT NULL REFERENCES works(id),
            section_id uuid NOT NULL REFERENCES sections(id),
            start_paragraph_id uuid NOT NULL REFERENCES paragraphs(id),
            end_paragraph_id uuid NOT NULL REFERENCES paragraphs(id), role varchar(64),
            PRIMARY KEY (relation_id, ordinal),
            CONSTRAINT uq_relation_nodes_ref UNIQUE
                (relation_id, work_id, section_id, start_paragraph_id, end_paragraph_id),
            CONSTRAINT ck_relation_nodes_ordinal CHECK (ordinal > 0)
        );
        COMMENT ON TABLE relation_nodes IS
            '有序证据引用，不复制正文；至少两处不同范围及合法归属由事务校验';
        CREATE INDEX ix_relation_nodes_section ON relation_nodes (section_id);
        CREATE TABLE relation_tags (
            relation_id uuid NOT NULL REFERENCES relations(id),
            tag_id uuid NOT NULL REFERENCES tags(id),
            PRIMARY KEY (relation_id, tag_id)
        );
        COMMENT ON TABLE relation_tags IS '关系共享标签集合；复合主键拒绝重复关联';
        CREATE INDEX ix_relation_tags_tag ON relation_tags (tag_id);
        CREATE TABLE relation_entities (
            relation_id uuid NOT NULL REFERENCES relations(id),
            entity_id uuid NOT NULL REFERENCES entities(id), PRIMARY KEY (relation_id, entity_id)
        );
        COMMENT ON TABLE relation_entities IS
            '关系实体集合；复合主键去重，事务内核验同作品归属';
        CREATE INDEX ix_relation_entities_entity ON relation_entities (entity_id);
    """)


def downgrade() -> None:
    """显式回退会删除本期实体和关系；旧标注、请求快照及原文保留。"""
    for name in (
        "relation_entities",
        "relation_tags",
        "relation_nodes",
        "relations",
        "annotation_entities",
        "entities",
    ):
        op.drop_table(name)
