"""真实 0002 数据升级及旧指纹、快照、游标兼容；样例由旧提交代码生成。"""

import asyncio
import json
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from alembic import command
from alembic.config import Config
from conftest import ROOT, temporary_database
from mcp import Client
from pydantic import SecretStr, TypeAdapter
from sqlalchemy import text
from test_entities_relations import entity
from test_live_http import running_server
from test_mcp_http import call

from novel_lens.asset_contracts import (
    AnnotationCreate,
    AnnotationGet,
    AnnotationList,
    AnnotationOut,
    AnnotationUpdate,
    AssetWriteOut,
    TagCreate,
)
from novel_lens.assets import AssetService
from novel_lens.config import Settings
from novel_lens.database import Database
from novel_lens.errors import ServiceError


def test_upgrade_real_0002_records_and_wire_compatibility(
    postgres_url: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fixture = json.loads(
        (Path(__file__).parent / "fixtures/assets_0002.json").read_text(encoding="utf-8")
    )
    with temporary_database(postgres_url, "0002") as url:
        database = Database(Settings.model_construct(database_url=SecretStr(url)))
        try:
            # 样例来自固定旧代码真实入库结果，不用当前模型伪造旧指纹或旧快照。
            with database.engine.begin() as connection:
                for name, rows in fixture["tables"].items():
                    connection.execute(
                        text(
                            f"INSERT INTO {name} SELECT * FROM "
                            f"jsonb_populate_recordset(NULL::{name}, CAST(:rows AS jsonb))"
                        ),
                        {"rows": json.dumps(rows)},
                    )
            with monkeypatch.context() as patch:
                patch.setenv("NOVEL_LENS_DATABASE_URL", url)
                config = Config(str(ROOT / "alembic.ini"))
                command.upgrade(config, "head")
                command.check(config)
            with database.engine.connect() as connection:
                assert (
                    connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                    == "0009"
                )
                for name, expected in fixture["tables"].items():
                    actual = (
                        connection.execute(text(f"SELECT to_jsonb(t) FROM {name} t"))
                        .scalars()
                        .all()
                    )
                    if name == "tags":
                        assert all(
                            r["version"] == 1 and r["updated_at"] == r["created_at"] for r in actual
                        )
                        actual = [
                            {k: v for k, v in r.items() if k not in {"version", "updated_at"}}
                            for r in actual
                        ]
                    elif name == "annotations":
                        assert all(r["status"] == "active" for r in actual)
                        actual = [{k: v for k, v in r.items() if k != "status"} for r in actual]
                    assert sorted(json.dumps(row, sort_keys=True) for row in actual) == sorted(
                        json.dumps(row, sort_keys=True) for row in expected
                    )
                assert connection.execute(
                    text("SELECT content FROM work_sources")
                ).scalar_one() == bytes.fromhex(fixture["source_hex"])
            assets = AssetService(database)
            for saved in fixture["requests"]:
                match saved["operation"]:
                    case "tag_create":
                        result = assets.create_tag(TagCreate.model_validate(saved["input"]))
                    case "annotation_create":
                        result = assets.create_annotation(
                            AnnotationCreate.model_validate(saved["input"])
                        )
                    case "annotation_update":
                        result = assets.update_annotation(
                            AnnotationUpdate.model_validate(saved["input"])
                        )
                    case _:
                        raise AssertionError("未知样例操作")
                expected = saved["response"] | {"replayed": True}
                assert result.model_dump(mode="json") == expected
                assert assets.write_result(result.request_id).model_dump(mode="json") == expected
                assert (
                    TypeAdapter(AssetWriteOut).validate_python(expected).model_dump(mode="json")
                    == expected
                )
            query = AnnotationList.model_validate(
                fixture["query"] | {"cursor": fixture["cursor"], "entity_ids": []}
            )
            assert [str(a.id) for a in assets.list_annotations(query).items] == [fixture["next_id"]]
            identifier = AnnotationGet(
                work_id=query.work_id, annotation_id=UUID(fixture["annotation_id"])
            )
            current = assets.get_annotation(identifier)
            assert current.entity_ids == [] and current.version == 2
            e = entity(assets, query.work_id)
            latest_request = AnnotationUpdate(
                request_id=uuid4(),
                work_id=current.work_id,
                annotation_id=current.id,
                expected_version=current.version,
                source_ranges=current.source_ranges,
                tag_ids=current.tag_ids,
                note=current.note,
                entity_ids=[e.id],
            )
            latest = assets.update_annotation(latest_request).result
            assert isinstance(latest, AnnotationOut) and latest.version == 3
            old_create, old_update = fixture["requests"][1:]
            assert assets.create_annotation(
                AnnotationCreate.model_validate(old_create["input"] | {"entity_ids": []})
            ).model_dump(mode="json") == old_create["response"] | {"replayed": True}
            assert assets.update_annotation(
                AnnotationUpdate.model_validate(old_update["input"])
            ).model_dump(mode="json") == old_update["response"] | {"replayed": True}
            assert assets.get_annotation(identifier) == latest
            # 新请求仍按旧字段集调用时保留关联；显式空数组则是另一项新版操作。
            legacy_input = old_update["input"] | {
                "request_id": str(uuid4()),
                "expected_version": 3,
                "note": "旧客户端新修改",
            }
            preserved = assets.update_annotation(
                AnnotationUpdate.model_validate(legacy_input)
            ).result
            assert isinstance(preserved, AnnotationOut) and preserved.entity_ids == [e.id]
            with pytest.raises(ServiceError) as conflict:
                assets.update_annotation(
                    AnnotationUpdate.model_validate(legacy_input | {"entity_ids": []})
                )
            assert conflict.value.code == "REQUEST_CONFLICT"
            with pytest.raises(ServiceError) as cursor:
                assets.list_annotations(query.model_copy(update={"entity_ids": [e.id]}))
            assert cursor.value.code == "INVALID_CURSOR"

            async def recover_over_mcp(rest: httpx.Client) -> None:
                """真实协议仍接受历史结果格式，恢复不能添加新版字段。"""
                async with Client(str(rest.base_url).rstrip("/") + "/mcp") as mcp:
                    for saved in fixture["requests"]:
                        expected = saved["response"] | {"replayed": True}
                        result = await call(
                            mcp, "asset_write_get", {"request_id": saved["input"]["request_id"]}
                        )
                        assert result == expected
                        assert await call(mcp, saved["operation"], saved["input"]) == expected

            with running_server(url, tmp_path, "legacy-upgrade") as rest:
                asyncio.run(recover_over_mcp(rest))
        finally:
            database.close()
