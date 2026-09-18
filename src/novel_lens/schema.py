"""数据库结构定义；原文写入使用单事务，查询只选择当前需要的字段。"""

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
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
