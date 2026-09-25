"""供 HTTP 管理端使用的作品生命周期操作，不修改原文内容。"""

from uuid import UUID

from sqlalchemy import select, text

from novel_lens.contracts import WorkOut, WorkVisibility
from novel_lens.database import Database
from novel_lens.errors import ServiceError
from novel_lens.schema import works
from novel_lens.semantic import lock_key

# 子表先于父表。所有标识符均为固定代码，只有作品 UUID 作为绑定参数传入。
# 新增作品所属表时须同步此清理范围及完整性测试；共享标签不属于作品。
DELETE_SCOPE = (
    ("semantic_index_heads", "work_id=:work"),
    (
        "semantic_build_receipts",
        "index_id IN (SELECT id FROM semantic_indexes WHERE work_id=:work)",
    ),
    ("semantic_index_items", "work_id=:work"),
    ("semantic_annotation_snapshots", "work_id=:work"),
    ("semantic_indexes", "work_id=:work"),
    ("analysis_coverage", "job_id IN (SELECT id FROM analysis_jobs WHERE work_id=:work)"),
    ("analysis_targets", "job_id IN (SELECT id FROM analysis_jobs WHERE work_id=:work)"),
    ("analysis_jobs", "work_id=:work"),
    ("style_guide_ranges", "work_id=:work"),
    ("style_guide_entries", "work_id=:work"),
    ("style_guides", "work_id=:work"),
    ("relation_entities", "relation_id IN (SELECT id FROM relations WHERE work_id=:work)"),
    ("relation_tags", "relation_id IN (SELECT id FROM relations WHERE work_id=:work)"),
    ("relation_nodes", "work_id=:work"),
    ("relations", "work_id=:work"),
    ("annotation_entities", "annotation_id IN (SELECT id FROM annotations WHERE work_id=:work)"),
    ("annotation_tags", "annotation_id IN (SELECT id FROM annotations WHERE work_id=:work)"),
    ("annotation_ranges", "work_id=:work"),
    ("annotations", "work_id=:work"),
    ("entities", "work_id=:work"),
    ("asset_write_requests", "response->'result'->>'work_id'=:work_text"),
    ("paragraphs", "section_id IN (SELECT id FROM sections WHERE work_id=:work)"),
    ("sections", "work_id=:work"),
    ("part_sources", "part_id IN (SELECT id FROM parts WHERE work_id=:work)"),
    ("parts", "work_id=:work"),
    ("catalog_requests", "work_id=:work"),
    ("works", "id=:work"),
)


class WorkManagementService:
    """状态设置幂等；物理删除在一个事务内清除作品及其所有派生数据。"""

    def __init__(self, database: Database) -> None:
        self.database = database

    def set_visibility(self, work_id: UUID, visibility: WorkVisibility) -> WorkOut:
        """串行执行绝对状态赋值；未知作品返回 404，不创建占位记录。"""
        with self.database.engine.begin() as connection:
            row = (
                connection.execute(
                    works.update()
                    .where(works.c.id == work_id)
                    .values(visibility=visibility)
                    .returning(works)
                )
                .mappings()
                .first()
            )
            if row is None:
                raise ServiceError("WORK_NOT_FOUND", "作品不存在", 404)
            return WorkOut.model_validate(row)

    def delete(self, work_id: UUID) -> None:
        """先排除跨事务模型构建，再锁作品；失败回滚，已不存在视为目标状态达成。

        资产写入持作品 KEY SHARE 锁直到业务与回执一起提交，故删除看见完整结果。
        不锁全库业务表，不删除共享标签、本地文件或外部备份。
        """
        with self.database.engine.begin() as connection:
            for kind in ("fulltext", "annotation"):
                acquired = connection.execute(
                    text("SELECT pg_try_advisory_xact_lock(:key)"),
                    {"key": lock_key(work_id, kind)},
                ).scalar_one()
                if not acquired:
                    raise ServiceError("WORK_BUSY", "作品索引正在创建或构建，请稍后删除", 409)
            row = connection.execute(
                select(works.c.id).where(works.c.id == work_id).with_for_update()
            ).first()
            if row is None:
                return
            for table, condition in DELETE_SCOPE:
                connection.execute(
                    text(f"DELETE FROM {table} WHERE {condition}"),
                    {"work": work_id, "work_text": str(work_id)},
                )
