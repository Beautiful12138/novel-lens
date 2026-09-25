"""通过新接口准备单分部作品，供已有资产和阅读行为用例复用。"""

from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from mcp import Client

from novel_lens.catalog import CatalogService
from novel_lens.contracts import ImportOut, WorkCreate
from novel_lens.importing import ImportService
from novel_lens.source import parse_canonical


def import_work(service: ImportService, data: bytes, key: UUID) -> ImportOut:
    """夹具显式创建作品再导入；生产导入不提供自动创建行为。"""
    work = (
        CatalogService(service.database)
        .create(
            WorkCreate(request_id=uuid5(key, "fixture-work"), name=parse_canonical(data).name.text)
        )
        .result
    )
    return service.import_source(data, key, work.id)


def http_work(client: Any, name: str | None = None) -> str:
    response = client.post(
        "/works", json={"request_id": str(uuid4()), "name": name or str(uuid4())}
    )
    assert response.status_code == 201, response.text
    return str(response.json()["result"]["id"])


def http_import(client: Any, **kwargs: Any) -> Any:
    """用原键派生独立的作品创建键，保持测试跨入口重试同一导入的能力。"""
    key = str(kwargs.get("data", {}).get("request_id", uuid4()))
    created = client.post(
        "/works", json={"request_id": str(uuid5(NAMESPACE_URL, key)), "name": f"fixture-{key}"}
    )
    assert created.status_code in (200, 201), created.text
    work_id = created.json()["result"]["id"]
    return client.post(f"/works/{work_id}/part-imports", **kwargs)


async def mcp_import(client: Client, arguments: dict[str, Any]) -> dict[str, Any]:
    from test_mcp_http import call

    key = str(arguments["request_id"])
    created = await call(
        client,
        "work_create",
        {"request_id": str(uuid5(NAMESPACE_URL, key)), "name": f"fixture-{key}"},
    )
    return await call(client, "part_import", {**arguments, "work_id": created["result"]["id"]})


def http_file(client: Any, work_id: Any) -> Any:
    """单部测试夹具通过分部目录找到真实下载入口。"""
    listing = client.get(f"/works/{work_id}/parts")
    if listing.status_code != 200:
        return listing
    part_id = listing.json()["items"][0]["id"]
    return client.get(f"/works/{work_id}/parts/{part_id}/file")
