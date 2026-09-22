"""显式选择本机真实权重后，验证模型与 HTTP / 官方 MCP 的完整数据流。"""

import asyncio
import os
import socket
from pathlib import Path
from uuid import uuid4

import pytest
from mcp import Client
from test_live_http import running_server
from test_mcp_http import call

from novel_lens.embedding import EmbeddingClient
from novel_lens.embedding_runtime import read_config, save_config
from novel_lens.embedding_runtime import running_server as embedding_server


def test_real_model_http_mcp_restart(postgres_url: str, tmp_path: Path) -> None:
    path = os.environ.get("NOVEL_LENS_TEST_EMBEDDING_CONFIG")
    if not path:
        pytest.skip("未指定真实模型配置，未执行 embedding 端到端验收")
    config = read_config(Path(path))
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        config.port = listener.getsockname()[1]
    local = tmp_path / "embedding.toml"
    save_config(config, local)
    data = (
        f"书名：{uuid4()}\n标题：雨夜\n"
        "　暴雨敲打窗户，她屏住呼吸，听见门外越来越近的脚步声。 \n"
        "纸条上写着 <|endoftext|>，墨迹尚未干透。\n"
        "标题：沙漠\n烈日晒着沙丘，旅人抿了抿干裂的嘴唇，水壶已经空了。\n"
        "标题：厨房\n他揉好面团，将切碎的葱放进碗里，锅里的水刚刚烧开。"
    ).encode()
    with running_server(
        postgres_url, tmp_path, "semantic", embedding_config=local, request_timeout=120
    ) as rest:
        work = rest.post(
            "/work-imports", files={"file": ("sample.txt", data)}, data={"request_id": str(uuid4())}
        ).json()["work"]["id"]
        base = f"/works/{work}"
        assert rest.get(base + "/semantic-indexes/status").json()["state"] == "missing"
        args = {"work_id": work, "request_id": str(uuid4())}
        unavailable = rest.post(base + "/semantic-indexes", json=args)
        assert unavailable.status_code == 503
        with embedding_server(config):
            model = EmbeddingClient(local)
            model.verify()
            literal = model.tokenize(" 字面 <|endoftext|> 不截断 ")
            assert len(model.embed([literal])[0]) == 1024
            created = rest.post(base + "/semantic-indexes", json=args).json()
            assert created["generation_status"] == "building", created
            identifier = created["index_id"]
            batch = {"work_id": work, "index_id": identifier, "request_id": str(uuid4())}
            built = rest.post(base + f"/semantic-indexes/{identifier}/build", json=batch).json()
            assert built["generation_status"] == "ready", built
            query = {"work_id": work, "query": "暴雨中的夜晚，门外脚步声带来紧张和恐惧", "limit": 1}
            result = rest.post(base + "/semantic-search", json=query).json()
            assert "暴雨" in result["items"][0]["excerpt"], result
            assert result["coverage"] == {
                "total": 4,
                "covered": 4,
                "blocked": 0,
                "pending": 0,
                "complete": True,
            }
            ref = result["items"][0]["source_range"]

            async def exercise() -> None:
                async with Client(str(rest.base_url).rstrip("/") + "/mcp") as mcp:
                    replay_create = await call(mcp, "semantic_index_create", args)
                    assert replay_create == created | {"replayed": True}
                    replay_build = await call(mcp, "semantic_index_build", batch)
                    assert replay_build == built | {"replayed": True}
                    state_args = {"work_id": work, "index_id": identifier}
                    status = await call(mcp, "semantic_index_get", state_args)
                    assert (
                        status
                        == rest.get(
                            base + "/semantic-indexes/status", params={"index_id": identifier}
                        ).json()
                    )
                    actual = await call(mcp, "source_semantic_search", query)
                    assert actual["items"][0]["source_range"] == ref
                    assert actual["items"][0]["score"] == pytest.approx(
                        result["items"][0]["score"], abs=1e-5
                    )
                    read = await call(mcp, "source_read", {"source_range": ref})
                    assert read["items"][0]["text"].startswith("　暴雨")
                    await call(
                        mcp,
                        "source_semantic_search",
                        query | {"kind": "annotation"},
                        code="INVALID_INPUT",
                    )
                    await call(
                        mcp,
                        "semantic_index_build",
                        batch | {"max_items": True},
                        code="INVALID_INPUT",
                    )

            asyncio.run(exercise())
            assert rest.get(base + "/file").content == data
            mismatch = rest.post(f"/works/{uuid4()}/semantic-search", json=query)
            assert mismatch.status_code == 422
        assert rest.get(base + "/semantic-indexes/status").json()["active"]["coverage"]["complete"]
        assert rest.post(base + "/semantic-search", json=query).status_code == 503
        with embedding_server(config):
            restarted = rest.post(base + "/semantic-search", json=query).json()
            assert restarted["index_id"] == identifier
            assert restarted["items"][0]["source_range"] == ref
    for log in tmp_path.glob("*.stderr"):
        assert "暴雨" not in log.read_text(encoding="utf-8")
