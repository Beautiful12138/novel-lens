"""官方 MCP 客户端和真实服务进程验证实体、关系、撤回及重启恢复。"""

import asyncio
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from mcp import Client
from test_live_http import running_server
from test_mcp_http import call


def test_entity_relation_tools_and_restart(postgres_url: str, tmp_path: Path) -> None:
    novel = tmp_path / "关系样例.txt"
    data = (
        f"书名：{uuid4()}\n标题：建立\nprivate-relation-source\n第二段\n标题：回收\n末段".encode()
    )
    novel.write_bytes(data)

    async def exercise(rest: httpx.Client) -> dict[str, Any]:
        async with Client(str(rest.base_url).rstrip("/") + "/mcp") as mcp:
            listing = (await mcp.list_tools()).tools
            assert len(listing) == 48
            by_name = {t.name: t for t in listing}
            assert by_name["relation_set_status"].annotations.destructive_hint  # type: ignore[union-attr]
            work = (
                await call(
                    mcp, "work_import", {"request_id": str(uuid4()), "file_path": str(novel)}
                )
            )["work"]
            work_id = work["id"]
            sections = (await call(mcp, "source_sections", {"work_id": work_id}))["items"]
            ranges = []
            for section in sections:
                paragraphs = (
                    await call(
                        mcp, "source_paragraphs", {"work_id": work_id, "section_id": section["id"]}
                    )
                )["items"]
                ranges.append(
                    dict(
                        work_id=work_id,
                        section_id=section["id"],
                        start_paragraph_id=paragraphs[0]["id"],
                        end_paragraph_id=paragraphs[-1]["id"],
                    )
                )
            entities = []
            for kind in ("character", "location", "item", "organization", "concept"):
                e = (
                    await call(
                        mcp,
                        "entity_create",
                        dict(
                            request_id=str(uuid4()),
                            work_id=work_id,
                            type=kind,
                            canonical_name="共同名称",
                            aliases=["ALIAS", "别名", "ALIAS"],
                        ),
                    )
                )["result"]
                entities.append(e)
                assert e["aliases"] == ["ALIAS", "别名"]
            assert (await call(mcp, "entity_list", {"work_id": work_id}))["items"] == entities
            assert (
                await call(
                    mcp,
                    "entity_search",
                    {"work_id": work_id, "query": "alias", "type": "character"},
                )
            )["items"] == [entities[0]]
            e = entities[0]
            entity_id = dict(work_id=work_id, entity_id=e["id"])
            assert await call(mcp, "entity_get", entity_id) == e
            update_entity = entity_id | dict(
                request_id=str(uuid4()),
                expected_version=1,
                type="character",
                canonical_name="新名称",
                aliases=[],
                note=None,
            )
            updated_entity = (await call(mcp, "entity_update", update_entity))["result"]
            assert updated_entity["version"] == 2
            await call(
                mcp, "entity_get", entity_id | {"work_id": str(uuid4())}, code="ENTITY_NOT_FOUND"
            )
            create_annotation = dict(
                request_id=str(uuid4()),
                work_id=work_id,
                source_ranges=[ranges[0]],
                entity_ids=[e["id"], e["id"]],
            )
            a = (await call(mcp, "annotation_create", create_annotation))["result"]
            assert a["entity_ids"] == [e["id"]]
            a = (
                await call(
                    mcp,
                    "annotation_update",
                    dict(
                        request_id=str(uuid4()),
                        work_id=work_id,
                        annotation_id=a["id"],
                        expected_version=1,
                        source_ranges=ranges,
                        tag_ids=[],
                        note="旧调用省略实体集合",
                    ),
                )
            )["result"]
            assert a["entity_ids"] == [e["id"]]
            summaries = (
                await call(
                    mcp,
                    "annotation_list",
                    {"work_id": work_id, "entity_ids": [e["id"]], "format": "full"},
                )
            )["items"]
            assert summaries[0]["entity_count"] == 1
            create_relation = dict(
                request_id=str(uuid4()),
                work_id=work_id,
                title="跨章关联",
                relation_type="回收",
                nodes=[
                    dict(source_range=r, role=role)
                    for r, role in zip(ranges, ["建立", "回收"], strict=True)
                ],
                entity_ids=[e["id"]],
                note="private-relation-note\n依据明确，有适用边界。",
            )
            r = (await call(mcp, "relation_create", create_relation))["result"]
            assert r["status"] == "active" and [n["ordinal"] for n in r["nodes"]] == [1, 2]
            identifier = dict(work_id=work_id, relation_id=r["id"])
            detail = await call(mcp, "relation_get", identifier)
            assert "nodes" not in detail and detail["node_count"] == 2
            page = await call(
                mcp, "relation_expand", identifier | dict(expected_version=1, limit=1)
            )
            assert page["items"] == [r["nodes"][0]]
            tail = await call(
                mcp,
                "relation_expand",
                identifier | dict(expected_version=1, cursor=page["next_cursor"]),
            )
            assert tail["items"] == [r["nodes"][1]]
            raw = await call(mcp, "source_read", {"source_range": tail["items"][0]["source_range"]})
            assert raw["items"][0]["text"] == "末段"
            found = await call(
                mcp,
                "relation_search",
                {"work_id": work_id, "entity_ids": [e["id"]], "source_range": ranges[1]},
            )
            assert len(found["items"]) == 1 and found["items"][0]["id"] == r["id"]
            update_relation = (
                create_relation
                | identifier
                | dict(
                    request_id=str(uuid4()),
                    expected_version=1,
                    tag_ids=[],
                    nodes=list(reversed(create_relation["nodes"])),
                )
            )
            changed = (await call(mcp, "relation_update", update_relation))["result"]
            assert changed["nodes"][0]["source_range"] == ranges[1]
            withdraw = identifier | dict(
                request_id=str(uuid4()), expected_version=2, status="withdrawn"
            )
            withdrawn = (await call(mcp, "relation_set_status", withdraw))["result"]
            assert (await call(mcp, "relation_search", {"work_id": work_id}))["items"] == []
            assert (
                await call(mcp, "relation_search", {"work_id": work_id, "status": "withdrawn"})
            )["items"][0]["id"] == r["id"]
            assert (await call(mcp, "relation_get", identifier))["status"] == "withdrawn"
            assert (await call(mcp, "relation_expand", identifier | {"expected_version": 3}))[
                "items"
            ] == changed["nodes"]
            await call(
                mcp,
                "relation_expand",
                identifier | dict(expected_version=1, cursor=page["next_cursor"]),
                code="VERSION_CONFLICT",
            )
            await call(
                mcp,
                "relation_expand",
                identifier | dict(expected_version=3, cursor=page["next_cursor"]),
                code="INVALID_CURSOR",
            )
            restore = identifier | dict(
                request_id=str(uuid4()), expected_version=3, status="active"
            )
            restored = (await call(mcp, "relation_set_status", restore))["result"]
            assert restored["nodes"] == changed["nodes"] and restored["version"] == 4
            assert (await call(mcp, "relation_set_status", withdraw))["result"] == withdrawn
            await call(
                mcp,
                "relation_get",
                identifier | {"work_id": str(uuid4())},
                code="RELATION_NOT_FOUND",
            )
            await call(
                mcp,
                "relation_set_status",
                restore | {"request_id": str(uuid4())},
                code="VERSION_CONFLICT",
            )
            invalids = [
                (
                    "entity_create",
                    dict(
                        request_id=str(uuid4()),
                        work_id=work_id,
                        type="unknown",
                        canonical_name="名称",
                    ),
                ),
                ("entity_search", {"work_id": work_id, "query": "　 "}),
                ("entity_update", update_entity | {"expected_version": True}),
                ("annotation_create", create_annotation | {"entity_ids": None}),
                ("relation_create", create_relation | {"nodes": create_relation["nodes"][:1]}),
                ("relation_create", create_relation | {"nodes": [create_relation["nodes"][0]] * 2}),
                ("relation_create", create_relation | {"note": "\x00"}),
                (
                    "relation_create",
                    create_relation
                    | {
                        "nodes": [
                            dict(source_range=ranges[0], role="a" * 65),
                            create_relation["nodes"][1],
                        ]
                    },
                ),
                (
                    "relation_create",
                    create_relation
                    | {
                        "nodes": [
                            dict(create_relation["nodes"][0], ordinal=5),
                            create_relation["nodes"][1],
                        ]
                    },
                ),
                ("relation_set_status", restore | {"status": "deleted"}),
                ("relation_set_status", restore | {"note": "private-unknown-status"}),
                ("relation_expand", identifier | {"expected_version": "4"}),
                ("relation_search", {"work_id": work_id, "query": " "}),
            ]
            for name, request in invalids:
                await call(mcp, name, request, code="INVALID_INPUT")
            oversized = create_relation | dict(request_id=str(uuid4()), note="a" * (1024 * 1024))
            await call(mcp, "relation_create", oversized, code="ASSET_TOO_LARGE")
            await call(
                mcp,
                "asset_write_get",
                {"request_id": oversized["request_id"]},
                code="WRITE_NOT_COMMITTED",
            )
            for name in ("大说明甲", "大说明乙"):
                await call(
                    mcp,
                    "entity_create",
                    dict(
                        request_id=str(uuid4()),
                        work_id=work_id,
                        type="concept",
                        canonical_name=name,
                        note="a" * 600000,
                    ),
                )
            await call(mcp, "entity_list", {"work_id": work_id}, code="RESULT_TOO_LARGE")
            assert (
                len((await call(mcp, "entity_list", {"work_id": work_id, "limit": 1}))["items"])
                == 1
            )
            assert rest.get(f"/works/{work_id}/file").content == data
            return dict(
                identifier=identifier,
                entity_id=entity_id,
                requests=[
                    ("entity_update", update_entity, updated_entity),
                    ("relation_create", create_relation, r),
                    ("relation_update", update_relation, changed),
                    ("relation_set_status", withdraw, withdrawn),
                    ("relation_set_status", restore, restored),
                ],
                restored=restored,
            )

    with running_server(postgres_url, tmp_path, "relations") as rest:
        saved = asyncio.run(exercise(rest))

    async def recover(rest: httpx.Client) -> None:
        async with Client(str(rest.base_url).rstrip("/") + "/mcp") as mcp:
            for name, request, result in saved["requests"]:
                recovered = await call(
                    mcp, "asset_write_get", {"request_id": request["request_id"]}
                )
                assert recovered["result"] == result and recovered["replayed"]
                assert (await call(mcp, name, request))["result"] == result
            detail = await call(mcp, "relation_get", saved["identifier"])
            assert detail["version"] == 4 and detail["status"] == "active"
            assert (
                await call(mcp, "relation_expand", saved["identifier"] | {"expected_version": 4})
            )["items"] == saved["restored"]["nodes"]

    with running_server(postgres_url, tmp_path, "relations-restart") as rest:
        asyncio.run(recover(rest))
    for label in ("relations", "relations-restart"):
        assert (tmp_path / f"{label}.stdout").read_bytes() == b""
        log = (tmp_path / f"{label}.stderr").read_text(encoding="utf-8")
        for private in (
            "private-relation-source",
            "private-relation-note",
            "private-unknown-status",
            "INSERT INTO",
            "UPDATE relations",
        ):
            assert private not in log
    assert novel.read_bytes() == data
