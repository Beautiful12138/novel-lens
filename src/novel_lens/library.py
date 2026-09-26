"""默认业务工具的目录与原文入口；复用现有分页、坐标和资产规则。"""

from pydantic import BaseModel
from sqlalchemy import select

from novel_lens.analysis import AnalysisService
from novel_lens.asset_contracts import (
    MAX_ASSET_RESULT_BYTES,
    AnalysisJobGet,
    AnalysisJobList,
    AnnotationGet,
    AnnotationList,
)
from novel_lens.assets import AssetService, range_bounds
from novel_lens.contracts import (
    CompactParagraphPage,
    CompactReadOut,
    Page,
    ParagraphSpan,
    ReadOut,
    ReadRequest,
    SourceRange,
)
from novel_lens.database import Database
from novel_lens.embedding import EmbeddingClient
from novel_lens.errors import ServiceError
from novel_lens.library_views import (
    CompactJob,
    CompactLibraryPage,
    CompactPart,
    CompactSection,
    CompactTag,
    FullSourcePage,
    NamedItem,
)
from novel_lens.query_views import annotation_list_view, annotation_view
from novel_lens.reading import ReadingService, searchable_work, section_at
from novel_lens.reference_contracts import LibraryBrowse, LibraryPage, SourceRead
from novel_lens.reference_index import index_state
from novel_lens.schema import paragraphs


class LibraryService:
    """目录仅返回当前请求的投影；原文分页保留完整自然段，不解释文学边界。"""

    def __init__(self, database: Database, model: EmbeddingClient) -> None:
        self.database = database
        self.model = model
        self.reading = ReadingService(database)

    def browse(self, request: LibraryBrowse) -> LibraryPage | CompactLibraryPage:
        """先执行目录查询再投影；full 保留诊断，compact 不附带整个索引统计。"""
        full = self._browse(request)
        if request.format == "full":
            return self._bounded(full)
        result = CompactLibraryPage(view=request.view)
        if full.work is not None:
            result.work = NamedItem.model_validate(full.work.model_dump())
        if full.works is not None:
            result.works = Page[NamedItem](
                items=[NamedItem.model_validate(v.model_dump()) for v in full.works.items],
                next_cursor=full.works.next_cursor,
            )
        if full.parts is not None:
            result.parts = Page[CompactPart](
                items=[CompactPart.model_validate(v.model_dump()) for v in full.parts.items],
                next_cursor=full.parts.next_cursor,
            )
        if full.sections is not None:
            result.sections = Page[CompactSection](
                items=[CompactSection.model_validate(v.model_dump()) for v in full.sections.items],
                next_cursor=full.sections.next_cursor,
            )
        if full.annotations is not None:
            assert request.work_id is not None
            result.annotations = annotation_list_view(full.annotations, request.work_id, "compact")  # type: ignore[assignment]
        if full.annotation is not None:
            result.annotation = annotation_view(full.annotation, "compact")  # type: ignore[assignment]
            result.tags = [CompactTag.model_validate(tag.model_dump()) for tag in full.tags or []]
        if full.jobs is not None:
            assert request.work_id is not None
            service = AnalysisService(self.database)
            result.jobs = Page[CompactJob](
                items=[
                    CompactJob.model_validate(
                        job.model_dump()
                        | {
                            "target": service.get(
                                AnalysisJobGet(work_id=request.work_id, job_id=job.id)
                            ).target
                        }
                    )
                    for job in full.jobs.items
                ],
                next_cursor=full.jobs.next_cursor,
            )
        return self._bounded(result)

    def _browse(self, request: LibraryBrowse) -> LibraryPage:
        if request.view == "works":
            return LibraryPage(
                view=request.view, works=self.reading.list_works(request.limit, request.cursor)
            )
        assert request.work_id is not None
        with self.database.engine.connect() as conn:
            work = searchable_work(conn, request.work_id)
            indexes = (
                index_state(conn, request.work_id, self.model.contract_id)
                if request.format == "full"
                else None
            )
        result = LibraryPage(view=request.view, work=work, indexes=indexes)
        match request.view:
            case "parts":
                result.parts = self.reading.list_parts(
                    request.work_id, request.limit, request.cursor
                )
            case "sections":
                result.sections = self.reading.list_sections(
                    request.work_id, request.limit, request.cursor, request.part_id
                )
            case "annotations":
                assets = AssetService(self.database)
                if request.annotation_id is not None:
                    result.annotation = assets.get_annotation(
                        AnnotationGet(work_id=request.work_id, annotation_id=request.annotation_id)
                    )
                    result.tags = [
                        assets.get_tag(identifier) for identifier in result.annotation.tag_ids
                    ]
                else:
                    result.annotations = assets.list_annotations(
                        AnnotationList(
                            work_id=request.work_id,
                            limit=request.limit,
                            cursor=request.cursor,
                            status=None,
                        )
                    )
            case "jobs":
                result.jobs = AnalysisService(self.database).list(
                    AnalysisJobList(
                        work_id=request.work_id, limit=request.limit, cursor=request.cursor
                    )
                )
        return result

    @staticmethod
    def _bounded[T: BaseModel](result: T) -> T:
        """HTTP 与 MCP 采用相同结果上限，超出时明确报错而不截断正文。"""
        if len(result.model_dump_json().encode()) > MAX_ASSET_RESULT_BYTES:
            raise ServiceError("RESULT_TOO_LARGE", "结果超过 1 MiB，请减小分页数量", 413)
        return result

    def read(self, request: SourceRead) -> CompactParagraphPage | CompactReadOut | FullSourcePage:
        """范围扩展由真实段落序号裁决；续页游标仍绑定扩展后的完整请求范围。"""
        if request.start_paragraph_id is None:
            page = self.reading.list_paragraphs(
                request.work_id, request.section_id, request.limit, request.cursor, request.format
            )
            if request.format == "full":
                return self._bounded(
                    FullSourcePage.model_validate(
                        page.model_dump()
                        | {
                            "work_id": request.work_id,
                            "section_id": request.section_id,
                            "actual_range": {
                                "start_paragraph_id": page.items[0].id,
                                "end_paragraph_id": page.items[-1].id,
                            }
                            if page.items
                            else None,
                        }
                    )
                )
            assert isinstance(page, CompactParagraphPage)
            return self._bounded(page)
        assert request.end_paragraph_id is not None
        ref = SourceRange(
            work_id=request.work_id,
            section_id=request.section_id,
            start_paragraph_id=request.start_paragraph_id,
            end_paragraph_id=request.end_paragraph_id,
        )
        with self.database.engine.connect() as conn:
            start, end = range_bounds(conn, request.work_id, ref)
            section = section_at(conn, request.work_id, request.section_id)
            first = max(1, start - request.before)
            last = min(section.paragraph_count, end + request.after)
            ids = dict(
                conn.execute(
                    select(paragraphs.c.ordinal, paragraphs.c.id).where(
                        paragraphs.c.section_id == request.section_id,
                        paragraphs.c.ordinal.in_([first, last]),
                    )
                )
                .tuples()
                .all()
            )
        ref = ref.model_copy(
            update={"start_paragraph_id": ids[first], "end_paragraph_id": ids[last]}
        )
        result = self.reading.read(
            request.work_id,
            ReadRequest(
                source_range=ref, limit=request.limit, cursor=request.cursor, format=request.format
            ),
        )
        if isinstance(result, ReadOut):
            return self._bounded(
                FullSourcePage(
                    work_id=request.work_id,
                    section_id=request.section_id,
                    items=result.items,
                    actual_range=ParagraphSpan.model_validate(result.actual_range.model_dump()),
                    requested_range=ParagraphSpan.model_validate(
                        result.requested_range.model_dump()
                    ),
                    next_cursor=result.next_cursor,
                )
            )
        assert isinstance(result, CompactReadOut)
        return self._bounded(result)
