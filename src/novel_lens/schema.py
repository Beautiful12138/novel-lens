"""数据库结构定义；原文写入使用单事务，查询只选择当前需要的字段。"""

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB

metadata = MetaData()

works = Table(
    "works",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("name", String(256, collation="C"), nullable=False),
    Column("request_id", Uuid, nullable=False),
    Column(
        "fingerprint",
        String(64),
        nullable=False,
        comment="导入协议和输入内容摘要；同键不同输入返回冲突",
    ),
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    Column("section_count", Integer, nullable=False),
    Column("paragraph_count", Integer, nullable=False),
    Column("character_count", Integer, nullable=False),
    Column("source_sha256", String(64), nullable=False),
    Column("source_bytes", Integer, nullable=False),
    UniqueConstraint("name", name="uq_works_name"),
    UniqueConstraint("request_id", name="uq_works_request_id"),
    CheckConstraint(
        "section_count > 0 AND paragraph_count > 0 AND character_count > 0", name="ck_works_counts"
    ),
    comment="作品与成功请求；名称唯一防止覆盖，请求键唯一防止重试重复创建",
)
Index("ix_works_created_id", works.c.created_at, works.c.id)

sources = Table(
    "work_sources",
    metadata,
    Column("work_id", Uuid, ForeignKey("works.id"), primary_key=True),
    Column("content", LargeBinary, nullable=False),
    Column("rule_version", String(32), nullable=False),
    Column("layout", JSONB, nullable=False),
    comment="完整输入字节及核验依据，与作品和所有段落同事务保存",
)

sections = Table(
    "sections",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("work_id", Uuid, ForeignKey("works.id"), nullable=False),
    Column("ordinal", Integer, nullable=False),
    Column("title", Text, nullable=False),
    Column("paragraph_count", Integer, nullable=False),
    UniqueConstraint("work_id", "ordinal", name="uq_sections_order"),
    CheckConstraint("ordinal > 0 AND paragraph_count >= 0", name="ck_sections_counts"),
    comment="平铺目录；作品内顺序唯一，标题可重复，允许空结构单元",
)

paragraphs = Table(
    "paragraphs",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("section_id", Uuid, ForeignKey("sections.id"), nullable=False),
    Column("ordinal", Integer, nullable=False),
    Column("text", Text, nullable=False),
    Column(
        "source_position",
        JSONB,
        nullable=False,
        comment="上传文件字节半开区间及一基行号，不含行终止符",
    ),
    UniqueConstraint("section_id", "ordinal", name="uq_paragraphs_order"),
    CheckConstraint("ordinal > 0 AND length(text) > 0", name="ck_paragraphs_content"),
    comment="保真自然段及稳定 ID；Section 内顺序唯一，不按文学场景切分",
)

Index(
    "ix_paragraphs_text_search",
    paragraphs.c.text,
    postgresql_using="pgroonga",
    postgresql_with={"tokenizer": "'TokenBigram'", "normalizers": "'NormalizerAuto'"},
)

tags = Table(
    "tags",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("namespace", String(64, collation="C"), nullable=False),
    Column("name", String(256, collation="C"), nullable=False),
    Column("description", Text, nullable=False),
    Column("aliases", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    UniqueConstraint("namespace", "name", name="uq_tags_name"),
    comment="跨作品共享的标签词表；名称精确唯一，别名允许跨标签重复",
)
Index("ix_tags_created_id", tags.c.created_at, tags.c.id)

annotations = Table(
    "annotations",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("work_id", Uuid, ForeignKey("works.id"), nullable=False),
    Column("note", Text),
    Column("version", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    Column("updated_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    CheckConstraint("version > 0", name="ck_annotations_version"),
    comment="原文入口及可选写法说明；版本条件防止并发覆盖，不代表分析进度",
)
Index(
    "ix_annotations_work_created_id",
    annotations.c.work_id,
    annotations.c.created_at,
    annotations.c.id,
)

Index(
    "ix_annotations_note_search",
    annotations.c.note,
    postgresql_using="pgroonga",
    postgresql_with={"tokenizer": "'TokenBigram'", "normalizers": "'NormalizerAuto'"},
)

annotation_ranges = Table(
    "annotation_ranges",
    metadata,
    Column("annotation_id", Uuid, ForeignKey("annotations.id"), primary_key=True),
    Column("ordinal", Integer, primary_key=True),
    Column("work_id", Uuid, ForeignKey("works.id"), nullable=False),
    Column("section_id", Uuid, ForeignKey("sections.id"), nullable=False),
    Column("start_paragraph_id", Uuid, ForeignKey("paragraphs.id"), nullable=False),
    Column("end_paragraph_id", Uuid, ForeignKey("paragraphs.id"), nullable=False),
    UniqueConstraint(
        "annotation_id",
        "work_id",
        "section_id",
        "start_paragraph_id",
        "end_paragraph_id",
        name="uq_annotation_ranges_ref",
    ),
    CheckConstraint("ordinal > 0", name="ck_annotation_ranges_ordinal"),
    comment="有序原文范围；不复制正文，事务内核验作品归属及端点顺序",
)
Index("ix_annotation_ranges_section", annotation_ranges.c.section_id)

annotation_tags = Table(
    "annotation_tags",
    metadata,
    Column("annotation_id", Uuid, ForeignKey("annotations.id"), primary_key=True),
    Column("tag_id", Uuid, ForeignKey("tags.id"), primary_key=True),
    comment="标注标签集合；复合主键拒绝重复关联",
)
Index("ix_annotation_tags_tag", annotation_tags.c.tag_id)

asset_write_requests = Table(
    "asset_write_requests",
    metadata,
    Column("request_id", Uuid, primary_key=True),
    Column("fingerprint", String(64), nullable=False),
    Column(
        "response",
        JSONB,
        nullable=False,
        comment="提交时完整结果快照；事务内先占请求键再填写，失败整体回滚",
    ),
    comment="资产写入的共享请求键与结果；同键并发由唯一约束串行裁决",
)

entities = Table(
    "entities",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("work_id", Uuid, ForeignKey("works.id"), nullable=False),
    Column("type", String(32), nullable=False),
    Column("canonical_name", String(256), nullable=False),
    Column("aliases", JSONB, nullable=False),
    Column("note", Text),
    Column("version", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    Column("updated_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    CheckConstraint("version > 0", name="ck_entities_version"),
    CheckConstraint(
        "type IN ('character', 'location', 'item', 'organization', 'concept')",
        name="ck_entities_type",
    ),
    comment="作品内稳定身份；名称和别名允许重复，关联保存 ID 而非名称副本",
)
Index("ix_entities_work_created_id", entities.c.work_id, entities.c.created_at, entities.c.id)

annotation_entities = Table(
    "annotation_entities",
    metadata,
    Column("annotation_id", Uuid, ForeignKey("annotations.id"), primary_key=True),
    Column("entity_id", Uuid, ForeignKey("entities.id"), primary_key=True),
    comment="标注实体集合；复合主键去重，事务内核验同作品归属",
)
Index("ix_annotation_entities_entity", annotation_entities.c.entity_id)

relations = Table(
    "relations",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("work_id", Uuid, ForeignKey("works.id"), nullable=False),
    Column("title", String(256), nullable=False),
    Column("relation_type", String(64), nullable=False),
    Column("note", Text, nullable=False),
    Column("status", String(16), nullable=False),
    Column("version", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    Column("updated_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    CheckConstraint("version > 0", name="ck_relations_version"),
    CheckConstraint("status IN ('active', 'withdrawn')", name="ck_relations_status"),
    comment="多节点原文关系；撤回保留内容，状态和内容修改竞争同一版本",
)
Index("ix_relations_work_created_id", relations.c.work_id, relations.c.created_at, relations.c.id)

relation_nodes = Table(
    "relation_nodes",
    metadata,
    Column("relation_id", Uuid, ForeignKey("relations.id"), primary_key=True),
    Column("ordinal", Integer, primary_key=True),
    Column("work_id", Uuid, ForeignKey("works.id"), nullable=False),
    Column("section_id", Uuid, ForeignKey("sections.id"), nullable=False),
    Column("start_paragraph_id", Uuid, ForeignKey("paragraphs.id"), nullable=False),
    Column("end_paragraph_id", Uuid, ForeignKey("paragraphs.id"), nullable=False),
    Column("role", String(64)),
    UniqueConstraint(
        "relation_id",
        "work_id",
        "section_id",
        "start_paragraph_id",
        "end_paragraph_id",
        name="uq_relation_nodes_ref",
    ),
    CheckConstraint("ordinal > 0", name="ck_relation_nodes_ordinal"),
    comment="有序证据引用，不复制正文；至少两处不同范围及合法归属由事务校验",
)
Index("ix_relation_nodes_section", relation_nodes.c.section_id)

relation_tags = Table(
    "relation_tags",
    metadata,
    Column("relation_id", Uuid, ForeignKey("relations.id"), primary_key=True),
    Column("tag_id", Uuid, ForeignKey("tags.id"), primary_key=True),
    comment="关系共享标签集合；复合主键拒绝重复关联",
)
Index("ix_relation_tags_tag", relation_tags.c.tag_id)

relation_entities = Table(
    "relation_entities",
    metadata,
    Column("relation_id", Uuid, ForeignKey("relations.id"), primary_key=True),
    Column("entity_id", Uuid, ForeignKey("entities.id"), primary_key=True),
    comment="关系实体集合；复合主键去重，事务内核验同作品归属",
)
Index("ix_relation_entities_entity", relation_entities.c.entity_id)

style_guides = Table(
    "style_guides",
    metadata,
    Column("work_id", Uuid, ForeignKey("works.id"), primary_key=True),
    Column("scope_note", Text, nullable=False),
    Column("version", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    Column("updated_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    CheckConstraint("version > 0", name="ck_style_guides_version"),
    comment="每作品一份风格导航；范围说明是调用方声明，不代表分析进度",
)

style_guide_entries = Table(
    "style_guide_entries",
    metadata,
    Column("work_id", Uuid, ForeignKey("style_guides.work_id"), primary_key=True),
    Column("ordinal", Integer, primary_key=True),
    Column("title", String(256), nullable=False),
    Column("kind", String(16), nullable=False),
    Column("description", Text, nullable=False),
    Column("applicability", Text, nullable=False),
    CheckConstraint("ordinal > 0", name="ck_style_guide_entries_ordinal"),
    CheckConstraint(
        "kind IN ('baseline', 'variation', 'exception')", name="ck_style_guide_entries_kind"
    ),
    comment="有序写法判断及适用边界；整体修订时替换，顺序不是稳定身份",
)

style_guide_ranges = Table(
    "style_guide_ranges",
    metadata,
    Column("work_id", Uuid, primary_key=True),
    Column("entry_ordinal", Integer, primary_key=True),
    Column("ordinal", Integer, primary_key=True),
    Column("section_id", Uuid, ForeignKey("sections.id"), nullable=False),
    Column("start_paragraph_id", Uuid, ForeignKey("paragraphs.id"), nullable=False),
    Column("end_paragraph_id", Uuid, ForeignKey("paragraphs.id"), nullable=False),
    ForeignKeyConstraint(
        ["work_id", "entry_ordinal"],
        ["style_guide_entries.work_id", "style_guide_entries.ordinal"],
    ),
    UniqueConstraint(
        "work_id",
        "entry_ordinal",
        "section_id",
        "start_paragraph_id",
        "end_paragraph_id",
        name="uq_style_guide_ranges_ref",
    ),
    CheckConstraint("ordinal > 0", name="ck_style_guide_ranges_ordinal"),
    comment="条目有序证据；事务内核验同作品、同章节及端点顺序，不复制原文",
)

analysis_jobs = Table(
    "analysis_jobs",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("work_id", Uuid, ForeignKey("works.id"), nullable=False),
    Column("title", String(256), nullable=False),
    Column("goal", Text, nullable=False),
    Column("target_kind", String(16), nullable=False),
    Column("status", String(16), nullable=False),
    Column("recovery", JSONB, nullable=False),
    Column("completion", JSONB(none_as_null=True)),
    Column("version", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    Column("updated_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    CheckConstraint("version > 0", name="ck_analysis_jobs_version"),
    CheckConstraint("target_kind IN ('whole_work', 'ranges')", name="ck_analysis_jobs_target"),
    CheckConstraint("status IN ('running', 'paused', 'completed')", name="ck_analysis_jobs_status"),
    CheckConstraint(
        "(status = 'completed') = (completion IS NOT NULL)", name="ck_analysis_jobs_completion"
    ),
    comment="独立深读任务；接续信息不替代进度，完成说明记录当时导航版本",
)
Index(
    "ix_analysis_jobs_work_created_id",
    analysis_jobs.c.work_id,
    analysis_jobs.c.created_at,
    analysis_jobs.c.id,
)

analysis_targets = Table(
    "analysis_targets",
    metadata,
    Column("job_id", Uuid, ForeignKey("analysis_jobs.id"), primary_key=True),
    Column("ordinal", Integer, primary_key=True),
    Column("section_id", Uuid, ForeignKey("sections.id"), nullable=False),
    Column("start_paragraph_id", Uuid, ForeignKey("paragraphs.id"), nullable=False),
    Column("end_paragraph_id", Uuid, ForeignKey("paragraphs.id"), nullable=False),
    Column("start_ordinal", Integer, nullable=False),
    Column("end_ordinal", Integer, nullable=False),
    CheckConstraint(
        "ordinal > 0 AND start_ordinal > 0 AND end_ordinal >= start_ordinal",
        name="ck_analysis_targets_order",
    ),
    UniqueConstraint("job_id", "section_id", "start_ordinal", name="uq_analysis_targets_start"),
    comment="固定且已合并的任务范围；端点序号来自不可变原文，事务内核验归属及不重叠",
)

analysis_coverage = Table(
    "analysis_coverage",
    metadata,
    Column("job_id", Uuid, ForeignKey("analysis_jobs.id"), primary_key=True),
    Column("paragraph_id", Uuid, ForeignKey("paragraphs.id"), primary_key=True),
    Column("status", String(16), nullable=False),
    Column("reason", Text),
    CheckConstraint(
        "status IN ('read', 'processed', 'needs_revisit')", name="ck_analysis_coverage_status"
    ),
    CheckConstraint(
        "(status = 'needs_revisit') = (reason IS NOT NULL)", name="ck_analysis_coverage_reason"
    ),
    comment="任务内稀疏段落进度；缺省为未处理，归属和目标包含关系由事务校验",
)
