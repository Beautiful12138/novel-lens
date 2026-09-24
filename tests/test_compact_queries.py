"""真实协议验证默认精简、完整内容保留、坐标重建、跨格式分页和写入隔离。"""

import asyncio
import json
from pathlib import Path
from uuid import uuid4

import httpx
from mcp import Client
from test_live_http import running_server
from test_mcp_http import call


def test_compact_queries(postgres_url: str, tmp_path: Path) -> None:
    async def exercise(rest: httpx.Client) -> None:
        data = f"书名：{uuid4()}\n标题：甲\n{'晨光' * 150}\n标题：乙\n晨光照进来。".encode()
        work = rest.post(
            "/work-imports",
            files={"file": ("book.txt", data)},
            data={"request_id": str(uuid4())},
        ).json()["work"]["id"]
        async with Client(str(rest.base_url).rstrip("/") + "/mcp") as mcp:
            query = {"work_id": work, "terms": ["晨光"], "limit": 1}
            default = await call(mcp, "source_search", query)
            assert default == rest.post("/source/search", json=query).json()
            full = await call(mcp, "source_search", query | {"format": "full"})
            assert full == rest.post("/source/search", json=query | {"format": "full"}).json()
            assert len(json.dumps(default)) < len(json.dumps(full))
            hit, original = default["items"][0], full["items"][0]
            assert hit["excerpt"] == {
                k: v for k, v in original["excerpt"].items() if k != "character_start"
            }
            assert hit["excerpt"]["truncated_after"]
            ref = {"work_id": default["work_id"]} | hit["source_range"]
            assert ref == original["source_range"]
            read = await call(mcp, "source_read", {"source_range": ref})
            assert read["items"][0]["text"] == "晨光" * 150
            tail = await call(
                mcp, "source_search", query | {"cursor": default["next_cursor"], "format": "full"}
            )
            assert tail["items"][0]["section_title"] == "乙" and tail["next_cursor"] is None
            refs = [ref, tail["items"][0]["source_range"]]
            note = "晨光中的对白衔接\n" + "完整说明保持原样。" * 60
            created = []
            for _ in range(2):
                result = await call(
                    mcp,
                    "annotation_create",
                    {
                        "request_id": str(uuid4()),
                        "work_id": work,
                        "source_ranges": refs,
                        "note": note,
                    },
                )
                created.append(result["result"])
                assert "created_at" in result["result"]
                assert (await call(mcp, "asset_write_get", {"request_id": result["request_id"]}))[
                    "result"
                ] == result["result"]
            identify = {"work_id": work, "annotation_id": created[0]["id"]}
            detail = await call(mcp, "annotation_get", identify)
            whole = await call(mcp, "annotation_get", identify | {"format": "full"})
            assert whole == created[0]
            assert detail["note"] == note
            assert [{"work_id": detail["work_id"]} | r for r in detail["source_ranges"]] == refs
            assert "created_at" not in detail and "updated_at" not in detail
            for field in ["id", "work_id", "version", "status", "tag_ids", "entity_ids"]:
                assert detail[field] == whole[field]
            for tool, params in [
                ("annotation_list", {"work_id": work, "limit": 1}),
                ("annotation_search", query),
            ]:
                small = await call(mcp, tool, params)
                large = await call(mcp, tool, params | {"format": "full"})
                assert small["next_cursor"] == large["next_cursor"]
                assert {"work_id": small["work_id"]} | small["items"][0][
                    "first_source_range"
                ] == refs[0]
                following = await call(
                    mcp, tool, params | {"format": "full", "cursor": small["next_cursor"]}
                )
                key = "id" if tool == "annotation_list" else "annotation_id"
                assert following["items"][0][key] == created[1]["id"]
                assert following["next_cursor"] is None
                if tool == "annotation_list":
                    assert small["items"][0]["note_preview"] == note[:200]
                    assert small["items"][0]["note_truncated"]
                else:
                    assert small == rest.post("/annotations/search", json=params).json()
                    assert (
                        small["items"][0]["excerpt"]["text"] == large["items"][0]["excerpt"]["text"]
                    )
            for tool, params in [
                ("source_search", query),
                ("annotation_search", query),
                ("annotation_list", {"work_id": work}),
                ("annotation_get", identify),
            ]:
                await call(mcp, tool, params | {"format": "unknown"}, code="INVALID_INPUT")
            for path in ["/source/search", "/annotations/search"]:
                assert rest.post(path, json=query | {"format": "unknown"}).status_code == 422
            await call(
                mcp,
                "annotation_set_status",
                identify
                | {
                    "request_id": str(uuid4()),
                    "expected_version": 1,
                    "status": "withdrawn",
                    "format": "compact",
                },
                code="INVALID_INPUT",
            )
            withdrawn = await call(
                mcp,
                "annotation_set_status",
                identify
                | {
                    "request_id": str(uuid4()),
                    "expected_version": 1,
                    "status": "withdrawn",
                },
            )
            assert withdrawn["result"]["source_ranges"] == refs
            assert (await call(mcp, "annotation_get", identify))["status"] == "withdrawn"
            assert [
                v["annotation_id"] for v in (await call(mcp, "annotation_search", query))["items"]
            ] == [created[1]["id"]]
            assert rest.get(f"/works/{work}/file").content == data

    with running_server(postgres_url, tmp_path, "compact-queries") as rest:
        asyncio.run(exercise(rest))
