"""通过官方客户端与真实进程验收分析资产工具、错误及持久化恢复。"""

import asyncio
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from mcp import Client
from test_live_http import running_server
from test_mcp_http import call


def test_asset_tools_roundtrip_restart_and_errors(postgres_url: str, tmp_path: Path) -> None:
    data = f"书名：{uuid4()}\n标题：章一\n首段\n次段\n标题：章二\n尾段".encode()
    novel = tmp_path / "临时样例.txt"
    novel.write_bytes(data)
    namespace = uuid4().hex
    secret_note = "private-analysis-note\n" + "写法说明。" * 60

    async def exercise(rest: httpx.Client) -> dict[str, Any]:
        async with Client(str(rest.base_url).rstrip("/") + "/mcp") as mcp:
            tools = (await mcp.list_tools()).tools
            assert len(tools) == 48
            assert all(t.output_schema for t in tools)
            by_name = {t.name: t for t in tools}
            assert by_name["annotation_update"].annotations.destructive_hint  # type: ignore[union-attr]
            work = (
                await call(
                    mcp, "work_import", {"file_path": str(novel), "request_id": str(uuid4())}
                )
            )["work"]
            sections = (await call(mcp, "source_sections", {"work_id": work["id"]}))["items"]
            ranges = []
            for section in sections:
                paragraphs = (
                    await call(
                        mcp,
                        "source_paragraphs",
                        {"work_id": work["id"], "section_id": section["id"]},
                    )
                )["items"]
                ranges.append(
                    dict(
                        work_id=work["id"],
                        section_id=section["id"],
                        start_paragraph_id=paragraphs[0]["id"],
                        end_paragraph_id=paragraphs[-1]["id"],
                    )
                )
            assert (await call(mcp, "tag_search", {"namespace": namespace, "query": "别名"}))[
                "items"
            ] == []
            tag_args = dict(
                request_id=str(uuid4()),
                namespace=namespace,
                name="机制",
                description="可复用的定义",
                aliases=["别名乙", "别名甲", "别名甲"],
            )
            tag = (await call(mcp, "tag_create", tag_args))["result"]
            assert (await call(mcp, "tag_create", tag_args | {"aliases": ["别名甲", "别名乙"]}))[
                "replayed"
            ]
            assert await call(mcp, "tag_get", {"tag_id": tag["id"]}) == tag
            assert (await call(mcp, "tag_list", {"namespace": namespace}))["items"] == [tag]
            assert (await call(mcp, "tag_search", {"namespace": namespace, "query": "别名甲"}))[
                "items"
            ] == [tag]
            await call(
                mcp, "tag_create", tag_args | {"request_id": str(uuid4())}, code="TAG_NAME_CONFLICT"
            )
            create = dict(
                request_id=str(uuid4()),
                work_id=work["id"],
                source_ranges=ranges,
                tag_ids=[tag["id"]],
                note=secret_note,
            )
            created = await call(mcp, "annotation_create", create)
            annotation = created["result"]
            identifier = dict(work_id=work["id"], annotation_id=annotation["id"])
            assert await call(mcp, "annotation_get", identifier | {"format": "full"}) == annotation
            summary = (
                await call(
                    mcp,
                    "annotation_list",
                    {"work_id": work["id"], "tag_ids": [tag["id"]], "source_range": ranges[1]},
                )
            )["items"][0]
            assert summary["note_truncated"] and summary["note_preview"] == secret_note[:200]
            assert summary["source_range_count"] == 2
            raw = await call(mcp, "source_read", {"source_range": ranges[0]})
            assert [p["text"] for p in raw["items"]] == ["首段", "次段"]
            update = identifier | dict(
                request_id=str(uuid4()),
                expected_version=1,
                source_ranges=[ranges[1]],
                tag_ids=[],
                note=None,
            )
            changed = (await call(mcp, "annotation_update", update))["result"]
            assert changed["version"] == 2 and changed["note"] is None and changed["tag_ids"] == []
            assert (await call(mcp, "annotation_update", update))["result"] == changed
            await call(
                mcp,
                "annotation_update",
                update | {"request_id": str(uuid4())},
                code="VERSION_CONFLICT",
            )
            await call(
                mcp,
                "annotation_get",
                identifier | {"work_id": str(uuid4())},
                code="ANNOTATION_NOT_FOUND",
            )
            await call(mcp, "tag_get", {"tag_id": str(uuid4())}, code="TAG_NOT_FOUND")
            await call(
                mcp, "asset_write_get", {"request_id": str(uuid4())}, code="WRITE_NOT_COMMITTED"
            )
            invalids = [
                create | {"source_ranges": []},
                create | {"source_ranges": [ranges[0], ranges[0]]},
                create | {"note": "\x00"},
                create | {"note": "　 \n"},
                create | {"private-unknown": "private-unknown"},
            ]
            for invalid in invalids:
                await call(mcp, "annotation_create", invalid, code="INVALID_INPUT")
            await call(
                mcp, "annotation_update", update | {"expected_version": True}, code="INVALID_INPUT"
            )
            await call(
                mcp,
                "annotation_update",
                {k: v for k, v in update.items() if k != "note"},
                code="INVALID_INPUT",
            )
            await call(
                mcp, "tag_create", tag_args | {"namespace": "illegal/path"}, code="INVALID_INPUT"
            )
            await call(mcp, "tag_search", {"query": " "}, code="INVALID_INPUT")
            await call(
                mcp, "annotation_list", {"work_id": work["id"], "limit": "1"}, code="INVALID_INPUT"
            )
            await call(
                mcp,
                "annotation_list",
                {"work_id": work["id"], "cursor": "bad"},
                code="INVALID_CURSOR",
            )
            large_namespace = uuid4().hex
            large = dict(namespace=large_namespace, name="大说明", description="a" * (1024 * 1024))
            too_large = large | {"request_id": str(uuid4())}
            await call(mcp, "tag_create", too_large, code="ASSET_TOO_LARGE")
            await call(
                mcp,
                "asset_write_get",
                {"request_id": too_large["request_id"]},
                code="WRITE_NOT_COMMITTED",
            )
            # 单条可恢复，整页过大必须拒绝并允许缩小分页。
            for name in ["一", "二"]:
                await call(
                    mcp,
                    "tag_create",
                    large | dict(request_id=str(uuid4()), name=name, description="a" * 600000),
                )
            await call(mcp, "tag_list", {"namespace": large_namespace}, code="RESULT_TOO_LARGE")
            assert (
                len(
                    (await call(mcp, "tag_list", {"namespace": large_namespace, "limit": 1}))[
                        "items"
                    ]
                )
                == 1
            )
            assert rest.get(f"/works/{work['id']}/file").content == data
            return dict(
                create=create,
                annotation=annotation,
                update=update,
                changed=changed,
                identifier=identifier,
                tag=tag,
            )

    with running_server(postgres_url, tmp_path, "assets") as rest:
        saved = asyncio.run(exercise(rest))

    async def recover(rest: httpx.Client) -> None:
        async with Client(str(rest.base_url).rstrip("/") + "/mcp") as mcp:
            assert (
                await call(mcp, "asset_write_get", {"request_id": saved["create"]["request_id"]})
            )["result"] == saved["annotation"]
            assert (await call(mcp, "annotation_create", saved["create"]))["result"] == saved[
                "annotation"
            ]
            assert (await call(mcp, "annotation_update", saved["update"]))["result"] == saved[
                "changed"
            ]
            assert (
                await call(mcp, "annotation_get", saved["identifier"] | {"format": "full"})
                == saved["changed"]
            )
            assert await call(mcp, "tag_get", {"tag_id": saved["tag"]["id"]}) == saved["tag"]

    with running_server(postgres_url, tmp_path, "assets-restart") as rest:
        asyncio.run(recover(rest))
    for label in ["assets", "assets-restart"]:
        assert (tmp_path / f"{label}.stdout").read_bytes() == b""
        log = (tmp_path / f"{label}.stderr").read_text(encoding="utf-8")
        assert "private-analysis-note" not in log and "private-unknown" not in log
        assert "INSERT INTO" not in log and "UPDATE annotations" not in log
    assert novel.read_bytes() == data
