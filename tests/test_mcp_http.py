"""官方 SDK 通过真实 HTTP 调用 MCP；同进程 REST 与 MCP 交叉验证持久化。"""

import asyncio
import json
import os
import subprocess
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest
from mcp import Client
from mcp.shared.exceptions import MCPError
from part_fixtures import http_file, http_import, http_work, mcp_import
from test_live_http import running_server


async def call(
    client: Client, name: str, arguments: dict[str, Any], *, code: str | None = None
) -> dict[str, Any]:
    result = await client.call_tool(name, arguments)
    payload = result.structured_content
    assert isinstance(payload, dict)
    assert result.is_error == (code is not None), payload
    if code is not None:
        assert payload["code"] == code, payload
    assert json.loads(result.content[0].text) == payload  # type: ignore[union-attr]
    return payload


def test_real_mcp_roundtrip_and_cross_entry_retry(postgres_url: str, tmp_path: Path) -> None:
    source = tmp_path / "中文小说.txt"
    data = (
        f"\ufeff分部：{uuid4()}\r\n标题：重复\r\n　private-novel-body😀 \t\r"
        "第二段\n第三段\n标题：重复\n标题：尾声\n末段"
    ).encode()
    source.write_bytes(data)
    key = str(uuid4())

    async def exercise(rest: httpx.Client) -> dict[str, Any]:
        address = str(rest.base_url).rstrip("/") + "/mcp"
        async with Client(address) as mcp:
            validation_work = http_work(rest)
            listing = await mcp.list_tools()
            expected = {
                "prepare_import",
                "prepare_batch",
                "prepare_status",
                "prepare_finish",
                "prepare_cleanup",
                "prepare_receipt",
                "part_import_validate",
                "part_import",
                "part_import_get",
                "work_create",
                "work_update",
                "part_list",
                "part_get",
                "part_update",
                "work_list",
                "work_get",
                "source_sections",
                "source_paragraphs",
                "source_read",
                "source_search",
                "annotation_search",
                "semantic_index_create",
                "semantic_index_build",
                "semantic_index_get",
                "source_semantic_search",
                "source_get_context",
                "tag_create",
                "tag_update",
                "annotation_set_status",
                "tag_get",
                "tag_list",
                "tag_search",
                "annotation_create",
                "annotation_update",
                "annotation_get",
                "annotation_list",
                "asset_write_get",
                "entity_create",
                "entity_update",
                "entity_get",
                "entity_list",
                "entity_search",
                "relation_create",
                "relation_update",
                "relation_get",
                "relation_search",
                "relation_expand",
                "relation_set_status",
                "style_guide_create",
                "style_guide_get",
                "style_guide_update",
                "analysis_job_create",
                "analysis_job_get",
                "analysis_job_list",
                "analysis_job_update",
                "analysis_job_complete",
                "coverage_get",
                "coverage_mark",
                "analysis_checkpoint",
            }
            assert {tool.name for tool in listing.tools} == expected
            assert all(
                tool.input_schema.get("additionalProperties") is False for tool in listing.tools
            )
            assert all(tool.output_schema for tool in listing.tools)
            report = await call(
                mcp, "part_import_validate", {"work_id": validation_work, "file_path": source.name}
            )
            assert report["status"] == "valid"
            result = await mcp_import(mcp, {"file_path": source.name, "request_id": key})
            work = result["work"]
            work_id = work["id"]
            assert not result["replayed"]
            assert rest.get(f"/works/{work_id}").json() == work
            assert http_file(rest, work_id).content == data
            replay = http_import(
                rest, files={"file": ("renamed.txt", data)}, data={"request_id": key}
            )
            assert replay.status_code == 200 and replay.json()["work"] == work
            assert (await mcp_import(mcp, {"file_path": source.name, "request_id": key}))[
                "replayed"
            ]
            assert (await call(mcp, "work_get", {"work_id": work_id})) == work
            assert work in (await call(mcp, "work_list", {}))["items"]
            part = result["part"]
            assert (await call(mcp, "part_list", {"work_id": work_id}))["items"] == [part]
            rename = {
                "work_id": work_id,
                "part_id": part["id"],
                "request_id": str(uuid4()),
                "expected_version": part["version"],
                "name": "任意分部名",
            }
            changed = await call(mcp, "part_update", rename)
            assert changed["result"]["name"] == "任意分部名"
            assert (await call(mcp, "part_update", rename))["replayed"]
            assert (await call(mcp, "part_get", {"work_id": work_id, "part_id": part["id"]}))[
                "version"
            ] == 2
            # 改名不改变原文件和原导入回执。恢复部名供后续重名校验使用。
            await call(
                mcp,
                "part_update",
                rename
                | {
                    "request_id": str(uuid4()),
                    "expected_version": 2,
                    "name": part["name"],
                },
            )
            renamed_work = await call(
                mcp,
                "work_update",
                {
                    "work_id": work_id,
                    "request_id": str(uuid4()),
                    "expected_version": work["version"],
                    "name": "改名-" + str(uuid4()),
                },
            )
            assert renamed_work["result"]["id"] == work_id
            assert renamed_work["result"]["version"] == work["version"] + 1
            assert (await call(mcp, "part_import_get", {"request_id": key}))["work"] == work
            page = await call(mcp, "source_sections", {"work_id": work_id, "limit": 1})
            section = page["items"][0]
            rest_page = await call(
                mcp, "source_sections", {"work_id": work_id, "cursor": page["next_cursor"]}
            )
            assert [s["ordinal"] for s in page["items"] + rest_page["items"]] == [1, 2, 3]
            args = {"work_id": work_id, "section_id": section["id"]}
            first = await call(mcp, "source_paragraphs", args | {"limit": 1, "format": "full"})
            following = await call(
                mcp, "source_paragraphs", args | {"cursor": first["next_cursor"], "format": "full"}
            )
            paragraphs = first["items"] + following["items"]
            for paragraph in paragraphs:
                pos = paragraph["source_position"]
                assert data[pos["start_byte"] : pos["end_byte"]].decode() == paragraph["text"]
            source_range = args | {
                "start_paragraph_id": paragraphs[0]["id"],
                "end_paragraph_id": paragraphs[-1]["id"],
            }
            read = await call(
                mcp, "source_read", {"source_range": source_range, "limit": 1, "format": "full"}
            )
            assert read["items"] == paragraphs[:1]
            assert read["actual_range"]["end_paragraph_id"] == paragraphs[0]["id"]
            context = await call(
                mcp,
                "source_get_context",
                args
                | {
                    "paragraph_id": paragraphs[1]["id"],
                    "before": 100,
                    "after": 100,
                    "format": "full",
                },
            )
            assert context["items"] == paragraphs
            assert context["at_section_start"] and context["at_section_end"]
            await call(
                mcp,
                "part_import",
                {"work_id": work_id, "file_path": source.name, "request_id": str(uuid4())},
                code="PART_NAME_CONFLICT",
            )
            # REST 写入后 MCP 立即可见，旧作品的跨作品坐标仍被拒绝。
            other_data = f"分部：{uuid4()}\n标题：章\n另一个作品".encode()
            other = http_import(
                rest, files={"file": ("other.txt", other_data)}, data={"request_id": str(uuid4())}
            ).json()["work"]
            assert await call(mcp, "work_get", {"work_id": other["id"]}) == other
            await call(
                mcp, "source_paragraphs", args | {"work_id": other["id"]}, code="SECTION_NOT_FOUND"
            )
            invalid_arguments: list[dict[str, Any]] = [
                {"limit": 0},
                {"limit": 1001},
                {"limit": "10"},
                {"limit": True},
                {"private-input": "private-input"},
            ]
            for invalid in invalid_arguments:
                error = await call(mcp, "work_list", invalid, code="INVALID_INPUT")
                assert "private-input" not in json.dumps(error)
            await call(mcp, "work_get", {"work_id": "private-input"}, code="INVALID_INPUT")
            await call(mcp, "source_paragraphs", args | {"cursor": "bad"}, code="INVALID_CURSOR")
            with pytest.raises(MCPError):
                await mcp.call_tool("unknown_tool", {})
            # 一个客户端关闭不会结束应用或另一个客户端。
            async with Client(address) as second:
                assert await second.list_tools()
            assert rest.get("/health").status_code == 200
            assert (await call(mcp, "part_import_get", {"request_id": key}))["work"] == work
            return {"work": work, "args": args, "paragraphs": paragraphs}

    with running_server(postgres_url, tmp_path, "mcp-first") as rest:
        saved = asyncio.run(exercise(rest))

    async def recovered(rest: httpx.Client) -> None:
        async with Client(str(rest.base_url).rstrip("/") + "/mcp") as mcp:
            assert (await call(mcp, "part_import_get", {"request_id": key}))["work"] == saved[
                "work"
            ]
            assert (await call(mcp, "source_paragraphs", saved["args"] | {"format": "full"}))[
                "items"
            ] == saved["paragraphs"]
            source.write_bytes(data + b"changed")
            await call(
                mcp,
                "part_import",
                {"work_id": saved["work"]["id"], "file_path": source.name, "request_id": key},
                code="REQUEST_CONFLICT",
            )

    with running_server(postgres_url, tmp_path, "mcp-second") as rest:
        asyncio.run(recovered(rest))
    for label in ["mcp-first", "mcp-second"]:
        assert (tmp_path / f"{label}.stdout").read_bytes() == b""
        log = (tmp_path / f"{label}.stderr").read_bytes()
        assert b"private-novel-body" not in log and b"private-input" not in log
        assert b"SELECT" not in log


def test_discovery_without_database_and_http_guards(tmp_path: Path) -> None:
    unavailable = "postgresql+psycopg://invalid:private-password@127.0.0.1:1/absent"
    (tmp_path / "a.txt").write_text("分部：预检样例\n标题：一\n正文", encoding="utf-8")
    with running_server(unavailable, tmp_path, "unavailable", request_limit=4096) as rest:

        async def exercise() -> None:
            async with Client(str(rest.base_url).rstrip("/") + "/mcp") as mcp:
                assert len((await mcp.list_tools()).tools) == 59
                await call(mcp, "work_list", {}, code="DATABASE_UNAVAILABLE")
                # 文件可以直接读取，后续重名预检仍需数据库。
                await call(
                    mcp,
                    "part_import_validate",
                    {"work_id": str(uuid4()), "file_path": "a.txt"},
                    code="DATABASE_UNAVAILABLE",
                )

        asyncio.run(exercise())
        assert rest.get("/health").status_code == 200
        for headers, status in [
            ({"Host": "evil.example"}, 421),
            ({"Origin": "http://evil.example"}, 403),
            ({"Host": "127.0.0.1:1"}, 421),
            ({"Origin": "null"}, 403),
        ]:
            assert rest.post("/mcp", json={}, headers=headers).status_code == status
        response = rest.post(
            "/mcp",
            content=iter([b"x" * 2100, b"y" * 2100]),
            headers={"Content-Type": "application/json"},
        )
        assert "content-length" not in response.request.headers
        assert response.status_code == 413
        assert rest.get("/mcp/mcp").status_code == 404
    assert b"private-password" not in (tmp_path / "unavailable.stderr").read_bytes()


def test_file_errors_and_oversize_result(postgres_url: str, tmp_path: Path) -> None:
    root = tmp_path / "imports"
    root.mkdir()
    bad = root / "bad.txt"
    bad.write_bytes(b"private-input\xff")
    huge = root / "huge.txt"
    huge.write_bytes(f"分部：{uuid4()}\n标题：章\n".encode() + b"z" * (1024 * 1024))
    too_large = root / "too-large.txt"
    with too_large.open("wb") as output:
        output.truncate(64 * 1024 * 1024 + 1)

    async def exercise(rest: httpx.Client) -> None:
        async with Client(str(rest.base_url).rstrip("/") + "/mcp") as mcp:
            validation_work = http_work(rest)
            for path, code in [
                ("missing.txt", "FILE_NOT_FOUND"),
                (".", "FILE_UNREADABLE"),
                (str(too_large), "FILE_TOO_LARGE"),
            ]:
                key = str(uuid4())
                await call(
                    mcp,
                    "part_import",
                    {"work_id": validation_work, "file_path": path, "request_id": key},
                    code=code,
                )
                assert rest.get(f"/part-imports/{key}").status_code == 404
            invalid = await call(
                mcp, "part_import_validate", {"work_id": validation_work, "file_path": str(bad)}
            )
            assert invalid["status"] == "invalid"
            await call(
                mcp,
                "part_import",
                {"work_id": validation_work, "file_path": str(bad), "request_id": str(uuid4())},
                code="IMPORT_VALIDATION_ERROR",
            )
            work = (await mcp_import(mcp, {"file_path": str(huge), "request_id": str(uuid4())}))[
                "work"
            ]
            section = (await call(mcp, "source_sections", {"work_id": work["id"]}))["items"][0]
            await call(
                mcp,
                "source_paragraphs",
                {"work_id": work["id"], "section_id": section["id"], "limit": 1},
                code="RESULT_TOO_LARGE",
            )
            await call(
                mcp,
                "source_paragraphs",
                {"work_id": work["id"], "section_id": section["id"], "format": "compact"},
                code="RESULT_TOO_LARGE",
            )
            assert http_file(rest, work["id"]).content == huge.read_bytes()

    with running_server(postgres_url, tmp_path, "files") as rest:
        asyncio.run(exercise(rest))


@pytest.mark.skipif(os.name != "nt", reason="此测试验证 Windows 目录联接及强制文件锁")
def test_windows_junction_and_unreadable_file(postgres_url: str, tmp_path: Path) -> None:
    import msvcrt

    root = tmp_path / "imports"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    original = f"分部：{uuid4()}\n标题：章\nprivate-outside".encode()
    (outside / "book.txt").write_bytes(original)
    junction = root / "linked"
    subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "New-Item -ItemType Junction -Path $env:NOVEL_LENS_TEST_LINK "
            "-Target $env:NOVEL_LENS_TEST_TARGET -ErrorAction Stop | Out-Null",
        ],
        env=os.environ
        | {
            "NOVEL_LENS_TEST_LINK": str(junction),
            "NOVEL_LENS_TEST_TARGET": str(outside),
        },
        check=True,
        capture_output=True,
    )
    locked = root / "locked.txt"
    locked.write_bytes(b"private-locked")

    async def exercise(rest: httpx.Client) -> None:
        async with Client(str(rest.base_url).rstrip("/") + "/mcp") as mcp:
            key = str(uuid4())
            validation_work = http_work(rest)
            work_id = None
            # 绝对路径、上级目录和目录联接都指向同一字节，重试不能重复创建作品。
            paths = [str(outside / "book.txt"), "../outside/book.txt", "linked/book.txt"]
            for path in paths:
                report = await call(
                    mcp, "part_import_validate", {"work_id": validation_work, "file_path": path}
                )
                assert report["status"] == "valid"
            for index, path in enumerate(paths):
                result = await mcp_import(mcp, {"file_path": path, "request_id": key})
                assert result["replayed"] == (index > 0)
                if work_id is None:
                    work_id = result["work"]["id"]
                assert result["work"]["id"] == work_id
                assert http_file(rest, work_id).content == original
            failed_key = str(uuid4())
            await call(
                mcp,
                "part_import",
                {"work_id": work_id, "file_path": "locked.txt", "request_id": failed_key},
                code="FILE_UNREADABLE",
            )
            assert rest.get(f"/part-imports/{failed_key}").status_code == 404

    try:
        with locked.open("r+b") as held:
            msvcrt.locking(held.fileno(), msvcrt.LK_NBLCK, 1)
            try:
                with running_server(postgres_url, root, "locked") as rest:
                    asyncio.run(exercise(rest))
            finally:
                held.seek(0)
                msvcrt.locking(held.fileno(), msvcrt.LK_UNLCK, 1)
    finally:
        # 只移除测试所建的联接，不递归删除其目标目录。
        junction.rmdir()
    assert (outside / "book.txt").read_bytes() == original
    assert locked.read_bytes() == b"private-locked"
