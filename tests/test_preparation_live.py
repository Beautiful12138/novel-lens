"""真实模型、HTTP 进程与 PostgreSQL 的自动准备闭环；不评价文学质量。"""

import asyncio
import os
import socket
import time
from pathlib import Path
from uuid import uuid4

import pytest
from conftest import temporary_database
from mcp import Client
from test_live_http import running_server
from test_mcp_http import call

from novel_lens.embedding_runtime import read_config, save_config
from novel_lens.embedding_runtime import running_server as embedding_server


def test_real_embedding_automatic_preparation(postgres_url: str, tmp_path: Path) -> None:
    configured = os.environ.get("NOVEL_LENS_TEST_EMBEDDING_CONFIG")
    if not configured:
        pytest.skip("未指定真实模型配置，未验证真实向量自动准备")
    config = read_config(Path(configured))
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        config.port = listener.getsockname()[1]
    local = tmp_path / "embedding.toml"
    save_config(config, local)
    source = tmp_path / "真实模型样例.txt"
    original = (
        "分部：样例\n标题：雨夜\n雨敲着窗户，她屏住呼吸，门外响起脚步声。\n"
        "标题：厨房\n他把葱花撒进面汤，热气模糊了眼镜。\n"
        "标题：荒漠\n水壶空了，旅人顶着烈日穿过沙丘。"
    )
    source.write_text(original, encoding="utf-8")
    with temporary_database(postgres_url) as isolated, embedding_server(config):
        with running_server(
            isolated,
            tmp_path,
            "prepare-live",
            embedding_config=local,
            request_timeout=120,
            mcp_profile="business",
        ) as rest:
            response = rest.post(
                "/preparation/import",
                json=dict(request_id=str(uuid4()), name=str(uuid4()), file_path=str(source)),
            )
            assert response.status_code == 200, response.text
            imported = response.json()
            key = dict(work_id=imported["work"]["id"], job_id=imported["job"]["id"])
            batch_number = 0
            while True:
                state = rest.post("/preparation/status", json=key).json()
                if not state["remaining"]["items"]:
                    break
                ref = state["remaining"]["items"][0]["source_range"]
                result = rest.post(
                    "/preparation/batch",
                    json=dict(
                        **key,
                        request_id=str(uuid4()),
                        expected_version=state["job"]["version"],
                        source_range=ref,
                        marks=[]
                        if batch_number == 0
                        else [dict(source_ranges=[ref], note="从原文的具体声音或动作进入场景")],
                        recovery={"next_action": "读取下一范围"},
                        outcome_note="自编短样例已核对",
                    ),
                )
                assert result.status_code == 200, result.text
                batch_number += 1
            deadline = time.monotonic() + 120
            while True:
                state = rest.post("/preparation/status", json=key).json()
                if state["indexes"]["state"] == "ready":
                    break
                assert state["indexes"]["state"] not in {"failed", "blocked"}, state
                assert time.monotonic() < deadline, state
                time.sleep(0.2)
            assert state["indexes"]["source_coverage"]["clues"]["covered"] == 2
            result = rest.post(
                "/preparation/finish",
                json=dict(
                    **key,
                    request_id=str(uuid4()),
                    expected_version=state["job"]["version"],
                    recovery={"next_action": "准备完成"},
                    note="原文、处理进度与索引已就绪",
                ),
            )
            assert result.status_code == 200, result.text
            assert rest.post("/preparation/status", json=key).json()["work_ready"]
            # 从自动生成的原文向量查询未依赖标签的情境，并读回准确原文。
            found = rest.post(
                "/reference/query",
                json=dict(
                    scope=[{"work_id": key["work_id"]}],
                    query="雨夜有人接近房门，她紧张地听脚步声",
                    limit=3,
                    terms=["门外", "脚步声"],
                ),
            )
            assert found.status_code == 200, found.text
            assert "雨" in found.json()["items"][0]["excerpt"]
            assert found.json()["items"][0]["annotation_ids"] == []

            second_file = tmp_path / "第二部样例.txt"
            second_file.write_text(
                "分部：夜班\n标题：交班\n走廊传来脚步声，他把交班本合上。", encoding="utf-8"
            )
            second = rest.post(
                "/preparation/import",
                json=dict(request_id=str(uuid4()), name=str(uuid4()), file_path=str(second_file)),
            ).json()
            second_key = dict(work_id=second["work"]["id"], job_id=second["job"]["id"])
            deadline = time.monotonic() + 120
            while not rest.post("/preparation/status", json=second_key).json()["indexes"][
                "source_ready"
            ]:
                assert time.monotonic() < deadline
                time.sleep(0.2)
            multi_request = dict(
                query="等待时听到门外脚步声",
                terms=["脚步声"],
                limit=1,
                scope=[{"work_id": key["work_id"]}, {"work_id": second_key["work_id"]}],
            )
            multi_response = rest.post("/reference/query", json=multi_request)
            assert multi_response.status_code == 200, multi_response.text
            multi = multi_response.json()

            async def read_through_business_mcp() -> None:
                async with Client(str(rest.base_url).rstrip("/") + "/mcp") as mcp:
                    assert len((await mcp.list_tools()).tools) == 8
                    result = await call(
                        mcp, "reference_query", {"search_id": found.json()["search_id"]}
                    )
                    assert result == found.json()
                    read = await call(mcp, "source_read", result["items"][0]["source_range"])
                    assert read["items"][0]["text"] == "雨敲着窗户，她屏住呼吸，门外响起脚步声。"
                    full = await call(
                        mcp, "reference_query", {"search_id": multi["search_id"], "format": "full"}
                    )
                    assert full["items"][0]["source_range"] == multi["items"][0]["source_range"]
                    assert "diagnostics" in full and "score" in full["items"][0]
                    page, seen = multi, []
                    while True:
                        seen.extend(page["items"])
                        if page["next_cursor"] is None:
                            break
                        page = await call(
                            mcp,
                            "reference_query",
                            {"search_id": multi["search_id"], "cursor": page["next_cursor"]},
                        )
                    assert {item["source_range"]["work_id"] for item in seen} == {
                        key["work_id"],
                        second_key["work_id"],
                    }
                    read_multi = await call(mcp, "source_read", multi["items"][0]["source_range"])
                    actual = {
                        "work_id": read_multi["work_id"],
                        "section_id": read_multi["section_id"],
                        **read_multi["actual_range"],
                    }
                    remaining = await call(
                        mcp, "reference_query", multi_request | {"exclude_ranges": [actual]}
                    )
                    assert remaining["items"] and remaining["items"][0]["source_range"] != actual
                    full_read = await call(
                        mcp, "source_read", multi["items"][0]["source_range"] | {"format": "full"}
                    )
                    assert full_read["actual_range"] == read_multi["actual_range"]
                    assert full_read["items"][0]["text"] == read_multi["items"][0]["text"]
                    assert "source_position" in full_read["items"][0]
                    state = await call(mcp, "prepare_status", key)
                    revision = dict(
                        **key,
                        request_id=str(uuid4()),
                        expected_version=state["job"]["version"],
                        source_range=result["items"][0]["source_range"],
                        marks=[
                            dict(
                                source_ranges=[result["items"][0]["source_range"]],
                                note="声音使等待具体可感",
                            )
                        ],
                        reopen_reason="补充阅读后确认的声音线索",
                        recovery={"next_action": "等待修订索引"},
                        outcome_note="补充一条原文观察",
                    )
                    saved = await call(mcp, "prepare_batch", revision)
                    assert (await call(mcp, "prepare_batch", revision))["replayed"]
                    deadline = time.monotonic() + 120
                    while True:
                        state = await call(mcp, "prepare_status", key)
                        assert state["job"]["status"] == "running" and not state["work_ready"]
                        if state["indexes"]["state"] == "ready":
                            break
                        assert time.monotonic() < deadline, state
                        await asyncio.sleep(0.2)
                    await call(
                        mcp,
                        "prepare_finish",
                        dict(
                            **key,
                            request_id=str(uuid4()),
                            expected_version=saved["version"],
                            recovery={"next_action": "修订完成"},
                            note="修订线索已同步",
                        ),
                    )

            asyncio.run(read_through_business_mcp())
            restart_page = rest.post("/reference/query", json=multi_request).json()
            restart_next = rest.post(
                "/reference/query",
                json={
                    "search_id": restart_page["search_id"],
                    "cursor": restart_page["next_cursor"],
                    "format": "full",
                },
            ).json()
        with running_server(
            isolated, tmp_path, "prepare-live-restart", embedding_config=local, request_timeout=120
        ) as rest:
            assert rest.post("/preparation/status", json=key).json()["work_ready"]
            assert (
                rest.post(
                    "/reference/query",
                    json={
                        "search_id": restart_page["search_id"],
                        "cursor": restart_page["next_cursor"],
                        "format": "full",
                    },
                ).json()
                == restart_next
            )
    assert source.read_text(encoding="utf-8") == original
