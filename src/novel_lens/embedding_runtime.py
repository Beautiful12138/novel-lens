"""按 AI 或用户准备的配置运行本机 embedding；不探测硬件、下载或自动调参。"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import socket
import subprocess
import sys
import time
import tomllib
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Final, Literal
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

MODEL_ID: Final = "qwen3-embedding-0.6b-q8_0-v1"
MODEL_SHA256 = "06507c7b42688469c4e7298b0a1e16deff06caf291cf0a5b278c308249c3e439"
RUNTIME_COMMIT = "b29c606e2"
DIMENSIONS = 1024


class RuntimeFailure(ValueError):
    """可呈现给部署操作者的失败，不携带请求正文或完整响应。"""


class RuntimeConfig(BaseModel):
    """本机参数不覆盖模型契约；未知字段及 CPU/GPU 冲突直接拒绝。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal[1] = 1
    model_contract: Literal["qwen3-embedding-0.6b-q8_0-v1"] = MODEL_ID
    executable: str = Field(min_length=1)
    model_path: str = Field(min_length=1)
    backend: Literal["cpu", "cuda"] = "cpu"
    device: str = "none"
    threads: int = Field(ge=1, le=256)
    threads_batch: int = Field(ge=1, le=256)
    context_tokens: int = Field(default=4096, ge=256, le=4096)
    port: int = Field(default=18081, ge=1024, le=65535)

    @model_validator(mode="after")
    def validate_device(self) -> RuntimeConfig:
        """GPU 显式指定单个 CUDA 设备，避免默认设备或静默 CPU 回退。"""
        if self.backend == "cpu" and self.device != "none":
            raise ValueError("CPU 配置必须使用 device = none")
        if self.backend == "cuda" and not re.fullmatch(r"CUDA\d+", self.device):
            raise ValueError("CUDA 配置须指定运行时列出的 CUDA 设备，如 CUDA0")
        return self


def read_config(path: Path) -> RuntimeConfig:
    """相对文件路径以配置目录为基准；读取失败不初始化或覆盖文件。"""
    try:
        config = RuntimeConfig.model_validate(tomllib.loads(path.read_text(encoding="utf-8-sig")))
    except (OSError, UnicodeError):
        raise RuntimeFailure("配置无法读取；请检查文件路径、权限与 UTF-8 编码。") from None
    except tomllib.TOMLDecodeError:
        raise RuntimeFailure("配置 TOML 语法无效；请修复候选文件，不覆盖已有配置。") from None
    except ValidationError as error:
        fields = sorted(
            {
                ".".join(str(item) for item in entry["loc"]) or "backend/device"
                for entry in error.errors(include_input=False, include_context=False)
            }
        )
        raise RuntimeFailure(
            "配置字段无效：" + "、".join(fields) + "；请核对 embedding.example.toml 的类型与约束。"
        ) from None
    for field in ("executable", "model_path"):
        resolved = (path.resolve().parent / getattr(config, field)).resolve()
        setattr(config, field, str(resolved))
    return config


def runtime_environment() -> dict[str, str]:
    """禁止全局 llama 参数覆盖已验证配置，保留加载运行时依赖所需的环境。"""
    return {k: v for k, v in os.environ.items() if not k.upper().startswith("LLAMA_ARG_")}


def process_options() -> dict[str, Any]:
    """Windows 子进程不打开可见窗口；前台父进程仍可接收 Ctrl+C。"""
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


def inspect_runtime(config: RuntimeConfig, option: str) -> str:
    """只执行已配置运行时的自检命令，超时或失败不继续启动。"""
    result: subprocess.CompletedProcess[bytes] = subprocess.run(
        [config.executable, option],
        env=runtime_environment(),
        capture_output=True,
        timeout=20,
        **process_options(),
    )
    if result.returncode:
        raise RuntimeFailure(f"运行时 {option} 失败，退出码 {result.returncode}；请检查依赖。")
    return (result.stdout + result.stderr).decode("utf-8", errors="replace")


def check_artifacts(config: RuntimeConfig) -> None:
    """每次启动检查实际权重与运行时版本，不以文件名或维度推断兼容。"""
    if not Path(config.executable).is_file() or not Path(config.model_path).is_file():
        raise RuntimeFailure("运行时或模型文件不存在；请按部署指南准备文件并修正路径。")
    with Path(config.model_path).open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != MODEL_SHA256:
        raise RuntimeFailure("模型 SHA-256 与固定契约不符；不启动或自动更换模型。")
    version = inspect_runtime(config, "--version")
    if not re.search(rf"build 10964, commit {RUNTIME_COMMIT}\b", version):
        raise RuntimeFailure("运行时版本不符；需要 llama.cpp build 10964 / commit b29c606e2。")
    if config.backend == "cuda":
        devices = inspect_runtime(config, "--list-devices")
        if not re.search(rf"(?m)^\s*{re.escape(config.device)}:", devices):
            raise RuntimeFailure(
                "所选 CUDA 设备未在运行时中列出；检查 GPU 构建与驱动，不回退 CPU。"
            )


def server_command(config: RuntimeConfig) -> list[str]:
    """只构建受支持的固定参数，批量 token 上限与上下文相同，并发固定为一。"""
    context = str(config.context_tokens)
    return [
        config.executable,
        "-m",
        config.model_path,
        "--embedding",
        "--pooling",
        "last",
        "--embd-normalize",
        "2",
        "--alias",
        MODEL_ID,
        "--host",
        "127.0.0.1",
        "--port",
        str(config.port),
        "-t",
        str(config.threads),
        "-tb",
        str(config.threads_batch),
        "-c",
        context,
        "-b",
        context,
        "-ub",
        context,
        "-np",
        "1",
        "--device",
        config.device,
        "-ngl",
        "0" if config.backend == "cpu" else "99",
        "--fit",
        "off",
        "--no-webui",
        "--log-disable",
        "--no-cache-prompt",
        "--cache-ram",
        "0",
    ]


class Endpoint:
    """仅连接配置中的 IPv4 回环端口，不使用系统 HTTP 代理。"""

    def __init__(self, port: int) -> None:
        self.url = f"http://127.0.0.1:{port}"
        self.opener = build_opener(ProxyHandler({}))

    def request(self, route: str, body: dict[str, Any] | None = None, timeout: int = 120) -> Any:
        """响应仅在内存中校验；错误不回显可能包含正文的服务响应。"""
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = Request(self.url + route, data=data, headers={"Content-Type": "application/json"})
        with self.opener.open(request, timeout=timeout) as response:
            return json.load(response)

    def health(self) -> None:
        """健康检查同时核对模型别名，不能把任意占用端口的服务当作就绪。"""
        if self.request("/health", timeout=2).get("status") != "ok":
            raise RuntimeFailure("embedding 服务尚未就绪。")
        models = self.request("/v1/models", timeout=2).get("data", [])
        if not any(row.get("id") == MODEL_ID for row in models):
            raise RuntimeFailure("端点模型标识与配置契约不符。")

    def embed(self, text: str) -> dict[str, float | int]:
        """验证真实单条向量及 token 用量；报告只包含耗时和维度等元信息。"""
        started = time.perf_counter()
        result = self.request("/v1/embeddings", {"model": MODEL_ID, "input": [text]})
        rows = result.get("data", [])
        if len(rows) != 1 or rows[0].get("index") != 0:
            raise RuntimeFailure("embedding 响应条目与输入不对应。")
        vector = rows[0].get("embedding", [])
        if len(vector) != DIMENSIONS or not all(
            type(x) in (int, float) and math.isfinite(x) for x in vector
        ):
            raise RuntimeFailure("embedding 向量维度或数值无效。")
        if not math.isclose(sum(x * x for x in vector), 1.0, abs_tol=1e-3):
            raise RuntimeFailure("embedding 向量未按 L2 归一化。")
        tokens = result.get("usage", {}).get("prompt_tokens")
        if type(tokens) is not int or tokens <= 0:
            raise RuntimeFailure("embedding 未返回有效 token 用量。")
        return {
            "seconds": time.perf_counter() - started,
            "tokens": tokens,
            "dimensions": DIMENSIONS,
        }


@contextmanager
def running_server(config: RuntimeConfig) -> Iterator[Endpoint]:
    """前台管理本次子进程；启动失败、异常及 Ctrl+C 都回收该子进程。"""
    with socket.socket() as listener:
        try:
            listener.bind(("127.0.0.1", config.port))
        except OSError:
            raise RuntimeFailure("embedding 端口已占用或不可绑定；不终止已有服务。") from None
    process = subprocess.Popen(
        server_command(config),
        env=runtime_environment(),
        stdout=subprocess.DEVNULL,
        **process_options(),
    )
    endpoint = Endpoint(config.port)
    try:
        deadline = time.monotonic() + 90
        while True:
            if process.poll() is not None:
                raise RuntimeFailure(f"embedding 服务提前退出，退出码 {process.returncode}。")
            try:
                endpoint.health()
                break
            except (OSError, URLError, RuntimeFailure):
                if time.monotonic() >= deadline:
                    raise RuntimeFailure("embedding 服务未在 90 秒内就绪。") from None
                time.sleep(0.25)
        yield endpoint
        if process.poll() is not None:
            raise RuntimeFailure(f"embedding 服务异常退出，退出码 {process.returncode}。")
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)


def probe(endpoint: Endpoint, context_tokens: int) -> dict[str, Any]:
    """用自编文本验证向量与拒绝超长输入，不使用小说或数据库。"""
    short = endpoint.embed("雨落在窗沿，她放下杯子，听见门外的脚步声。")
    pool = "雨落在窗沿，她放下杯子，听见门外的脚步声。\n" * context_tokens
    tokens = endpoint.request("/tokenize", {"content": pool, "add_special": False})["tokens"]
    long_text = endpoint.request(
        "/detokenize", {"tokens": tokens[: min(1024, context_tokens - 64)]}
    )["content"]
    longer = endpoint.embed(long_text)
    oversized = endpoint.request("/detokenize", {"tokens": tokens[: context_tokens + 32]})[
        "content"
    ]
    try:
        endpoint.embed(oversized)
    except HTTPError as error:
        if error.code != 400:
            raise RuntimeFailure("超长输入未返回预期的 HTTP 400。") from None
    else:
        raise RuntimeFailure("服务接受了超长输入；不保存该配置，以免静默截断。")
    return {"short": short, "longer": longer, "oversize_http_status": 400}


def save_config(config: RuntimeConfig, path: Path) -> None:
    """验证成功后由调用方保存；独占创建，写入失败仅回收本次新文件。"""
    content = "# 已通过真实向量验证；本机配置不提交 Git。\n"
    content += "".join(
        f"{key} = {json.dumps(value, ensure_ascii=False)}\n"
        for key, value in config.model_dump().items()
    )
    created = False
    try:
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            created = True
            stream.write(content)
    except OSError:
        if created:
            path.unlink(missing_ok=True)
        raise


def main(argv: list[str] | None = None) -> int:
    """各命令不隐式下载或覆盖配置；stdout 为元信息，错误写 stderr。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["check", "verify", "run", "status"])
    parser.add_argument("--config", type=Path, default=Path("embedding.local.toml"))
    parser.add_argument("--save", type=Path, help="仅 verify 可用；成功后独占创建目标配置")
    args = parser.parse_args(argv)
    if args.save is not None and args.command != "verify":
        parser.error("--save 仅用于 verify")
    try:
        if args.save is not None and (args.save.exists() or args.save.is_symlink()):
            raise RuntimeFailure("目标配置已存在；不覆盖。修复时请另存候选并验证。")
        config = read_config(args.config)
        report: dict[str, Any] = {"command": args.command, "model_contract": MODEL_ID}
        if args.command == "status":
            Endpoint(config.port).health()
            report.update(status="ready", url=f"http://127.0.0.1:{config.port}")
        else:
            check_artifacts(config)
            if args.command == "check":
                report.update(config=config.model_dump())
            else:
                with running_server(config) as endpoint:
                    if args.command == "verify":
                        report.update(probe(endpoint, config.context_tokens))
                    else:
                        short = endpoint.embed("窗外的雨停了。")
                        print(
                            json.dumps({"status": "ready", "url": endpoint.url, "probe": short}),
                            flush=True,
                        )
                        # 不再次调参或重复生成向量，轮询端点以发现子进程退出。
                        while True:
                            time.sleep(2)
                            endpoint.health()
                if args.save is not None:
                    save_config(config, args.save)
                    report["saved_config"] = str(args.save.resolve())
        print(json.dumps(report, ensure_ascii=False))
        return 0
    except KeyboardInterrupt:
        print("embedding 已停止。", file=sys.stderr)
        return 130
    except (RuntimeFailure, OSError, URLError, subprocess.SubprocessError) as error:
        if isinstance(error, RuntimeFailure):
            message = str(error)
        elif isinstance(error, HTTPError):
            message = f"embedding 接口返回 HTTP {error.code}。"
        else:
            message = "文件、运行时或服务访问失败；检查路径、权限、依赖与服务状态。"
        print(message, file=sys.stderr)
        return 1
    except (ValueError, TypeError, KeyError, AttributeError, IndexError):
        print("embedding 服务响应格式无效；未保存配置。", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
