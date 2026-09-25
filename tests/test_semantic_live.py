"""显式选择本机真实权重后，验证模型与 HTTP / 官方 MCP 的完整数据流。"""

import asyncio
import os
import socket
from pathlib import Path
from uuid import uuid4

import pytest
from mcp import Client
from part_fixtures import http_file, http_import
from test_live_http import running_server
from test_mcp_http import call

from novel_lens.embedding import EmbeddingClient
from novel_lens.embedding_runtime import read_config, save_config
from novel_lens.embedding_runtime import running_server as embedding_server


@pytest.mark.parametrize("kind", ["fulltext", "annotation"])
def test_real_model_http_mcp_restart(postgres_url: str, tmp_path: Path, kind: str) -> None:
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
        f"分部：{uuid4()}\n标题：雨夜\n"
        "　暴雨敲打窗户，她屏住呼吸，听见门外越来越近的脚步声。 \n"
        "纸条上写着 <|endoftext|>，墨迹尚未干透。\n"
        "标题：沙漠\n烈日晒着沙丘，旅人抿了抿干裂的嘴唇，水壶已经空了。\n"
        "标题：厨房\n他揉好面团，将切碎的葱放进碗里，锅里的水刚刚烧开。"
    ).encode()
    with running_server(
        postgres_url, tmp_path, "semantic", embedding_config=local, request_timeout=120
    ) as rest:
        work = http_import(
            rest, files={"file": ("sample.txt", data)}, data={"request_id": str(uuid4())}
        ).json()["work"]["id"]

        async def prepare_annotations() -> None:
            if kind != "annotation":
                return
            async with Client(str(rest.base_url).rstrip("/") + "/mcp") as mcp:
                sections = (await call(mcp, "source_sections", {"work_id": work}))["items"]
                for section in sections:
                    paragraphs = (
                        await call(
                            mcp, "source_paragraphs", {"work_id": work, "section_id": section["id"]}
                        )
                    )["items"]
                    await call(
                        mcp,
                        "annotation_create",
                        {
                            "work_id": work,
                            "request_id": str(uuid4()),
                            "note": "保留标注说明，不作为向量输入",
                            "source_ranges": [
                                {
                                    "work_id": work,
                                    "section_id": section["id"],
                                    "start_paragraph_id": paragraphs[0]["id"],
                                    "end_paragraph_id": paragraphs[-1]["id"],
                                }
                            ],
                        },
                    )

        asyncio.run(prepare_annotations())
        base = f"/works/{work}"
        assert (
            rest.get(base + "/semantic-indexes/status", params={"kind": kind}).json()["state"]
            == "missing"
        )
        args = {"work_id": work, "kind": kind, "request_id": str(uuid4())}
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
            query = {
                "work_id": work,
                "kind": kind,
                "query": "暴雨中的夜晚，门外脚步声带来紧张和恐惧",
                "limit": 1,
            }
            result = rest.post(base + "/semantic-search", json=query).json()
            assert "暴雨" in result["items"][0]["excerpt"], result
            assert result["coverage"] == {
                "total": 4 if kind == "fulltext" else 3,
                "covered": 4 if kind == "fulltext" else 3,
                **({"stale": 0, "not_indexed": 0} if kind == "annotation" else {}),
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
                    state_args = {"work_id": work, "kind": kind, "index_id": identifier}
                    status = await call(mcp, "semantic_index_get", state_args)
                    assert (
                        status
                        == rest.get(
                            base + "/semantic-indexes/status",
                            params={"index_id": identifier, "kind": kind},
                        ).json()
                    )
                    actual = await call(mcp, "source_semantic_search", query)
                    assert actual["items"][0]["source_range"] == ref
                    if kind == "annotation":
                        asset = await call(
                            mcp,
                            "annotation_get",
                            {"work_id": work, "annotation_id": actual["items"][0]["annotation_id"]},
                        )
                        assert asset["version"] == actual["items"][0]["annotation_version"]
                        assert asset["source_ranges"][0] == ref
                    assert actual["items"][0]["score"] == pytest.approx(
                        result["items"][0]["score"], abs=1e-5
                    )
                    # compact 引用与顶层归属重建，显式 full 保留原完整结果。
                    expanded = {"work_id": actual["work_id"]} | ref
                    whole = await call(mcp, "source_semantic_search", query | {"format": "full"})
                    assert whole["items"][0]["source_range"] == expanded
                    assert whole["coverage"] == actual["coverage"]
                    assert whole["partial"] == actual["partial"]
                    assert whole["candidate_window_limited"] == actual["candidate_window_limited"]
                    assert whole["items"][0]["excerpt"] == actual["items"][0]["excerpt"]
                    assert (
                        whole["items"][0]["excerpt_truncated"]
                        == actual["items"][0]["excerpt_truncated"]
                    )
                    assert "index_id" not in actual and "kind" not in actual["items"][0]
                    rest_full = rest.post(
                        base + "/semantic-search", json=query | {"format": "full"}
                    ).json()
                    assert rest_full["items"][0]["source_range"] == expanded
                    await call(
                        mcp,
                        "source_semantic_search",
                        query | {"format": "unknown"},
                        code="INVALID_INPUT",
                    )
                    assert (
                        rest.post(
                            base + "/semantic-search", json=query | {"format": "unknown"}
                        ).status_code
                        == 422
                    )
                    read = await call(mcp, "source_read", {"source_range": expanded})
                    assert read["items"][0]["text"].startswith("　暴雨")
                    await call(
                        mcp,
                        "source_semantic_search",
                        query | {"kind": "unsupported"},
                        code="INVALID_INPUT",
                    )
                    await call(
                        mcp,
                        "semantic_index_build",
                        batch | {"max_items": True},
                        code="INVALID_INPUT",
                    )

            asyncio.run(exercise())
            assert http_file(rest, work).content == data
            mismatch = rest.post(f"/works/{uuid4()}/semantic-search", json=query)
            assert mismatch.status_code == 422
        assert rest.get(base + "/semantic-indexes/status", params={"kind": kind}).json()["active"][
            "coverage"
        ]["complete"]
        assert rest.post(base + "/semantic-search", json=query).status_code == 503
        with embedding_server(config):
            restarted = rest.post(base + "/semantic-search", json=query).json()
            assert restarted["work_id"] == work and restarted["kind"] == kind
            assert restarted["items"][0]["source_range"] == ref
    for log in tmp_path.glob("*.stderr"):
        assert "暴雨" not in log.read_text(encoding="utf-8")
