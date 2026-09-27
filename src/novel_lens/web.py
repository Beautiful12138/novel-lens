"""正式 WebUI 的静态入口；业务 API 继续使用既有规则与同源地址。"""

from pathlib import Path

from fastapi import FastAPI
from starlette.responses import HTMLResponse
from starlette.staticfiles import StaticFiles

WEB_ROOT = Path(__file__).resolve().parent / "webui"


def mount_webui(app: FastAPI) -> None:
    """在根 MCP 挂载之前注册；源码安装未构建时明确说明，不伪装为空页面。"""
    if (WEB_ROOT / "index.html").is_file():
        app.mount("/ui", StaticFiles(directory=WEB_ROOT, html=True), name="webui")
    else:

        @app.get("/ui/{path:path}", include_in_schema=False)
        def unavailable(path: str) -> HTMLResponse:
            return HTMLResponse(
                '<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
                "<title>NovelLens · 前端尚未构建</title><h1>前端尚未构建</h1>"
                "<p>在 web 目录执行 npm ci 与 npm run build，然后重启业务服务。</p></html>",
                status_code=503,
            )
