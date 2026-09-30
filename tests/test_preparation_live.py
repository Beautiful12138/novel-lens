"""真实 HTTP/MCP 进程验证无模型的分类、标签、标注、原文及完成闭环。"""

import asyncio
from pathlib import Path
from uuid import uuid4

from annotation_fixtures import annotation_fields
from mcp import Client
from test_live_http import running_server
from test_mcp_http import call


def test_live_tag_navigation_without_embedding(postgres_url: str, tmp_path: Path) -> None:
    source = tmp_path / "样例.txt"
    original = "分部：正文\n标题：雨夜\n他在门边停下。\n她仍旧说着昨天的笑话。"
    source.write_text(original, encoding="utf-8")
    with running_server(postgres_url, tmp_path, "tag-navigation", mcp_profile="business") as rest:

        async def exercise() -> dict[str, str]:
            async with Client(str(rest.base_url).rstrip("/") + "/mcp") as mcp:
                tools = {tool.name for tool in (await mcp.list_tools()).tools}
                assert "reference_query" not in tools and "tag_update" in tools
                imported = await call(
                    mcp,
                    "prepare_import",
                    dict(request_id=str(uuid4()), name=str(uuid4()), file_path=str(source)),
                )
                key = dict(work_id=imported["work"]["id"], job_id=imported["job"]["id"])
                state = await call(mcp, "prepare_status", key)
                span = state["remaining"]["items"][0]["source_range"]
                saved = await call(
                    mcp,
                    "prepare_batch",
                    dict(
                        **key,
                        request_id=str(uuid4()),
                        expected_version=state["job"]["version"],
                        source_range=span,
                        marks=[
                            dict(
                                **annotation_fields([span]),
                                note="对话与动作错开",
                                tags=[
                                    dict(
                                        namespace="写法",
                                        name=str(uuid4()),
                                        description="话语与身体反应不同步",
                                        categories=["interaction", "emotion"],
                                    )
                                ],
                            )
                        ],
                        recovery={"next_action": "完成核对"},
                        outcome_note="已读",
                    ),
                )
                request = dict(view="categories", work_id=key["work_id"])
                categories = await call(mcp, "library_browse", request)
                assert categories == rest.post("/library/browse", json=request).json()
                assert {c["id"] for c in categories["categories"]} == {"interaction", "emotion"}
                request = dict(
                    view="tags", work_id=key["work_id"], category="interaction", format="full"
                )
                tags = await call(mcp, "library_browse", request)
                assert tags == rest.post("/library/browse", json=request).json()
                tag = tags["tag_page"]["items"][0]
                updated = await call(
                    mcp,
                    "tag_update",
                    dict(
                        request_id=str(uuid4()),
                        tag_id=tag["id"],
                        expected_version=tag["version"],
                        name=tag["name"],
                        description=tag["description"],
                        aliases=["话语动作错开"],
                        categories=["interaction", "language"],
                    ),
                )
                assert updated["result"]["categories"] == ["interaction", "language"]
                request = dict(view="annotations", work_ids=[key["work_id"]], tag_ids=[tag["id"]])
                marks = await call(mcp, "library_browse", request)
                assert marks == rest.post("/library/browse", json=request).json()
                mark = marks["annotations"]["items"][0]
                detail = await call(
                    mcp,
                    "library_browse",
                    dict(view="annotations", work_id=key["work_id"], annotation_id=mark["id"]),
                )
                assert detail["locations"][0]["section_title"] == "雨夜"
                read = await call(mcp, "source_read", mark["first_source_range"])
                assert [p["text"] for p in read["items"]] == [
                    "他在门边停下。",
                    "她仍旧说着昨天的笑话。",
                ]
                await call(
                    mcp,
                    "prepare_finish",
                    dict(
                        **key,
                        request_id=str(uuid4()),
                        expected_version=saved["version"],
                        recovery={"next_action": "完成"},
                        note="已核对",
                    ),
                )
                assert (await call(mcp, "prepare_status", key))["work_ready"]
                return key

        key = asyncio.run(exercise())
    with running_server(
        postgres_url, tmp_path, "tag-navigation-restart", mcp_profile="business"
    ) as rest:
        assert rest.post("/preparation/status", json=key).json()["work_ready"]
        assert rest.post("/reference/query", json={"query": "声音"}).status_code == 404
    assert source.read_text(encoding="utf-8") == original
