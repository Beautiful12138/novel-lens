"""HTTP 应用定义，与进程启动和配置读取分离。"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Literal
from uuid import UUID

from fastapi import FastAPI, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from mcp.server.transport_security import TransportSecuritySettings
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse

from novel_lens.assets import AssetService
from novel_lens.catalog import CatalogService
from novel_lens.config import Settings
from novel_lens.contracts import (
    CatalogWrite,
    CompactContextOut,
    CompactParagraphPage,
    CompactReadOut,
    ContextOut,
    ContextRequest,
    ImportOut,
    Limit,
    Page,
    ParagraphOut,
    PartOut,
    PartUpdate,
    ReadingFormat,
    ReadOut,
    ReadRequest,
    SectionOut,
    ValidationReport,
    WorkCreate,
    WorkFilter,
    WorkOut,
    WorkUpdate,
    WorkVisibilityUpdate,
)
from novel_lens.database import Database
from novel_lens.embedding import EmbeddingClient
from novel_lens.errors import ServiceError
from novel_lens.errors import database_error as public_database_error
from novel_lens.http import RequestSizeLimit, upload, upload_schema
from novel_lens.importing import ImportService
from novel_lens.mcp_api import create_mcp
from novel_lens.query_views import (
    CompactAnnotationResults,
    CompactSemanticResults,
    CompactSourceResults,
    annotation_search_view,
    semantic_search_view,
    source_search_view,
)
from novel_lens.reading import ReadingService
from novel_lens.search import SearchService
from novel_lens.search_contracts import (
    AnnotationSearchHit,
    AnnotationSearchRequest,
    SearchRequest,
    SourceSearchHit,
)
from novel_lens.semantic import SemanticService
from novel_lens.semantic_contracts import (
    SemanticBuild,
    SemanticCreate,
    SemanticGet,
    SemanticResults,
    SemanticSearch,
    SemanticStatus,
    SemanticWrite,
)
from novel_lens.work_management import WorkManagementService

PageLimit = Annotated[Limit, Query()]


def create_app(settings: Settings | None = None) -> FastAPI:
    """显式注入配置；缺省采用默认值，不从调用者环境隐式读取连接信息。"""
    settings = settings if settings is not None else Settings.model_construct()
    database = Database(settings)
    catalog = CatalogService(database)
    importing = ImportService(database, settings.max_file_bytes)
    reading = ReadingService(database)
    management = WorkManagementService(database)
    assets = AssetService(database)
    search = SearchService(database)
    semantic = SemanticService(database, EmbeddingClient(settings.embedding_config))
    mcp = create_mcp(settings, importing, reading, assets, semantic)
    # 只允许当前监听端口；不沿用 SDK 默认允许任意本机端口的通配配置。
    names = {settings.host, "localhost"}
    if settings.host == "localhost":
        names.update({"127.0.0.1", "::1"})
    hosts = [
        f"[{name}]:{settings.port}" if ":" in name else f"{name}:{settings.port}"
        for name in sorted(names)
    ]
    if settings.port == 80:
        hosts.extend(f"[{name}]" if ":" in name else name for name in sorted(names))
    mcp_app = mcp.streamable_http_app(
        json_response=True,
        stateless_http=True,
        max_request_body_size=settings.max_request_bytes,
        transport_security=TransportSecuritySettings(
            allowed_hosts=hosts, allowed_origins=[f"http://{host}" for host in hosts]
        ),
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            async with mcp.session_manager.run():
                yield
        finally:
            await run_in_threadpool(database.close)

    app = FastAPI(title="NovelLens", lifespan=lifespan)
    app.add_middleware(RequestSizeLimit, maximum=settings.max_request_bytes)

    @app.exception_handler(ServiceError)
    async def service_error(request: Request, exc: ServiceError) -> JSONResponse:
        return JSONResponse(exc.payload(), status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def input_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Pydantic 的 input、上下文和未知字段名可能含正文，不直接序列化。
        return JSONResponse(
            ServiceError("INVALID_INPUT", "输入参数或 JSON 结构无效").payload(), status_code=422
        )

    @app.exception_handler(HTTPException)
    async def transport_error(request: Request, exc: HTTPException) -> JSONResponse:
        if exc.status_code == 413:
            error = ServiceError("REQUEST_TOO_LARGE", "请求体超过允许大小", 413)
        elif exc.status_code == 400:
            error = ServiceError("INVALID_INPUT", "HTTP 请求格式无效", 422)
        else:
            error = ServiceError("HTTP_ERROR", "HTTP 请求无法处理", exc.status_code)
        return JSONResponse(error.payload(), status_code=error.status, headers=exc.headers)

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request: Request, exc: SQLAlchemyError) -> JSONResponse:
        # 不记录驱动异常文本；其中可能含 SQL、参数或服务器返回的正文片段。
        error = public_database_error(exc)
        return JSONResponse(error.payload(), status_code=error.status)

    @app.get("/health", summary="检查 HTTP 进程存活")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post(
        "/works/{work_id}/part-imports/validate",
        summary="预检结构已确认的 TXT",
        openapi_extra=upload_schema(importing=False),
    )
    async def validate(work_id: UUID, request: Request) -> ValidationReport:
        data, _ = await upload(request, settings.max_file_bytes, importing=False)
        return await run_in_threadpool(importing.validate, data, work_id)

    @app.post(
        "/works/{work_id}/part-imports",
        summary="原子追加一个完整分部",
        status_code=201,
        openapi_extra=upload_schema(importing=True),
    )
    async def import_source(work_id: UUID, request: Request, response: Response) -> ImportOut:
        data, request_id = await upload(request, settings.max_file_bytes, importing=True)
        assert request_id is not None  # upload 已校验正式导入必需的请求键。
        result = await run_in_threadpool(importing.import_source, data, request_id, work_id)
        response.status_code = 200 if result.replayed else 201
        return result

    @app.get("/part-imports/{request_id}", summary="查询已提交请求")
    def import_result(request_id: UUID) -> ImportOut:
        return importing.result(request_id)

    @app.post("/works", summary="创建空作品", status_code=201)
    def create_work(request: WorkCreate, response: Response) -> CatalogWrite:
        result = catalog.create(request)
        response.status_code = 200 if result.replayed else 201
        return result

    @app.patch("/works/{work_id}", summary="修改作品名称")
    def update_work(work_id: UUID, request: WorkUpdate) -> CatalogWrite:
        if work_id != request.work_id:
            raise ServiceError("INVALID_INPUT", "路径作品与请求不一致")
        return catalog.update(request)

    @app.get("/works/{work_id}/parts", summary="分页列出分部")
    def list_parts(
        work_id: UUID, limit: PageLimit = 100, cursor: str | None = None
    ) -> Page[PartOut]:
        return reading.list_parts(work_id, limit, cursor)

    @app.get("/works/{work_id}/parts/{part_id}", summary="读取分部元数据")
    def get_part(work_id: UUID, part_id: UUID) -> PartOut:
        return reading.get_part(work_id, part_id)

    @app.patch("/works/{work_id}/parts/{part_id}", summary="修改分部名称")
    def update_part(work_id: UUID, part_id: UUID, request: PartUpdate) -> CatalogWrite:
        if work_id != request.work_id or part_id != request.part_id:
            raise ServiceError("INVALID_INPUT", "路径归属与请求不一致")
        return catalog.update(request)

    @app.get("/works", summary="分页列出作品")
    def list_works(
        limit: PageLimit = 100, cursor: str | None = None, visibility: WorkFilter = "visible"
    ) -> Page[WorkOut]:
        return reading.list_works(limit, cursor, visibility)

    @app.patch("/works/{work_id}/visibility", summary="屏蔽作品或恢复检索可见性")
    def set_visibility(work_id: UUID, request: WorkVisibilityUpdate) -> WorkOut:
        return management.set_visibility(work_id, request.visibility)

    @app.delete("/works/{work_id}", status_code=204, summary="物理删除作品及全部作品内数据")
    def delete_work(work_id: UUID) -> Response:
        management.delete(work_id)
        return Response(status_code=204)

    @app.get("/works/{work_id}", summary="读取作品元数据")
    def get_work(work_id: UUID) -> WorkOut:
        return reading.get_work(work_id)

    @app.get("/works/{work_id}/sections", summary="分页读取目录")
    def list_sections(
        work_id: UUID,
        limit: PageLimit = 100,
        cursor: str | None = None,
        part_id: UUID | None = None,
    ) -> Page[SectionOut]:
        return reading.list_sections(work_id, limit, cursor, part_id)

    @app.get("/works/{work_id}/sections/{section_id}/paragraphs", summary="分页读取完整自然段")
    def list_paragraphs(
        work_id: UUID,
        section_id: UUID,
        limit: PageLimit = 100,
        cursor: str | None = None,
        format: ReadingFormat = "compact",
    ) -> Page[ParagraphOut] | CompactParagraphPage:
        return reading.list_paragraphs(work_id, section_id, limit, cursor, format)

    @app.post("/works/{work_id}/source/read", summary="读取段落范围")
    def read(work_id: UUID, request: ReadRequest) -> ReadOut | CompactReadOut:
        return reading.read(work_id, request)

    @app.post("/works/{work_id}/source/context", summary="补读同 Section 上下文")
    def context(work_id: UUID, request: ContextRequest) -> ContextOut | CompactContextOut:
        return reading.context(work_id, request)

    @app.get(
        "/works/{work_id}/parts/{part_id}/file", summary="下载原上传字节", response_class=Response
    )
    def file(work_id: UUID, part_id: UUID) -> Response:
        return Response(
            reading.file(work_id, part_id),
            media_type="application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{part_id}.txt"'},
        )

    @app.post("/source/search", summary="在指定作品内检索原文关键词")
    def source_search(request: SearchRequest) -> Page[SourceSearchHit] | CompactSourceResults:
        return source_search_view(search.source(request), request.work_id, request.format)

    @app.post("/annotations/search", summary="在指定作品内检索写法说明关键词")
    def annotation_search(
        request: AnnotationSearchRequest,
    ) -> Page[AnnotationSearchHit] | CompactAnnotationResults:
        return annotation_search_view(search.annotations(request), request.work_id, request.format)

    @app.post("/works/{work_id}/semantic-indexes", summary="显式创建指定层原文语义索引代")
    def semantic_create(work_id: UUID, request: SemanticCreate) -> SemanticWrite:
        if work_id != request.work_id:
            raise ServiceError("INVALID_INPUT", "路径与请求中的作品 ID 不一致")
        return semantic.create(request)

    @app.post("/works/{work_id}/semantic-indexes/{index_id}/build", summary="构建有限索引批次")
    def semantic_build(work_id: UUID, index_id: UUID, request: SemanticBuild) -> SemanticWrite:
        if work_id != request.work_id or index_id != request.index_id:
            raise ServiceError("INVALID_INPUT", "路径与请求中的作品或索引 ID 不一致")
        return semantic.build(request)

    @app.get("/works/{work_id}/semantic-indexes/status", summary="发现索引代与分页读取缺口")
    def semantic_get(
        work_id: UUID,
        kind: Literal["fulltext", "annotation"] = "fulltext",
        index_id: UUID | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 100,
        cursor: Annotated[str | None, Query(max_length=2048)] = None,
    ) -> SemanticStatus:
        return semantic.get(
            SemanticGet(
                work_id=work_id,
                kind=kind,
                index_id=index_id,
                limit=limit,
                cursor=cursor,
            )
        )

    @app.post("/works/{work_id}/semantic-search", summary="在指定作品内查询原文语义候选")
    def semantic_search(
        work_id: UUID,
        request: SemanticSearch,
    ) -> SemanticResults | CompactSemanticResults:
        if work_id != request.work_id:
            raise ServiceError("INVALID_INPUT", "路径与请求中的作品 ID 不一致")
        return semantic_search_view(semantic.search(request), work_id, request.kind, request.format)

    # SDK 自带 /mcp 路由，根挂载必须放在所有现有路由后，避免遮蔽 REST。
    app.mount("/", mcp_app)
    return app
