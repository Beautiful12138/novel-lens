"""真实 HTTP 与 MCP 上验证阅读投影、原文保真、范围重建和跨格式续读。"""

import asyncio
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from mcp import Client
from part_fixtures import http_file, http_import
from test_import_read import sample
from test_live_http import running_server
from test_mcp_http import call


def test_compact_reading_roundtrip(postgres_url: str, tmp_path: Path) -> None:
    data = sample()

    async def exercise(rest: httpx.Client) -> None:
        work = http_import(
            rest, files={"file": ("sample.txt", data)}, data={"request_id": str(uuid4())}
        ).json()["work"]["id"]
        sections = rest.get(f"/works/{work}/sections").json()["items"]
        section = sections[0]["id"]
        scope = {"work_id": work, "section_id": section}
        path = f"/works/{work}/sections/{section}/paragraphs"
        full = rest.get(path, params={"format": "full"}).json()
        original = full["items"]
        expected = [{k: p[k] for k in ("id", "ordinal", "text")} for p in original]
        source_range = scope | {
            "start_paragraph_id": original[0]["id"],
            "end_paragraph_id": original[-1]["id"],
        }

        async with Client(str(rest.base_url).rstrip("/") + "/mcp") as mcp:
            # 默认精简；需要字节位置时显式 full，两入口返回相同投影。
            assert await call(mcp, "source_paragraphs", scope | {"format": "full"}) == full
            assert rest.get(path, params={"format": "full"}).json() == full
            compact = await call(mcp, "source_paragraphs", scope | {"format": "compact"})
            assert compact == rest.get(path, params={"format": "compact"}).json()
            assert compact == rest.get(path).json()
            assert compact == await call(mcp, "source_paragraphs", scope)
            assert compact["items"] == expected
            assert compact["work_id"] == work and compact["section_id"] == section
            assert scope | compact["actual_range"] == source_range
            assert compact["next_cursor"] is None
            assert len(json.dumps(compact)) < len(json.dumps(full))
            for p in original:
                pos = p["source_position"]
                assert data[pos["start_byte"] : pos["end_byte"]].decode() == p["text"]

            first = await call(mcp, "source_paragraphs", scope | {"limit": 1, "format": "compact"})
            tail = rest.get(path, params={"cursor": first["next_cursor"], "format": "full"}).json()
            assert tail["items"] == original[1:]
            full_first = rest.get(path, params={"limit": 1, "format": "full"}).json()
            compact_tail = await call(
                mcp,
                "source_paragraphs",
                scope | {"cursor": full_first["next_cursor"], "format": "compact"},
            )
            assert compact_tail["items"] == expected[1:]
            assert compact_tail["next_cursor"] is None

            # 重建实际范围后只读回该页，而非误把请求范围视为全部已读。
            read_path = f"/works/{work}/source/read"
            request = {"source_range": source_range, "limit": 1, "format": "compact"}
            read = await call(mcp, "source_read", request)
            assert rest.post(read_path, json=request).json() == read
            assert read["items"] == expected[:1]
            assert scope | read["requested_range"] == source_range
            actual = scope | read["actual_range"]
            reread = await call(mcp, "source_read", {"source_range": actual, "format": "full"})
            assert reread["items"] == original[:1] and reread["next_cursor"] is None
            tail_request = {
                "source_range": source_range,
                "cursor": read["next_cursor"],
                "format": "full",
            }
            assert rest.post(read_path, json=tail_request).json()["items"] == original[1:]
            full_read = await call(
                mcp, "source_read", {"source_range": source_range, "limit": 1, "format": "full"}
            )
            tail_request.update(cursor=full_read["next_cursor"], format="compact")
            assert (await call(mcp, "source_read", tail_request))["items"] == expected[1:]

            context_path = f"/works/{work}/source/context"
            anchor = {"section_id": section, "paragraph_id": original[1]["id"]}
            for before, after in [(0, 0), (100, 100), (1, 0), (0, 1)]:
                context_request = anchor | {"before": before, "after": after}
                whole = rest.post(context_path, json=context_request | {"format": "full"}).json()
                default = rest.post(context_path, json=context_request).json()
                context_request["format"] = "compact"
                context = await call(mcp, "source_get_context", {"work_id": work} | context_request)
                assert rest.post(context_path, json=context_request).json() == context
                assert default == context
                assert (
                    await call(
                        mcp,
                        "source_get_context",
                        {"work_id": work} | anchor | {"before": before, "after": after},
                    )
                    == context
                )
                assert context["items"] == [
                    {k: p[k] for k in ("id", "ordinal", "text")} for p in whole["items"]
                ]
                assert scope | context["actual_range"] == whole["actual_range"]
                assert context["at_section_start"] == whole["at_section_start"]
                assert context["at_section_end"] == whole["at_section_end"]

            empty_args = {"work_id": work, "section_id": sections[1]["id"], "format": "compact"}
            empty = await call(mcp, "source_paragraphs", empty_args)
            assert empty["items"] == [] and empty["actual_range"] is None
            assert empty["next_cursor"] is None
            assert (
                rest.get(
                    f"/works/{work}/sections/{sections[1]['id']}/paragraphs",
                    params={"format": "compact"},
                ).json()
                == empty
            )

            # 精简模式不能绕过归属、游标、范围和输入校验。
            other = http_import(
                rest, files={"file": ("other.txt", sample())}, data={"request_id": str(uuid4())}
            ).json()["work"]["id"]
            await call(
                mcp,
                "source_paragraphs",
                scope | {"work_id": other, "format": "compact"},
                code="SECTION_NOT_FOUND",
            )
            await call(
                mcp,
                "source_read",
                {
                    "source_range": source_range | {"section_id": sections[2]["id"]},
                    "format": "compact",
                },
                code="PARAGRAPH_NOT_FOUND",
            )
            await call(
                mcp,
                "source_read",
                {
                    "source_range": source_range
                    | {
                        "start_paragraph_id": original[-1]["id"],
                        "end_paragraph_id": original[0]["id"],
                    },
                    "format": "compact",
                },
                code="INVALID_RANGE",
            )
            await call(
                mcp,
                "source_read",
                request | {"cursor": first["next_cursor"]},
                code="INVALID_CURSOR",
            )
            invalid_requests: list[tuple[str, dict[str, Any]]] = [
                ("source_paragraphs", scope),
                ("source_read", {"source_range": source_range}),
                ("source_get_context", {"work_id": work} | anchor),
            ]
            for tool, arguments in invalid_requests:
                await call(mcp, tool, arguments | {"format": "unknown"}, code="INVALID_INPUT")
            assert rest.get(path, params={"format": "unknown"}).status_code == 422
            assert rest.post(read_path, json=request | {"format": "unknown"}).status_code == 422
            assert rest.post(context_path, json=anchor | {"format": "unknown"}).status_code == 422
            assert http_file(rest, work).content == data

    with running_server(postgres_url, tmp_path, "compact") as rest:
        asyncio.run(exercise(rest))
