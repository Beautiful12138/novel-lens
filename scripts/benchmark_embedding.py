"""对已启动的本机 llama-server 执行中文冒烟检索与长度耗时测试。

仅发送仓库内自编样例，不读取小说或数据库，不启动服务或安装模型。
报告包含候选 ID、排名、token 数和耗时，不保存向量或完整请求体。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


class Endpoint:
    """绕过代理访问测试服务，统一验证返回向量的维度与归一化。"""

    def __init__(self, url: str, dimensions: int) -> None:
        self.url = url.rstrip("/")
        self.dimensions = dimensions
        self.opener = build_opener(ProxyHandler({}))

    def post(self, route: str, body: dict[str, Any]) -> Any:
        request = Request(
            self.url + route,
            data=json.dumps(body, ensure_ascii=False).encode(),
            headers={"Content-Type": "application/json"},
        )
        with self.opener.open(request, timeout=180) as response:
            return json.load(response)

    def embed(self, texts: list[str]) -> tuple[list[list[float]], float, int]:
        started = time.perf_counter()
        result = self.post("/v1/embeddings", {"input": texts, "encoding_format": "float"})
        elapsed = time.perf_counter() - started
        rows = sorted(result["data"], key=lambda row: row["index"])
        if [row["index"] for row in rows] != list(range(len(texts))):
            raise ValueError("向量响应条目与输入不对应")
        vectors = [row["embedding"] for row in rows]
        for vector in vectors:
            if len(vector) != self.dimensions or not all(math.isfinite(x) for x in vector):
                raise ValueError("向量维度或数值无效")
            if not math.isclose(sum(x * x for x in vector), 1.0, abs_tol=1e-3):
                raise ValueError("向量未按 L2 归一化")
        return vectors, elapsed, result.get("usage", {}).get("prompt_tokens", 0)


def main() -> None:
    """每次执行固定样例与长度阶梯；测量是实机快照，不是吞吐承诺。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:18081")
    parser.add_argument("--dimensions", type=int, default=1024)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--lengths", type=int, nargs="+", default=[256, 512, 1024, 2048])
    args = parser.parse_args()
    fixture = ROOT / "tests/fixtures/embedding_cases.json"
    cases = json.loads(fixture.read_text(encoding="utf-8"))
    endpoint = Endpoint(args.url, args.dimensions)
    instruction = "Given a Chinese fiction writing query, retrieve relevant passages from novels"
    prompt = f"Instruct: {instruction}\nQuery: "
    report: dict[str, Any] = {
        "label": args.label,
        "fixture_sha256": hashlib.sha256(fixture.read_bytes()).hexdigest(),
        "query_instruction": instruction,
        "dimensions": args.dimensions,
        "limitations": "自编样例仅验证基本召回；性能文本由样例拼接，不代表全书分布。",
    }
    endpoint.embed(["预热用的短句。"])
    documents = []
    document_seconds = []
    for passage in cases["passages"]:
        vectors, elapsed, _ = endpoint.embed([passage["text"]])
        documents.append(vectors[0])
        document_seconds.append(elapsed)
    query_seconds = []
    rankings = []
    for query in cases["queries"]:
        vectors, elapsed, tokens = endpoint.embed([prompt + query["text"]])
        query_seconds.append(elapsed)
        scores = [sum(a * b for a, b in zip(vectors[0], doc, strict=True)) for doc in documents]
        order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        top = [cases["passages"][i]["id"] for i in order]
        rank = min(top.index(item) + 1 for item in query["relevant"])
        rankings.append(
            {
                "id": query["id"],
                "kind": query.get("kind", "technique"),
                "rank": rank,
                "top3": top[:3],
                "tokens": tokens,
            }
        )
    report.update(
        rankings=rankings,
        top1=sum(row["rank"] == 1 for row in rankings),
        top3=sum(row["rank"] <= 3 for row in rankings),
        queries=len(rankings),
        query_seconds_median=statistics.median(query_seconds),
        query_seconds_max=max(query_seconds),
        document_seconds_median=statistics.median(document_seconds),
    )
    report["query_groups"] = {}
    for kind in sorted({row["kind"] for row in rankings}):
        group = [row for row in rankings if row["kind"] == kind]
        report["query_groups"][kind] = {
            "count": len(group),
            "top1": sum(row["rank"] == 1 for row in group),
            "top3": sum(row["rank"] <= 3 for row in group),
        }
    # 输入为确定性的中文样例拼接，按服务 tokenizer 裁到测试 token 长度。
    pool = "\n".join(item["text"] for item in cases["passages"]) * 20
    tokens = endpoint.post("/tokenize", {"content": pool, "add_special": False})["tokens"]
    if len(tokens) < max(args.lengths):
        raise ValueError("性能样例长度不足")
    timings = []
    for length in args.lengths:
        text = endpoint.post("/detokenize", {"tokens": tokens[:length]})["content"]
        samples = []
        for _ in range(3):
            # 随机前缀避免重复输入或长度阶梯共用 KV 前缀缓存，测实际新文本处理。
            _, elapsed, actual_tokens = endpoint.embed([uuid4().hex + "\n" + text])
            samples.append(elapsed)
        timings.append(
            {
                "requested_tokens": length,
                "actual_tokens": actual_tokens,
                "seconds": samples,
                "median_seconds": statistics.median(samples),
            }
        )
        print(f"完成 {length} token 测试", flush=True)
    report["length_timings"] = timings
    report["timing_method"] = "每次性能请求增加不同 UUID 前缀；actual_tokens 包含前缀和特殊 token。"
    batch_text = [item["text"] for item in cases["passages"][:4]]
    batch_vectors, elapsed, actual_tokens = endpoint.embed(batch_text)
    report["batch4"] = {
        "seconds": elapsed,
        "actual_tokens": actual_tokens,
        "min_cosine_to_single": min(
            sum(a * b for a, b in zip(documents[i], v, strict=True))
            for i, v in enumerate(batch_vectors)
        ),
    }
    # 记录服务对超长输入的真实行为，不能默认把其视为拒绝或无截断。
    try:
        _, elapsed, actual_tokens = endpoint.embed([pool])
        report["oversize"] = {
            "status": "accepted",
            "actual_tokens": actual_tokens,
            "tokenizer_tokens": len(tokens),
            "seconds": elapsed,
        }
    except HTTPError as error:
        report["oversize"] = {
            "status": "rejected",
            "http_status": error.code,
            "tokenizer_tokens": len(tokens),
        }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                key: report[key]
                for key in ["label", "top1", "top3", "queries", "query_seconds_median", "oversize"]
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
