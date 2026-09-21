"""官方 MCP 客户端验证风格导航完整调用、证据回读和进程重启恢复。"""

import asyncio
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from mcp import Client
from test_live_http import running_server
from test_mcp_http import call


def test_style_guide_tools_and_restart(postgres_url: str, tmp_path: Path) -> None:
    async def exercise(rest: httpx.Client) -> dict[str, Any]:
        async with Client(str(rest.base_url).rstrip("/") + "/mcp") as mcp:
            tools = {t.name: t for t in (await mcp.list_tools()).tools}
            assert len(tools) == 42
            assert tools["style_guide_get"].annotations.read_only_hint  # type: ignore[union-attr]
            assert tools["style_guide_update"].annotations.destructive_hint  # type: ignore[union-attr]
            work = rest.post(
                "/work-imports",
                data={"request_id": str(uuid4())},
                files={
                    "file": (
                        "guide.txt",
                        f"书名：{uuid4()}\n标题：片段\nprivate-guide-source\n尾段".encode(),
                    ),
                },
            ).json()["work"]
            work_id = work["id"]
            section = (await call(mcp, "source_sections", {"work_id": work_id}))["items"][0]
            paragraphs = (
                await call(
                    mcp,
                    "source_paragraphs",
                    {
                        "work_id": work_id,
                        "section_id": section["id"],
                    },
                )
            )["items"]
            ref = dict(
                work_id=work_id,
                section_id=section["id"],
                start_paragraph_id=paragraphs[0]["id"],
                end_paragraph_id=paragraphs[-1]["id"],
            )
            key = {"work_id": work_id}
            await call(mcp, "style_guide_get", key, code="STYLE_GUIDE_NOT_FOUND")
            entry = dict(
                title="对白",
                kind="baseline",
                description="private-guide-description\n动作衔接。",
                applicability="只在样本范围内成立",
                source_ranges=[ref],
            )
            create = dict(
                request_id=str(uuid4()),
                work_id=work_id,
                scope_note="仅分析片段\n未覆盖全书",
                entries=[entry],
            )
            first = (await call(mcp, "style_guide_create", create))["result"]
            assert first["version"] == 1 and first["entries"] == [entry]
            assert await call(mcp, "style_guide_get", key) == first
            read = await call(
                mcp, "source_read", {"source_range": first["entries"][0]["source_ranges"][0]}
            )
            assert read["items"][0]["text"] == "private-guide-source"
            update = create | dict(
                request_id=str(uuid4()), expected_version=1, entries=[], scope_note="结论待重新核验"
            )
            latest = (await call(mcp, "style_guide_update", update))["result"]
            assert latest["version"] == 2 and latest["entries"] == []
            await call(
                mcp,
                "style_guide_update",
                update | {"request_id": str(uuid4())},
                code="VERSION_CONFLICT",
            )
            await call(
                mcp,
                "style_guide_create",
                create | {"request_id": str(uuid4())},
                code="STYLE_GUIDE_EXISTS",
            )
            await call(mcp, "style_guide_get", {"work_id": str(uuid4())}, code="WORK_NOT_FOUND")
            for bad in (
                entry | {"source_ranges": []},
                entry | {"source_ranges": [ref, ref]},
                entry | {"kind": "score"},
                entry | {"description": " \n"},
                entry | {"applicability": "private-invalid\x00"},
                entry | {"title": "首尾 "},
                entry | {"extra": "private-extra"},
                entry | {"source_ranges": [ref | {"extra": True}]},
            ):
                await call(
                    mcp, "style_guide_create", create | {"entries": [bad]}, code="INVALID_INPUT"
                )
            for bad_update in (
                update | {"expected_version": True},
                update | {"expected_version": "2"},
                update | {"scope_note": None},
                update | {"entries": None},
                {k: v for k, v in update.items() if k != "entries"},
                update | {"complete": True},
            ):
                await call(mcp, "style_guide_update", bad_update, code="INVALID_INPUT")
            await call(
                mcp,
                "style_guide_update",
                update
                | {
                    "request_id": str(uuid4()),
                    "expected_version": 2,
                    "scope_note": "大" * 350000,
                },
                code="ASSET_TOO_LARGE",
            )
            assert await call(mcp, "style_guide_get", key) == latest
            return dict(key=key, create=create, first=first, update=update, latest=latest)

    with running_server(postgres_url, tmp_path, "guide-first") as rest:
        saved = asyncio.run(exercise(rest))

    async def recover(rest: httpx.Client) -> None:
        async with Client(str(rest.base_url).rstrip("/") + "/mcp") as mcp:
            for operation, request, expected in (
                ("style_guide_create", saved["create"], saved["first"]),
                ("style_guide_update", saved["update"], saved["latest"]),
            ):
                result = await call(mcp, "asset_write_get", {"request_id": request["request_id"]})
                assert result["result"] == expected and result["replayed"]
                assert await call(mcp, operation, request) == result
            assert await call(mcp, "style_guide_get", saved["key"]) == saved["latest"]

    with running_server(postgres_url, tmp_path, "guide-second") as rest:
        asyncio.run(recover(rest))
    for label in ("guide-first", "guide-second"):
        assert (tmp_path / f"{label}.stdout").read_bytes() == b""
        logs = (tmp_path / f"{label}.stderr").read_text(encoding="utf-8")
        for private in (
            "private-guide-source",
            "private-guide-description",
            "private-invalid",
            "private-extra",
        ):
            assert private not in logs
