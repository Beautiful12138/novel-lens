"""单密钥入口鉴权与浏览器会话；不引入用户表，所有凭据持有者权限相同。"""

import hashlib
import hmac
import secrets
from collections import OrderedDict
from time import time
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, Request, Response
from fastapi.openapi.utils import get_openapi
from pydantic import BaseModel, Field, SecretStr
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from novel_lens.config import Settings
from novel_lens.contracts import RequestModel
from novel_lens.errors import ServiceError

COOKIE_NAME = "novel_lens_session"
SESSION_SECONDS = 8 * 60 * 60
FAILURE_WINDOW = 60
FAILURE_LIMIT = 10


class Login(RequestModel):
    access_key: SecretStr = Field(min_length=1, max_length=512)


class AuthenticationStatus(BaseModel):
    enabled: bool
    authenticated: bool


class AccessPolicy:
    """单进程内有界失败计数；方法在 ASGI 事件循环内调用，不跨线程修改状态。"""

    def __init__(self, settings: Settings) -> None:
        self.enabled = settings.access_key is not None
        self.origin = settings.public_origin
        self.key = (
            settings.access_key.get_secret_value().encode("utf-8")
            if settings.access_key is not None
            else b""
        )
        # 区分 API 密钥和会话签名用途；原密钥不会进入 Cookie。
        self.signing_key = hmac.digest(self.key, b"novel-lens-browser-session-v1", "sha256")
        self.failures: OrderedDict[str, tuple[float, int]] = OrderedDict()

    def client(self, request: Request) -> str:
        """使用服务器解析的客户端地址；不直接信任请求自带的转发头。"""
        return request.client.host if request.client is not None else "unknown"

    def check_attempt(self, request: Request) -> None:
        entry = self.failures.get(self.client(request))
        if entry is not None:
            started, count = entry
            if time() - started >= FAILURE_WINDOW:
                self.failures.pop(self.client(request), None)
            elif count >= FAILURE_LIMIT:
                raise ServiceError("AUTH_RATE_LIMITED", "尝试过于频繁，请一分钟后重试", 429)

    def failed(self, request: Request) -> None:
        client = self.client(request)
        started, count = self.failures.pop(client, (time(), 0))
        if time() - started >= FAILURE_WINDOW:
            started, count = time(), 0
        self.failures[client] = started, count + 1
        if len(self.failures) > 2048:
            self.failures.popitem(last=False)

    def check_key(self, value: str) -> bool:
        """摘要后常量时间比较，不受输入长度差异影响，也不记录输入。"""
        return len(value) <= 512 and secrets.compare_digest(
            hashlib.sha256(value.encode("utf-8")).digest(), hashlib.sha256(self.key).digest()
        )

    def issue_session(self) -> str:
        payload = f"{int(time()) + SESSION_SECONDS}.{secrets.token_hex(16)}"
        signature = hmac.new(self.signing_key, payload.encode(), "sha256").hexdigest()
        return f"{payload}.{signature}"

    def valid_session(self, value: str) -> bool:
        if len(value) > 160:
            return False
        try:
            expires, nonce, signature = value.split(".")
            payload = f"{expires}.{nonce}"
            expected = hmac.new(self.signing_key, payload.encode(), "sha256").hexdigest()
            return int(expires) > time() and secrets.compare_digest(signature, expected)
        except (ValueError, TypeError):
            return False

    def check_origin(self, request: Request, *, required: bool) -> None:
        """浏览器写入必须来自精确 origin；同主机其他端口也不共享写入授权。"""
        origin = request.headers.get("origin")
        expected = self.origin or str(request.base_url).rstrip("/")
        if (origin is None and required) or (origin is not None and origin != expected):
            raise ServiceError("ORIGIN_FORBIDDEN", "请求来源与服务入口不一致", 403)

    def authenticate(self, request: Request) -> bool:
        """显式 Authorization 优先；错误 Bearer 不得被现有 Cookie 掩盖。"""
        if not self.enabled:
            return True
        authorization = request.headers.get("authorization")
        session = request.cookies.get(COOKIE_NAME)
        if authorization is not None:
            self.check_attempt(request)
            scheme, _, value = authorization.partition(" ")
            if scheme.lower() != "bearer" or not self.check_key(value):
                self.failed(request)
                return False
            return True
        if session is not None:
            self.check_attempt(request)
            if not self.valid_session(session):
                self.failed(request)
                return False
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                self.check_origin(request, required=True)
            return True
        return False


def auth_error(error: ServiceError) -> JSONResponse:
    """ASGI 层也使用统一错误结构，不依赖内层路由异常处理器。"""
    headers = {"Cache-Control": "no-store"}
    if error.status == 401:
        headers["WWW-Authenticate"] = 'Bearer realm="NovelLens"'
    if error.status == 429:
        headers["Retry-After"] = str(FAILURE_WINDOW)
    return JSONResponse(error.payload(), status_code=error.status, headers=headers)


class AccessMiddleware:
    """在读取请求体或调用业务前保护所有接口；静态壳本身不包含作品数据。"""

    def __init__(self, app: ASGIApp, policy: AccessPolicy) -> None:
        self.app, self.policy = app, policy

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self.policy.enabled:
            await self.app(scope, receive, send)
            return
        path = scope["path"]
        public = path in {"/auth/login", "/auth/status", "/auth/logout"} or (
            scope["method"] in {"GET", "HEAD"}
            and (path == "/health" or path == "/ui" or path.startswith("/ui/"))
        )
        if not public:
            try:
                if not self.policy.authenticate(Request(scope)):
                    raise ServiceError("UNAUTHORIZED", "请先登录或提供有效访问密钥", 401)
            except ServiceError as error:
                await auth_error(error)(scope, receive, send)
                return

        async def private_send(message: Message) -> None:
            # 含数据、登录状态和 API 描述的响应不进入共享缓存或历史缓存。
            if message["type"] == "http.response.start" and not path.startswith("/ui/"):
                message["headers"] = list(message.get("headers", [])) + [
                    (b"cache-control", b"no-store")
                ]
            await send(message)

        await self.app(scope, receive, private_send)


def mount_authentication(app: FastAPI, settings: Settings) -> None:
    """注册网页认证端点；公开部署的静态壳可加载，数据接口由统一中间件保护。"""
    policy = AccessPolicy(settings)
    app.add_middleware(AccessMiddleware, policy=policy)

    @app.get("/auth/status", response_model=AuthenticationStatus)
    async def status(request: Request) -> AuthenticationStatus | JSONResponse:
        try:
            return AuthenticationStatus(
                enabled=policy.enabled, authenticated=policy.authenticate(request)
            )
        except ServiceError as error:
            return auth_error(error)

    @app.post("/auth/login", response_model=AuthenticationStatus)
    async def login(
        body: Login, request: Request, response: Response
    ) -> AuthenticationStatus | JSONResponse:
        try:
            policy.check_origin(request, required=False)
            policy.check_attempt(request)
            if policy.enabled and not policy.check_key(body.access_key.get_secret_value()):
                policy.failed(request)
                raise ServiceError("UNAUTHORIZED", "访问密钥不正确，请重试", 401)
        except ServiceError as error:
            return auth_error(error)
        policy.failures.pop(policy.client(request), None)
        if policy.enabled:
            response.set_cookie(
                COOKIE_NAME,
                policy.issue_session(),
                max_age=SESSION_SECONDS,
                httponly=True,
                secure=policy.origin is not None,
                samesite="strict",
                path="/",
            )
        return AuthenticationStatus(enabled=policy.enabled, authenticated=True)

    @app.post("/auth/logout", response_model=AuthenticationStatus)
    async def logout(request: Request, response: Response) -> AuthenticationStatus | JSONResponse:
        try:
            policy.check_origin(request, required=True)
        except ServiceError as error:
            return auth_error(error)
        response.delete_cookie(
            COOKIE_NAME,
            path="/",
            secure=policy.origin is not None,
            httponly=True,
            samesite="strict",
        )
        return AuthenticationStatus(enabled=policy.enabled, authenticated=not policy.enabled)

    def openapi() -> dict[str, Any]:
        """声明实际启用的认证方式，业务 schema 继续从真实路由生成。"""
        if app.openapi_schema is None:
            schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
            if policy.enabled:
                schema.setdefault("components", {})["securitySchemes"] = {
                    "AccessKey": {"type": "http", "scheme": "bearer"},
                    "BrowserSession": {"type": "apiKey", "in": "cookie", "name": COOKIE_NAME},
                }
                schema["security"] = [{"AccessKey": []}, {"BrowserSession": []}]
                for path in ("/health", "/auth/status", "/auth/login", "/auth/logout"):
                    for operation in schema.get("paths", {}).get(path, {}).values():
                        operation["security"] = []
            if policy.origin is not None:
                schema["servers"] = [{"url": policy.origin}]
            app.openapi_schema = schema
        return app.openapi_schema

    app.openapi = openapi  # type: ignore[method-assign]


def public_host(settings: Settings) -> str | None:
    """MCP 的 Host 白名单复用唯一公开 origin，不接受通配主机。"""
    return urlsplit(settings.public_origin).netloc if settings.public_origin else None
