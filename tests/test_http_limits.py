"""验证接收边界与数据库不可用时的实际 HTTP 响应。"""

from collections.abc import Iterator
from uuid import uuid4

from pydantic import SecretStr
from starlette.testclient import TestClient

from novel_lens.app import create_app
from novel_lens.config import Settings


def test_size_limits_including_without_content_length() -> None:
    settings = Settings.model_construct(max_file_bytes=20, max_request_bytes=512)
    with TestClient(create_app(settings)) as client:
        response = client.post(
            f"/works/{uuid4()}/part-imports/validate", files={"file": ("large.txt", b"x" * 21)}
        )
        assert response.status_code == 413
        assert response.json()["code"] == "FILE_TOO_LARGE"

        def chunks() -> Iterator[bytes]:
            yield (
                b"--boundary\r\nContent-Disposition: form-data; "
                b'name="file"; filename="book.txt"\r\n\r\n'
            )
            yield b"x" * 300
            yield b"y" * 300
            yield b"\r\n--boundary--\r\n"

        response = client.post(
            f"/works/{uuid4()}/part-imports/validate",
            content=chunks(),
            headers={"Content-Type": "multipart/form-data; boundary=boundary"},
        )
        assert "content-length" not in response.request.headers
        assert response.status_code == 413
        assert response.json()["code"] == "REQUEST_TOO_LARGE"
        response = client.post(
            f"/works/{uuid4()}/source/read",
            content=b" " * 513,
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 413


def test_database_unavailable_keeps_health_alive() -> None:
    # 端口 1 是本机未配置的数据库地址；不停止共享的开发数据库。
    settings = Settings.model_construct(
        database_url=SecretStr("postgresql+psycopg://invalid:private-password@127.0.0.1:1/absent")
    )
    for config in [Settings.model_construct(), settings]:
        with TestClient(create_app(config)) as client:
            assert client.get("/health").json() == {"status": "ok"}
            response = client.get("/works")
            assert response.status_code == 503
            assert "private-password" not in response.text
            assert "SELECT" not in response.text


def test_malformed_multipart_rejected_safely() -> None:
    with TestClient(create_app()) as client:
        response = client.post(
            f"/works/{uuid4()}/part-imports",
            content=b"private-body",
            headers={"Content-Type": "multipart/form-data"},
        )
        assert response.status_code == 422
        assert "private-body" not in response.text
        response = client.post(f"/works/{uuid4()}/part-imports", json={"file": "/server/path"})
        assert response.status_code == 422
