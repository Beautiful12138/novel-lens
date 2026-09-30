"""准备任务创建的标签归属；用于作品清理时保留其他资产使用的共享标签。"""

from sqlalchemy import Column, ForeignKey, MetaData, Table, Uuid


def register_preparation_tables(metadata: MetaData) -> None:
    """标签和作品删除时回收归属关系，不自动删除共享标签。"""
    Table(
        "preparation_tags",
        metadata,
        Column("work_id", Uuid, ForeignKey("works.id", ondelete="CASCADE"), primary_key=True),
        Column("tag_id", Uuid, ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
        comment="准备批次新建标签的归属记录；清理仅移除未被其他资产引用的新建标签",
    )
