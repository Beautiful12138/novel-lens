"""真实 HTTP 进程的持久化检查；测试库由会话 fixture 创建与清理。"""

import os
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import httpx


@contextmanager
def running_server(
    url: str,
    directory: Path,
    label: str,
    *,
    request_limit: int | None = None,
    embedding_config: Path | None = None,
    request_timeout: float = 10,
) -> Iterator[httpx.Client]:
    """随机端口启动服务，退出时发送关闭信号；超时强制回收并判定失败。"""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    env = {key: value for key, value in os.environ.items() if not key.startswith("NOVEL_LENS_")}
    env.update(NOVEL_LENS_DATABASE_URL=url, NOVEL_LENS_PORT=str(port))
    if request_limit is not None:
        env["NOVEL_LENS_MAX_REQUEST_BYTES"] = str(request_limit)
    if embedding_config is not None:
        env["NOVEL_LENS_EMBEDDING_CONFIG"] = str(embedding_config)
    with (
        (directory / f"{label}.stdout").open("wb") as out,
        (directory / f"{label}.stderr").open("wb") as err,
    ):
        process = subprocess.Popen(
            [sys.executable, "-m", "novel_lens"],
            cwd=directory,
            env=env,
            stdout=out,
            stderr=err,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        )
        try:
            with httpx.Client(
                base_url=f"http://127.0.0.1:{port}", timeout=request_timeout, trust_env=False
            ) as client:
                deadline = time.monotonic() + 15
                while True:
                    assert process.poll() is None, "服务在健康检查前退出"
                    try:
                        if client.get("/health").status_code == 200:
                            break
                    except httpx.ConnectError:
                        pass
                    assert time.monotonic() < deadline, "服务启动超时"
                    time.sleep(0.05)
                yield client
        finally:
            if process.poll() is None:
                process.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGINT)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
                raise AssertionError("服务未在关闭信号后正常退出") from None
    assert "Application shutdown complete" in (directory / f"{label}.stderr").read_text(
        encoding="utf-8"
    )
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", port))


def test_restart_preserves_source_ids_and_unreceived_result(
    postgres_url: str, tmp_path: Path
) -> None:
    key = str(uuid4())
    data = f"\ufeff书名：{uuid4()}\r\n标题：开始\r\n　private-novel-body😀 \t\r\n尾段".encode()
    with running_server(postgres_url, tmp_path, "first") as client:
        # 调用方忽略首次响应，只保存 request_id，模拟提交成功但没有保存返回值。
        assert (
            client.post(
                "/work-imports", data={"request_id": key}, files={"file": ("book.txt", data)}
            ).status_code
            == 201
        )
        work = client.get(f"/work-imports/{key}").json()["work"]
        work_id = work["id"]
        section = client.get(f"/works/{work_id}/sections").json()["items"][0]
        path = f"/works/{work_id}/sections/{section['id']}/paragraphs"
        paragraphs = client.get(path).json()["items"]
        assert client.get(f"/works/{work_id}/file").content == data
    with running_server(postgres_url, tmp_path, "second") as client:
        assert client.get(f"/work-imports/{key}").json()["work"] == work
        assert client.get(path).json()["items"] == paragraphs
        assert client.get(f"/works/{work_id}/file").content == data
        replay = client.post(
            "/work-imports", data={"request_id": key}, files={"file": ("book.txt", data)}
        )
        assert replay.status_code == 200 and replay.json()["work"] == work
    for label in ["first", "second"]:
        assert (tmp_path / f"{label}.stdout").read_bytes() == b""
        log = (tmp_path / f"{label}.stderr").read_bytes()
        assert b"private-novel-body" not in log
        assert b"SELECT" not in log
