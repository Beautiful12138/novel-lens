"""本机视觉预览：静态文件与两个固定只读 API 同源，不转发任意 URL 或写入接口。"""

import json
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1] / "docs" / "prototypes" / "webui"
READ_ROUTES = {"/api/library/browse": "/library/browse", "/api/source/read": "/source/read"}


class PreviewHandler(SimpleHTTPRequestHandler):
    """仅监听回环地址；请求与响应不写入日志，不接触数据库凭据。"""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def do_POST(self) -> None:
        route = READ_ROUTES.get(self.path)
        if route is None:
            self.respond(404, {"message": "预览仅开放作品目录和原文读取"})
            return
        origin = self.headers.get("Origin")
        if origin and origin not in {"http://127.0.0.1:8765", "http://localhost:8765"}:
            self.respond(403, {"message": "不接受跨站请求"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 262144:
                self.respond(413, {"message": "请求大小无效"})
                return
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("请求必须为对象")
        except (ValueError, UnicodeDecodeError):
            self.respond(422, {"message": "请求格式无效"})
            return
        try:
            with httpx.Client(trust_env=False, timeout=40) as client:
                response = client.post("http://127.0.0.1:8000" + route, json=payload)
            self.respond(response.status_code, response.json())
        except (httpx.HTTPError, ValueError):
            self.respond(502, {"message": "无法读取本机 8000 端口的业务服务，请启动服务后重试"})

    def respond(self, status: int, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", 8765), PreviewHandler).serve_forever()
