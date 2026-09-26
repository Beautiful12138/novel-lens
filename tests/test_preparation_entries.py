"""验证准备入口跨协议复用回执及服务生命周期中的自动索引消费。"""

import asyncio
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest
from conftest import temporary_database
from mcp import Client
from mcp.shared.exceptions import MCPError
from pydantic import SecretStr
from starlette.testclient import TestClient
from test_live_http import running_server
from test_mcp_http import call
from test_semantic import DeterministicModel

from novel_lens.app import create_app
from novel_lens.config import Settings


def test_preparation_http_mcp_receipt_and_remaining(postgres_url: str, tmp_path: Path) -> None:
    source = tmp_path / "准备.txt"
    source.write_text("分部：卷一\n标题：第一章\n雨停了。\n他推开窗。", encoding="utf-8")
    request = dict(request_id=str(uuid4()), name=str(uuid4()), file_path=str(source))

    async def exercise(rest: httpx.Client) -> None:
        async with Client(str(rest.base_url).rstrip("/") + "/mcp") as mcp:
            names = {tool.name for tool in (await mcp.list_tools()).tools}
            assert names == {
                "library_browse",
                "prepare_import",
                "prepare_status",
                "prepare_batch",
                "prepare_finish",
                "prepare_cleanup",
                "reference_query",
                "source_read",
            }
            with pytest.raises(MCPError):
                await mcp.call_tool("tag_create", {})
            imported = await call(mcp, "prepare_import", request)
            receipt = rest.get(f"/preparation/receipts/{request['request_id']}").json()
            assert receipt["replayed"] and receipt["part"] == imported["part"]
            via_status = await call(mcp, "prepare_status", {"request_id": request["request_id"]})
            assert via_status == receipt
            status_request = dict(work_id=imported["work"]["id"], job_id=imported["job"]["id"])
            status = await call(mcp, "prepare_status", status_request)
            chapters = await call(
                mcp, "library_browse", dict(view="sections", work_id=imported["work"]["id"])
            )
            reading = await call(
                mcp,
                "source_read",
                dict(
                    work_id=imported["work"]["id"],
                    section_id=chapters["sections"]["items"][0]["id"],
                ),
            )
            assert [p["text"] for p in reading["items"]] == ["雨停了。", "他推开窗。"]
            batch = dict(
                **status_request,
                request_id=str(uuid4()),
                expected_version=1,
                source_range=status["remaining"]["items"][0]["source_range"],
                marks=[],
                recovery={"next_action": "等待索引"},
                outcome_note="已读，无需新增标记",
            )
            saved = rest.post("/preparation/batch", json=batch)
            assert saved.status_code == 200, saved.text
            assert (await call(mcp, "prepare_batch", batch))["replayed"]
            current = await call(mcp, "prepare_status", status_request)
            assert current["remaining"]["items"] == [] and not current["work_ready"]
            await call(
                mcp,
                "prepare_finish",
                dict(
                    **status_request,
                    request_id=str(uuid4()),
                    expected_version=saved.json()["version"],
                    recovery={"next_action": "完成"},
                    note="已核对",
                ),
                code="PREPARATION_INDEX_PENDING",
            )
            cleaned = await call(
                mcp,
                "prepare_cleanup",
                dict(work_id=imported["work"]["id"], confirm_work_name=imported["work"]["name"]),
            )
            assert cleaned["deleted"] and source.exists()

    with running_server(postgres_url, tmp_path, "preparation", mcp_profile="business") as rest:
        asyncio.run(exercise(rest))


def test_lifespan_consumes_queue_without_ai_calls(
    postgres_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """模型替身只验证调度和事务，不作为真实 embedding 或文学效果证据。"""
    import novel_lens.app as app_module

    model = DeterministicModel()
    monkeypatch.setattr(app_module, "EmbeddingClient", lambda path: model)
    source = tmp_path / "自动索引.txt"
    source.write_text("分部：卷一\n标题：雨夜\n雨声盖过了脚步声。", encoding="utf-8")
    with temporary_database(postgres_url) as isolated:
        settings = Settings.model_construct(
            database_url=SecretStr(isolated), embedding_config=tmp_path / "fake.toml"
        )
        with TestClient(create_app(settings)) as client:
            imported = client.post(
                "/preparation/import",
                json=dict(request_id=str(uuid4()), name=str(uuid4()), file_path=str(source)),
            ).json()
            status_request = dict(work_id=imported["work"]["id"], job_id=imported["job"]["id"])
            initial = client.post("/preparation/status", json=status_request).json()
            batch: dict[str, Any] = dict(
                **status_request,
                request_id=str(uuid4()),
                expected_version=1,
                source_range=initial["remaining"]["items"][0]["source_range"],
                marks=[
                    dict(
                        source_ranges=[initial["remaining"]["items"][0]["source_range"]],
                        note="声音掩盖行动",
                    )
                ],
                recovery={"next_action": "等待索引"},
                outcome_note="原文已核对",
            )
            assert client.post("/preparation/batch", json=batch).status_code == 200
            deadline = time.monotonic() + 20
            while True:
                state = client.post("/preparation/status", json=status_request).json()
                if state["indexes"]["state"] == "ready":
                    break
                assert time.monotonic() < deadline, state
                time.sleep(0.05)
            assert not state["work_ready"]
            finished = client.post(
                "/preparation/finish",
                json=dict(
                    **status_request,
                    request_id=str(uuid4()),
                    expected_version=state["job"]["version"],
                    recovery={"next_action": "已准备完成"},
                    note="全部目标已处理",
                ),
            )
            assert finished.status_code == 200, finished.text
        # 重启服务读取持久状态，不重复计算已同步的向量，不创建 AI 任务。
        calls = len(model.calls)
        with TestClient(create_app(settings)) as restarted:
            state = restarted.post("/preparation/status", json=status_request).json()
            assert state["work_ready"] and state["job"]["status"] == "completed"
        assert len(model.calls) == calls
