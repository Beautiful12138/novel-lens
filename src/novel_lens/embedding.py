"""固定本机模型适配；字面分词、输入预算和响应核验共同定义向量空间。"""

import hashlib
import json
import math
import threading
from pathlib import Path
from typing import Any
from urllib.error import HTTPError

from novel_lens.embedding_runtime import (
    DIMENSIONS,
    MODEL_ID,
    MODEL_SHA256,
    RUNTIME_COMMIT,
    Endpoint,
    RuntimeFailure,
    check_artifacts,
    read_config,
)
from novel_lens.errors import ServiceError

MAX_TOKENS = 4095
TARGET_TOKENS = 1024
OVERLAP_TOKENS = 128
MAX_PARAGRAPH_BYTES = 1024 * 1024
QUERY_PREFIX = (
    "Instruct: Given a Chinese fiction writing query, retrieve relevant passages from novels\n"
    "Query: "
)
CONTRACT = {
    "model": "Qwen/Qwen3-Embedding-0.6B-GGUF",
    "revision": "370f27d7550e0def9b39c1f16d3fbaa13aa67728",
    "gguf_sha256": MODEL_SHA256,
    "tokenizer_sha256": MODEL_SHA256,
    "quantization": "Q8_0",
    "runtime": "llama.cpp-b10964-" + RUNTIME_COMMIT,
    "dimensions": DIMENSIONS,
    "pooling": "last",
    "normalize": "L2",
    "query_prefix": QUERY_PREFIX,
    "join": "\n",
    "add_special": True,
    "parse_special": False,
    "strategy": "paragraph-window-v1",
    "target_tokens": TARGET_TOKENS,
    "overlap_tokens": OVERLAP_TOKENS,
    "max_tokens": MAX_TOKENS,
    "max_paragraph_bytes": MAX_PARAGRAPH_BYTES,
}


def digest(value: Any) -> str:
    """规范化 JSON 的摘要用于契约和请求重放，不记录原始参数。"""
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


CONTRACT_ID = digest(CONTRACT)


def invalid_response() -> ServiceError:
    return ServiceError("EMBEDDING_INVALID_RESPONSE", "模型响应不符合向量与 token 契约", 502)


class EmbeddingClient:
    """延迟核验本机产物；不启动服务，不下载模型，不影响普通读写的可用性。

    产物校验缓存仅用于本进程内同配置、同文件元信息；配置或产物改变后重新校验。
    固定本机运行入口是信任前提，别名和 props 不用于证明第三方服务的权重。
    """

    contract_id = CONTRACT_ID

    def __init__(self, config_path: Path | None) -> None:
        self.config_path = config_path
        self._verified: tuple[Any, ...] | None = None
        self._guard = threading.Lock()
        self.endpoint: Endpoint | None = None

    def verify(self) -> None:
        """每次业务调用核对在线身份、上下文；首次或文件变化时重新核验本机产物。"""
        if self.config_path is None:
            raise ServiceError(
                "EMBEDDING_NOT_CONFIGURED", "请配置 NOVEL_LENS_EMBEDDING_CONFIG", 503
            )
        try:
            with self._guard:
                config = read_config(self.config_path)
                if config.context_tokens != 4096 or config.backend != "cpu":
                    raise RuntimeFailure("语义索引要求 4096 上下文及已验证的 CPU 后端")
                identity: tuple[Any, ...] = (config.model_dump_json(),)
                for name in (config.model_path, config.executable):
                    stat = Path(name).stat()
                    identity += (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
                if identity != self._verified:
                    check_artifacts(config)
                    self._verified = identity
                self.endpoint = Endpoint(config.port)
        except (RuntimeFailure, OSError, ValueError) as exc:
            raise ServiceError(
                "EMBEDDING_CONTRACT_MISMATCH", "本机模型配置或产物与已验证契约不兼容", 409
            ) from exc
        health = self._request("/health")
        models = self._request("/v1/models")
        props = self._request("/props")
        try:
            if health["status"] != "ok":
                raise ServiceError("EMBEDDING_UNAVAILABLE", "模型服务未就绪", 503)
            if (
                not any(row["id"] == MODEL_ID for row in models["data"])
                or props["default_generation_settings"]["n_ctx"] != 4096
            ):
                raise ServiceError("EMBEDDING_CONTRACT_MISMATCH", "在线模型或上下文不匹配", 409)
        except (KeyError, TypeError):
            raise invalid_response() from None

    def _request(self, route: str, body: dict[str, Any] | None = None) -> Any:
        if self.endpoint is None:
            raise ServiceError("EMBEDDING_UNAVAILABLE", "模型连接尚未核验", 503)
        try:
            return self.endpoint.request(route, body)
        except HTTPError as exc:
            if 400 <= exc.code < 500:
                raise invalid_response() from None
            raise ServiceError("EMBEDDING_UNAVAILABLE", "模型服务暂时不可用", 503) from None
        except (OSError, TimeoutError):
            raise ServiceError("EMBEDDING_UNAVAILABLE", "无法连接模型服务或调用超时", 503) from None
        except (ValueError, UnicodeError):
            raise invalid_response() from None

    def tokenize(self, value: str) -> list[int]:
        """最终拼接文本只做一次字面分词，包含模型自动添加的特殊 token。"""
        response = self._request(
            "/tokenize", {"content": value, "add_special": True, "parse_special": False}
        )
        tokens = response.get("tokens") if isinstance(response, dict) else None
        if (
            not isinstance(tokens, list)
            or not tokens
            or any(type(token) is not int or token < 0 for token in tokens)
        ):
            raise invalid_response()
        return tokens

    def embed(self, inputs: list[list[int]]) -> list[list[float]]:
        """只接收已计数的 ID 数组；先核验整个响应，调用方才可原子发布本批。"""
        total = sum(map(len, inputs))
        if not inputs or any(not row for row in inputs) or total > MAX_TOKENS:
            raise ValueError("调用方必须遵守批次 token 预算")
        response = self._request("/v1/embeddings", {"model": MODEL_ID, "input": inputs})
        try:
            rows = response["data"]
            usage = response["usage"]["prompt_tokens"]
            if len(rows) != len(inputs) or type(usage) is not int or usage != total:
                raise ValueError
            vectors = []
            for index, row in enumerate(rows):
                values = row["embedding"]
                if type(row["index"]) is not int or row["index"] != index:
                    raise ValueError
                if len(values) != DIMENSIONS or not all(
                    type(v) in (int, float) and math.isfinite(v) for v in values
                ):
                    raise ValueError
                if abs(math.sqrt(sum(v * v for v in values)) - 1) > 1e-3:
                    raise ValueError
                vectors.append([float(v) for v in values])
            return vectors
        except (KeyError, TypeError, ValueError, OverflowError):
            raise invalid_response() from None
