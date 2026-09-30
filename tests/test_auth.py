"""验证统一入口认证、浏览器会话和公开部署配置，不使用真实部署密钥。"""

from pathlib import Path
from time import time
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from test_live_http import running_server

from novel_lens import auth
from novel_lens.app import create_app
from novel_lens.config import ConfigurationError, Settings, load_settings

KEY = "test-only-access-key"
ORIGIN = "https://reader.example.test:10003"


def client(key: str = KEY) -> TestClient:
    """HTTPS 测试客户端验证 Secure Cookie 的实际发送规则，不连接业务库。"""
    settings = Settings.model_construct(access_key=SecretStr(key), public_origin=ORIGIN)
    return TestClient(create_app(settings), base_url=ORIGIN)


def test_all_business_surfaces_require_authentication() -> None:
    with client() as http:
        for path in ("/works", "/openapi.json", "/ai/openapi.json", "/docs", "/mcp"):
            response = http.get(path)
            assert response.status_code == 401, path
            assert response.headers["www-authenticate"].startswith("Bearer")
        for path in ("/library/browse", "/preparation/batch", "/preparation/cleanup"):
            assert http.post(path, json={}).status_code == 401
        assert http.get("/parts/00000000-0000-0000-0000-000000000000/file").status_code == 401
        assert http.get("/health").status_code == 200
        assert http.get("/auth/status").json() == {"enabled": True, "authenticated": False}
        assert http.get("/ui/").status_code != 401
        assert http.get("/ai/openapi.json", params={"access_key": KEY}).status_code == 401
        response = http.get("/ai/openapi.json", headers={"Authorization": f"Bearer {KEY}"})
        assert response.status_code == 200
        schema = response.json()
        assert schema["security"] == [{"AccessKey": []}, {"BrowserSession": []}]
        assert schema["components"]["securitySchemes"]["AccessKey"]["scheme"] == "bearer"
        assert schema["servers"] == [{"url": ORIGIN}]
        assert response.headers["cache-control"] == "no-store"


def test_browser_login_origin_priority_and_logout() -> None:
    with client() as http:
        bad = http.post("/auth/login", json={"access_key": "wrong-private-input"})
        assert bad.status_code == 401 and "wrong-private-input" not in bad.text
        assert (
            http.post(
                "/auth/login",
                json={"access_key": KEY},
                headers={"Origin": "https://elsewhere.test"},
            ).status_code
            == 403
        )
        logged = http.post("/auth/login", json={"access_key": KEY}, headers={"Origin": ORIGIN})
        assert logged.status_code == 200
        cookie = logged.headers["set-cookie"]
        assert all(flag in cookie for flag in ("HttpOnly", "Secure", "SameSite=strict"))
        assert KEY not in cookie and KEY not in logged.text
        assert http.get("/auth/status").json()["authenticated"]
        assert http.get("/ai/openapi.json").status_code == 200
        # 缺少 Origin 或同主机其他端口均不能借用浏览器 Cookie 写入。
        for origin in (None, "https://reader.example.test:10000", "null"):
            headers = {"Origin": origin} if origin is not None else {}
            assert http.post("/library/browse", json={}, headers=headers).status_code == 403
        assert http.post("/source/read", json={}, headers={"Origin": ORIGIN}).status_code == 422
        assert (
            http.get("/ai/openapi.json", headers={"Authorization": "Bearer wrong"}).status_code
            == 401
        )
        assert http.post("/auth/logout", headers={"Origin": ORIGIN}).status_code == 200
        assert not http.get("/auth/status").json()["authenticated"]
        assert http.get("/ai/openapi.json").status_code == 401


def test_session_expiry_tampering_and_key_rotation(monkeypatch: pytest.MonkeyPatch) -> None:
    with client() as http:
        http.post("/auth/login", json={"access_key": KEY})
        saved = http.cookies.get(auth.COOKIE_NAME)
        assert saved is not None
        with client("new-test-key") as rotated:
            rotated.cookies.set(auth.COOKIE_NAME, saved)
            assert rotated.get("/ai/openapi.json").status_code == 401
        with client() as forged:
            forged.cookies.set(auth.COOKIE_NAME, saved[:-1] + ("a" if saved[-1] != "a" else "b"))
            assert forged.get("/ai/openapi.json").status_code == 401
        now = time()
        monkeypatch.setattr(auth, "time", lambda: now + auth.SESSION_SECONDS + 1)
        assert http.get("/ai/openapi.json").status_code == 401
        assert not http.get("/auth/status").json()["authenticated"]


def test_failed_credentials_are_throttled_and_recover(monkeypatch: pytest.MonkeyPatch) -> None:
    with client() as http:
        for _ in range(10):
            assert (
                http.get(
                    "/ai/openapi.json", headers={"Authorization": "Bearer incorrect"}
                ).status_code
                == 401
            )
        blocked = http.post("/auth/login", json={"access_key": KEY})
        assert blocked.status_code == 429 and blocked.headers["retry-after"] == "60"
        now = time()
        monkeypatch.setattr(auth, "time", lambda: now + 61)
        assert http.post("/auth/login", json={"access_key": KEY}).status_code == 200


def test_public_configuration_fails_closed_without_leaking_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("NOVEL_LENS_PUBLIC_ORIGIN", ORIGIN)
    monkeypatch.delenv("NOVEL_LENS_ACCESS_KEY", raising=False)
    with pytest.raises(ConfigurationError, match="访问密钥"):
        load_settings()
    monkeypatch.setenv("NOVEL_LENS_ACCESS_KEY", KEY)
    assert load_settings().public_origin == ORIGIN
    for origin in ("http://reader.test", "https://u:private-value@reader.test", ORIGIN + "/path"):
        monkeypatch.setenv("NOVEL_LENS_PUBLIC_ORIGIN", origin)
        with pytest.raises(ConfigurationError) as error:
            load_settings()
        assert "private-value" not in str(error.value) and KEY not in str(error.value)
    monkeypatch.setenv("NOVEL_LENS_PUBLIC_ORIGIN", ORIGIN)
    monkeypatch.setenv("NOVEL_LENS_ACCESS_KEY", " ")
    with pytest.raises(ConfigurationError, match="ACCESS_KEY"):
        load_settings()


def test_unconfigured_local_mode_keeps_existing_behavior() -> None:
    with TestClient(create_app()) as http:
        assert http.get("/auth/status").json() == {"enabled": False, "authenticated": True}
        assert http.get("/ai/openapi.json").status_code == 200
        assert "security" not in http.get("/ai/openapi.json").json()


def test_authenticated_real_http_and_mcp(postgres_url: str, tmp_path: Path) -> None:
    """真实协议握手与只读业务请求验证公开 Host、Origin 和 Bearer 的组合。"""
    with running_server(
        postgres_url, tmp_path, "auth", mcp_profile="business", access_key=KEY, public_origin=ORIGIN
    ) as http:
        assert http.post("/library/browse", json={"view": "works"}).status_code == 401
        headers = {
            "Authorization": f"Bearer {KEY}",
            "Host": "reader.example.test:10003",
            "Origin": ORIGIN,
            "Accept": "application/json, text/event-stream",
        }
        assert (
            http.post("/library/browse", json={"view": "works"}, headers=headers).status_code == 200
        )
        initialize: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "auth-test", "version": "1"},
            },
        }
        assert http.post("/mcp", json=initialize).status_code == 401
        initialized = http.post("/mcp", json=initialize, headers=headers)
        assert initialized.status_code == 200 and "result" in initialized.json()
        listing = http.post(
            "/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, headers=headers
        )
        assert listing.status_code == 200 and len(listing.json()["result"]["tools"]) == 15
        read = http.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "library_browse", "arguments": {"view": "works"}},
            },
            headers=headers,
        )
        assert read.status_code == 200 and not read.json()["result"].get("isError", False)
        denied = http.post("/mcp", json=initialize, headers=headers | {"Host": "other.test"})
        assert denied.status_code == 421
