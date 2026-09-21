"""验证部署边界与进程回收；协议样例不替代真实模型验收。"""

from __future__ import annotations

import hashlib
import json
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from novel_lens import embedding_runtime as runtime


def candidate(tmp_path: Path) -> Path:
    """创建独立候选配置，所用小文件刻意不冒充实际模型。"""
    (tmp_path / "model.gguf").write_bytes(b"invalid model fixture")
    path = tmp_path / "candidate.toml"
    path.write_text(
        f"executable = {json.dumps(sys.executable)}\n"
        'model_path = "model.gguf"\nthreads = 4\nthreads_batch = 4\n',
        encoding="utf-8",
    )
    return path


def test_saved_paths_do_not_change_meaning_and_existing_config_is_preserved(tmp_path: Path) -> None:
    path = candidate(tmp_path)
    config = runtime.read_config(path)
    destination = tmp_path / "another-directory"
    destination.mkdir()
    saved = destination / "embedding.local.toml"
    runtime.save_config(config, saved)
    assert runtime.read_config(saved) == config
    assert config.model_path == str(tmp_path / "model.gguf")
    original = saved.read_bytes()
    with pytest.raises(FileExistsError):
        runtime.save_config(config, saved)
    assert saved.read_bytes() == original


def test_verify_does_not_overwrite_a_damaged_existing_config(tmp_path: Path) -> None:
    path = candidate(tmp_path)
    saved = tmp_path / "embedding.local.toml"
    saved.write_bytes(b"damaged existing config")
    assert runtime.main(["verify", "--config", str(path), "--save", str(saved)]) == 1
    assert saved.read_bytes() == b"damaged existing config"


def test_corrupt_model_never_becomes_saved_config(tmp_path: Path) -> None:
    path = candidate(tmp_path)
    saved = tmp_path / "embedding.local.toml"
    with pytest.raises(runtime.RuntimeFailure, match="SHA-256"):
        runtime.check_artifacts(runtime.read_config(path))
    assert runtime.main(["verify", "--config", str(path), "--save", str(saved)]) == 1
    assert not saved.exists()


def test_wrong_runtime_version_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = candidate(tmp_path)
    monkeypatch.setattr(
        runtime, "MODEL_SHA256", hashlib.sha256(b"invalid model fixture").hexdigest()
    )
    # Python 的真实 --version 输出不能被当作 llama.cpp 已验证版本。
    with pytest.raises(runtime.RuntimeFailure, match="运行时版本不符"):
        runtime.check_artifacts(runtime.read_config(path))


def test_busy_port_is_not_reused_or_terminated(tmp_path: Path) -> None:
    config = runtime.read_config(candidate(tmp_path))
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        config.port = occupied.getsockname()[1]
        with pytest.raises(runtime.RuntimeFailure, match="端口"):
            with runtime.running_server(config):
                pytest.fail("不能复用已占用的端口")
        # 原监听者仍能正常接受连接。
        with socket.create_connection(("127.0.0.1", config.port), timeout=1):
            connection, _ = occupied.accept()
            connection.close()


def test_failure_reaps_only_the_child_it_started(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = runtime.read_config(candidate(tmp_path))
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        config.port = reservation.getsockname()[1]
    children: list[subprocess.Popen[bytes]] = []
    popen = subprocess.Popen

    def capture(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
        child: subprocess.Popen[bytes] = popen(*args, **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(subprocess, "Popen", capture)
    monkeypatch.setattr(
        runtime,
        "server_command",
        lambda config: [sys.executable, "-c", "import time; time.sleep(60)"],
    )
    monkeypatch.setattr(runtime.Endpoint, "health", lambda self: None)
    with pytest.raises(runtime.RuntimeFailure, match="验证失败"):
        with runtime.running_server(config):
            assert children[0].poll() is None
            raise runtime.RuntimeFailure("验证失败")
    assert len(children) == 1 and children[0].poll() is not None


@pytest.mark.parametrize("bad", ["dimension", "nonfinite", "norm", "index"])
def test_invalid_vectors_are_rejected(bad: str, monkeypatch: pytest.MonkeyPatch) -> None:
    vector = [1.0] + [0.0] * 1023
    index = 0
    if bad == "dimension":
        vector.pop()
    elif bad == "nonfinite":
        vector[0] = float("nan")
    elif bad == "norm":
        vector[0] = 2.0
    else:
        index = 1
    reply = {"data": [{"index": index, "embedding": vector}], "usage": {"prompt_tokens": 10}}
    monkeypatch.setattr(runtime.Endpoint, "request", lambda *args, **kwargs: reply)
    with pytest.raises(runtime.RuntimeFailure):
        runtime.Endpoint(18081).embed("协议验证样例")


def test_runtime_environment_does_not_override_saved_parameters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLAMA_ARG_HOST", "0.0.0.0")
    monkeypatch.setenv("LLAMA_ARG_MODEL", "another.gguf")
    monkeypatch.setenv("PATH", "retained-loader-path")
    environment = runtime.runtime_environment()
    assert "LLAMA_ARG_HOST" not in environment and "LLAMA_ARG_MODEL" not in environment
    assert environment["PATH"] == "retained-loader-path"


def test_healthy_foreign_model_is_not_reported_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    def reply(self: runtime.Endpoint, route: str, **kwargs: Any) -> dict[str, Any]:
        return {"status": "ok"} if route == "/health" else {"data": [{"id": "another-model"}]}

    monkeypatch.setattr(runtime.Endpoint, "request", reply)
    with pytest.raises(runtime.RuntimeFailure, match="模型标识"):
        runtime.Endpoint(18081).health()
