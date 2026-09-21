"""官方 MCP 客户端验证任务工具、重启恢复及日志不泄露接续内容。"""

import asyncio
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from mcp import Client
from test_live_http import running_server
from test_mcp_http import call


def test_analysis_tools_restart_and_errors(postgres_url: str, tmp_path: Path) -> None:
    async def exercise(rest: httpx.Client) -> dict[str, Any]:
        async with Client(str(rest.base_url).rstrip("/") + "/mcp") as mcp:
            tools = {tool.name: tool for tool in (await mcp.list_tools()).tools}
            assert len(tools) == 42
            hints = tools["analysis_checkpoint"].annotations
            assert hints and hints.destructive_hint and not hints.read_only_hint
            read_hints = tools["coverage_get"].annotations
            assert read_hints and read_hints.read_only_hint
            work = rest.post(
                "/work-imports",
                data={"request_id": str(uuid4())},
                files={
                    "file": (
                        "sample.txt",
                        f"书名：{uuid4()}\n标题：样本\nprivate-checkpoint-source\n末段".encode(),
                    )
                },
            ).json()["work"]
            work_id = work["id"]
            section = (await call(mcp, "source_sections", {"work_id": work_id}))["items"][0]
            p = (
                await call(
                    mcp, "source_paragraphs", dict(work_id=work_id, section_id=section["id"])
                )
            )["items"]
            ref = dict(
                work_id=work_id,
                section_id=section["id"],
                start_paragraph_id=p[0]["id"],
                end_paragraph_id=p[-1]["id"],
            )
            recovery = dict(
                facts=[dict(note="private-checkpoint-fact", source_ranges=[ref])],
                open_questions=[
                    dict(
                        observation="private-checkpoint-observation",
                        question="private-checkpoint-question",
                        source_ranges=[ref],
                    )
                ],
                next_action="private-checkpoint-next",
                next_range=ref,
            )
            create = dict(
                request_id=str(uuid4()),
                work_id=work_id,
                title="样本深读",
                goal="核对样本写法",
                target=dict(kind="whole_work"),
                recovery=recovery,
            )
            job = (await call(mcp, "analysis_job_create", create))["result"]
            key = dict(work_id=work_id, job_id=job["id"])
            assert (await call(mcp, "analysis_job_list", dict(work_id=work_id)))["items"][0][
                "counts"
            ]["unprocessed"] == 2
            assert (await call(mcp, "coverage_get", key))["items"][0]["status"] == "unprocessed"
            mark = key | dict(
                request_id=str(uuid4()), expected_version=1, source_range=ref, status="read"
            )
            job = (await call(mcp, "coverage_mark", mark))["result"]
            await call(
                mcp,
                "coverage_mark",
                mark | dict(request_id=str(uuid4()), expected_version=2, status="processed"),
                code="INVALID_INPUT",
            )
            job = (
                await call(
                    mcp,
                    "analysis_job_update",
                    key
                    | dict(
                        request_id=str(uuid4()),
                        expected_version=2,
                        status="running",
                        recovery=recovery,
                    ),
                )
            )["result"]
            checkpoint = key | dict(
                request_id=str(uuid4()),
                expected_version=3,
                source_range=ref,
                writes=[],
                recovery=recovery,
            )
            await call(mcp, "analysis_checkpoint", checkpoint, code="INVALID_INPUT")
            checkpoint["outcome_note"] = "private-checkpoint-outcome"
            checkpoint["writes"] = [
                dict(
                    operation="style_guide_create",
                    input=dict(
                        request_id=str(uuid4()),
                        work_id=work_id,
                        scope_note="仅样本，暂未发现稳定写法",
                        entries=[],
                    ),
                )
            ]
            receipt = await call(mcp, "analysis_checkpoint", checkpoint)
            assert receipt["result"]["version"] == 4
            complete = key | dict(
                request_id=str(uuid4()),
                expected_version=4,
                recovery=recovery,
                style_guide_version=1,
                calibration_note="private-checkpoint-calibration",
                limitations=None,
            )
            done = await call(mcp, "analysis_job_complete", complete)
            assert done["result"]["status"] == "completed"
            await call(
                mcp,
                "analysis_checkpoint",
                checkpoint | dict(request_id=str(uuid4()), expected_version=5),
                code="JOB_STATE_CONFLICT",
            )
            await call(
                mcp, "analysis_job_get", key | dict(work_id=str(uuid4())), code="JOB_NOT_FOUND"
            )
            return dict(
                key=key,
                create=create,
                checkpoint=checkpoint,
                receipt=receipt,
                complete=complete,
                done=done,
            )

    with running_server(postgres_url, tmp_path, "analysis-first") as rest:
        saved = asyncio.run(exercise(rest))

    async def recover(rest: httpx.Client) -> None:
        async with Client(str(rest.base_url).rstrip("/") + "/mcp") as mcp:
            for operation, request, expected in [
                ("analysis_checkpoint", saved["checkpoint"], saved["receipt"]),
                ("analysis_job_complete", saved["complete"], saved["done"]),
            ]:
                assert await call(mcp, operation, request) == expected | dict(replayed=True)
                assert await call(
                    mcp, "asset_write_get", dict(request_id=request["request_id"])
                ) == expected | dict(replayed=True)
            job = await call(mcp, "analysis_job_get", saved["key"])
            assert job == saved["done"]["result"]
            assert (await call(mcp, "coverage_get", saved["key"]))["items"][0][
                "status"
            ] == "processed"
            assert (await call(mcp, "analysis_job_create", saved["create"]))["result"][
                "version"
            ] == 1

    with running_server(postgres_url, tmp_path, "analysis-restart") as rest:
        asyncio.run(recover(rest))
    for path in tmp_path.glob("analysis-*.std*"):
        log = path.read_text(encoding="utf-8")
        assert "private-checkpoint" not in log and "Traceback" not in log
