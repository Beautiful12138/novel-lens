"""固定查询的召回与完整回读记录，供人工对照；不自动判定文学质量。

默认使用工作区代码和本地配置；--base-url 可记录已运行版本的 HTTP 基线。
仅创建搜索快照，不修改作品或索引。输出含原文，应保存在忽略的 tmp/ 下。
"""

import argparse
import json
import statistics
import time
from pathlib import Path
from typing import Any
from urllib.request import ProxyHandler, Request, build_opener
from uuid import UUID

from novel_lens.config import Settings
from novel_lens.database import Database
from novel_lens.embedding import EmbeddingClient
from novel_lens.library import LibraryService
from novel_lens.reference import ReferenceService
from novel_lens.reference_contracts import ReferenceQuery, SourceRead


def main() -> None:
    """每个查询保存候选和全部返回范围，失败时保留已经完成的记录。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-id", type=UUID, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base-url", help="省略时直接调用工作区业务服务")
    parser.add_argument(
        "--cases",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "tests/fixtures/reference_queries.json",
    )
    args = parser.parse_args()
    if args.output.exists():
        parser.error("输出文件已存在，请另选路径以保留对照记录")
    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    settings = Settings()
    database = Database(settings)
    model = EmbeddingClient(settings.embedding_config)
    reference, library = ReferenceService(database, model), LibraryService(database, model)
    opener = build_opener(ProxyHandler({}))

    def call(route: str, body: dict[str, Any]) -> dict[str, Any]:
        if args.base_url:
            request = Request(
                args.base_url.rstrip("/") + route,
                json.dumps(body).encode(),
                {"Content-Type": "application/json"},
            )
            with opener.open(request, timeout=180) as response:
                return dict(json.load(response))
        if route == "/reference/query":
            return reference.query(ReferenceQuery.model_validate(body)).model_dump(mode="json")
        return library.read(SourceRead.model_validate(body)).model_dump(mode="json")

    report: dict[str, Any] = {"work_id": str(args.work_id), "cases": []}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        for case in cases:
            start = time.perf_counter()
            result = call(
                "/reference/query",
                {
                    "query": case["query"],
                    "scope": [{"work_id": str(args.work_id)}],
                    "limit": 8,
                    "format": "full",
                },
            )
            elapsed = time.perf_counter() - start
            for hit in result["items"]:
                hit["paragraphs"] = []
                read = hit["source_range"] | {"limit": 200}
                while True:
                    page = call("/source/read", read)
                    hit["paragraphs"].extend(page["items"])
                    if not page["next_cursor"]:
                        break
                    read["cursor"] = page["next_cursor"]
            report["cases"].append(case | {"seconds": elapsed, "result": result})
            args.output.write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"{case['id']}: {elapsed:.2f}s", flush=True)
        print(f"median: {statistics.median(c['seconds'] for c in report['cases']):.2f}s")
    finally:
        database.close()


if __name__ == "__main__":
    main()
