"""真实客户端验证纠错工具发现、状态查询和跨入口关键词过滤。"""

import asyncio
from pathlib import Path
from uuid import uuid4

import httpx
from mcp import Client
from test_live_http import running_server
from test_mcp_http import call


def test_corrections_mcp(postgres_url: str, tmp_path: Path) -> None:
    async def exercise(rest: httpx.Client) -> None:
        work = rest.post(
            "/work-imports",
            files={"file": ("book.txt", f"书名：{uuid4()}\n标题：一\n测试原文".encode())},
            data={"request_id": str(uuid4())},
        ).json()["work"]["id"]
        async with Client(str(rest.base_url).rstrip("/") + "/mcp") as mcp:
            listing = {t.name: t for t in (await mcp.list_tools()).tools}
            assert {"tag_update", "annotation_set_status"} <= listing.keys()
            for name in ["tag_update", "annotation_set_status"]:
                hints = listing[name].annotations
                assert hints and hints.destructive_hint and not hints.read_only_hint
            sections = (await call(mcp, "source_sections", {"work_id": work}))["items"]
            page = await call(
                mcp,
                "source_paragraphs",
                {"work_id": work, "section_id": sections[0]["id"], "format": "compact"},
            )
            ref = {k: page[k] for k in ("work_id", "section_id")} | page["actual_range"]
            tag = (
                await call(
                    mcp,
                    "tag_create",
                    dict(
                        request_id=str(uuid4()),
                        namespace=uuid4().hex,
                        name="旧名",
                        description="旧定义",
                    ),
                )
            )["result"]
            change = dict(
                request_id=str(uuid4()),
                tag_id=tag["id"],
                expected_version=tag["version"],
                name="新名",
                description="新定义",
                aliases=["别名"],
            )
            updated = (await call(mcp, "tag_update", change))["result"]
            assert updated["version"] == 2 and updated["full_name"].endswith("/新名")
            assert (await call(mcp, "tag_update", change))["replayed"]
            await call(mcp, "tag_update", change | {"namespace": "other"}, code="INVALID_INPUT")
            a = (
                await call(
                    mcp,
                    "annotation_create",
                    dict(
                        request_id=str(uuid4()),
                        work_id=work,
                        source_ranges=[ref],
                        tag_ids=[tag["id"]],
                        note="纠错测试说明",
                    ),
                )
            )["result"]
            assert a["status"] == "active"
            identify = dict(work_id=work, annotation_id=a["id"])
            revoke = identify | dict(
                request_id=str(uuid4()), expected_version=1, status="withdrawn"
            )
            withdrawn = (await call(mcp, "annotation_set_status", revoke))["result"]
            assert withdrawn["version"] == 2 and withdrawn["source_ranges"] == [ref]
            assert (await call(mcp, "annotation_set_status", revoke))["replayed"]
            assert not (await call(mcp, "annotation_list", {"work_id": work}))["items"]
            q = {"work_id": work, "terms": ["纠错"]}
            assert not (await call(mcp, "annotation_search", q))["items"]
            for state in ["withdrawn", None]:
                query = q | {"status": state}
                found = await call(mcp, "annotation_search", query)
                assert found == rest.post("/annotations/search", json=query).json()
                assert found["items"][0]["annotation_id"] == a["id"]
            edited = (
                await call(
                    mcp,
                    "annotation_update",
                    identify
                    | dict(
                        request_id=str(uuid4()),
                        expected_version=2,
                        source_ranges=[ref],
                        tag_ids=[tag["id"]],
                        note="纠错修订说明",
                    ),
                )
            )["result"]
            assert edited["status"] == "withdrawn"
            await call(
                mcp,
                "annotation_set_status",
                revoke | {"request_id": str(uuid4()), "status": "active"},
                code="VERSION_CONFLICT",
            )
            restored = (
                await call(
                    mcp,
                    "annotation_set_status",
                    identify | dict(request_id=str(uuid4()), expected_version=3, status="active"),
                )
            )["result"]
            assert restored["version"] == 4
            assert (await call(mcp, "annotation_search", q))["items"][0]["status"] == "active"
            assert (await call(mcp, "asset_write_get", {"request_id": revoke["request_id"]}))[
                "result"
            ] == withdrawn
            assert await call(mcp, "annotation_get", identify | {"format": "full"}) == restored
            assert (await call(mcp, "source_read", {"source_range": ref}))["items"][0][
                "text"
            ] == "测试原文"

    with running_server(postgres_url, tmp_path, "corrections") as rest:
        asyncio.run(exercise(rest))
