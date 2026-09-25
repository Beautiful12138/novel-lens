"""官方 MCP 客户端验证与 HTTP 共用的查询、错误和回读坐标。"""

import asyncio
from pathlib import Path
from uuid import uuid4

import httpx
from mcp import Client
from part_fixtures import http_import
from test_live_http import running_server
from test_mcp_http import call
from test_search import INVALID_SEARCH


def test_search_mcp_http_parity(postgres_url: str, tmp_path: Path) -> None:
    async def exercise(rest: httpx.Client) -> None:
        data = f"分部：{uuid4()}\n标题：甲\n张三望着月光\n月光很暗\n标题：乙\n月亮".encode()
        work = http_import(
            rest, files={"file": ("sample.txt", data)}, data={"request_id": str(uuid4())}
        ).json()["work"]
        arguments = dict(work_id=work["id"], terms=["月光"], limit=1)
        async with Client(str(rest.base_url).rstrip("/") + "/mcp") as mcp:
            source = await call(mcp, "source_search", arguments)
            assert source == rest.post("/source/search", json=arguments).json()
            ref = {"work_id": source["work_id"]} | source["items"][0]["source_range"]
            read = await call(mcp, "source_read", {"source_range": ref})
            assert read["items"][0]["text"] == "张三望着月光"
            created = await call(
                mcp,
                "annotation_create",
                dict(
                    request_id=str(uuid4()),
                    work_id=work["id"],
                    source_ranges=[ref],
                    note="以月光呈现留白",
                    tag_ids=[],
                ),
            )
            hits = await call(mcp, "annotation_search", arguments)
            assert hits == rest.post("/annotations/search", json=arguments).json()
            identifier = hits["items"][0]["annotation_id"]
            full = await call(
                mcp,
                "annotation_get",
                dict(work_id=work["id"], annotation_id=identifier, format="full"),
            )
            assert full == created["result"]
            for tool, path in [
                ("source_search", "/source/search"),
                ("annotation_search", "/annotations/search"),
            ]:
                for change in INVALID_SEARCH:
                    await call(mcp, tool, arguments | change, code="INVALID_INPUT")
                invalid_cursor = arguments | {"cursor": "bad"}
                error = await call(mcp, tool, invalid_cursor, code="INVALID_CURSOR")
                assert error == rest.post(path, json=invalid_cursor).json()
            resumed = arguments | {"cursor": source["next_cursor"]}
            assert (
                await call(mcp, "source_search", resumed)
                == rest.post("/source/search", json=resumed).json()
            )

    with running_server(postgres_url, tmp_path, "search") as rest:
        asyncio.run(exercise(rest))
    for log in tmp_path.glob("*.log"):
        assert "以月光呈现留白" not in log.read_text(encoding="utf-8")
