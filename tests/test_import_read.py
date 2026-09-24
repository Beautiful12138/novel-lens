"""真实 PostgreSQL 上验证事务、幂等、隔离与 HTTP 原文保真。"""

from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from threading import Barrier
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text
from starlette.testclient import TestClient

from novel_lens.database import Database
from novel_lens.errors import ServiceError
from novel_lens.importing import ImportService


def sample(name: str | None = None) -> bytes:
    return (
        f"\ufeff书名：{name or uuid4()}\r\n标题：重复标题\r"
        "　首段😀 \t\n第二段\r\n第三段\r标题：重复标题\n标题：结尾\n末段"
    ).encode()


def imported(client: TestClient, data: bytes | None = None) -> dict[str, Any]:
    response = client.post(
        "/work-imports",
        files={"file": ("book.txt", data or sample())},
        data={"request_id": str(uuid4())},
    )
    assert response.status_code == 201, response.text
    result: dict[str, Any] = response.json()
    return result


def test_upload_read_download_and_idempotency(client: TestClient) -> None:
    data = sample()
    assert (
        client.post("/work-imports/validate", files={"file": ("书.txt", data)}).json()["status"]
        == "valid"
    )
    result = imported(client, data)
    work = result["work"]
    work_id = work["id"]
    assert (work["section_count"], work["paragraph_count"]) == (3, 4)
    assert work["source_sha256"] == sha256(data).hexdigest()
    assert work["source_bytes"] == len(data)
    assert client.get(f"/works/{work_id}/file").content == data
    sections = client.get(f"/works/{work_id}/sections").json()["items"]
    assert [s["ordinal"] for s in sections] == [1, 2, 3]
    assert sections[0]["title"] == sections[1]["title"]
    paragraph_url = f"/works/{work_id}/sections/{sections[0]['id']}/paragraphs"
    first = client.get(paragraph_url, params={"limit": 1, "format": "full"}).json()
    second = client.get(
        paragraph_url, params={"limit": 2, "cursor": first["next_cursor"], "format": "full"}
    ).json()
    paragraphs = first["items"] + second["items"]
    assert [p["ordinal"] for p in paragraphs] == [1, 2, 3]
    assert second["next_cursor"] is None
    for paragraph in paragraphs:
        position = paragraph["source_position"]
        assert data[position["start_byte"] : position["end_byte"]].decode() == paragraph["text"]
    assert work["character_count"] == sum(len(p["text"]) for p in paragraphs) + len("末段")
    empty = client.get(
        f"/works/{work_id}/sections/{sections[1]['id']}/paragraphs", params={"format": "full"}
    ).json()
    assert empty == {"items": [], "next_cursor": None}
    replay = client.post(
        "/work-imports",
        files={"file": ("renamed.txt", data)},
        data={"request_id": result["request_id"]},
    )
    assert replay.status_code == 200
    assert replay.json()["replayed"] is True
    assert replay.json()["work"] == work
    assert client.get(paragraph_url, params={"format": "full"}).json()["items"] == paragraphs
    assert client.get(f"/work-imports/{result['request_id']}").json()["work"] == work
    conflict = client.post(
        "/work-imports",
        files={"file": ("book.txt", data + b"changed")},
        data={"request_id": result["request_id"]},
    )
    assert (conflict.status_code, conflict.json()["code"]) == (409, "REQUEST_CONFLICT")
    conflict = client.post(
        "/work-imports", files={"file": ("book.txt", data)}, data={"request_id": str(uuid4())}
    )
    assert (conflict.status_code, conflict.json()["code"]) == (409, "WORK_NAME_CONFLICT")
    report = client.post("/work-imports/validate", files={"file": ("book.txt", data)}).json()
    assert report["issues"][0]["code"] == "WORK_NAME_CONFLICT"


def test_ranges_context_and_scope_isolation(client: TestClient) -> None:
    work_id = imported(client)["work"]["id"]
    other_work = imported(client)["work"]["id"]
    seen: list[str] = []
    cursor = None
    while True:
        params: dict[str, str | int] = {"limit": 2}
        if cursor is not None:
            params["cursor"] = cursor
        works_page = client.get("/works", params=params).json()
        seen.extend(w["id"] for w in works_page["items"])
        cursor = works_page["next_cursor"]
        if cursor is None:
            break
    assert len(seen) == len(set(seen))
    assert {work_id, other_work}.issubset(seen)
    directory_url = f"/works/{work_id}/sections"
    page = client.get(directory_url, params={"limit": 1}).json()
    rest = client.get(directory_url, params={"limit": 2, "cursor": page["next_cursor"]}).json()
    assert [s["ordinal"] for s in page["items"] + rest["items"]] == [1, 2, 3]
    section_id = page["items"][0]["id"]
    url = f"/works/{work_id}/sections/{section_id}/paragraphs"
    paragraphs = client.get(url).json()["items"]
    source_range = {
        "work_id": work_id,
        "section_id": section_id,
        "start_paragraph_id": paragraphs[0]["id"],
        "end_paragraph_id": paragraphs[2]["id"],
    }
    read_url = f"/works/{work_id}/source/read"
    result = client.post(read_url, json={"source_range": source_range, "limit": 2}).json()
    assert result["items"] == paragraphs[:2]
    assert result["actual_range"]["end_paragraph_id"] == paragraphs[1]["id"]
    assert {"work_id": result["work_id"], "section_id": result["section_id"]} | result[
        "requested_range"
    ] == source_range
    last = client.post(
        read_url, json={"source_range": source_range, "cursor": result["next_cursor"]}
    ).json()
    assert last["items"] == paragraphs[2:]
    assert last["next_cursor"] is None
    context = client.post(
        f"/works/{work_id}/source/context",
        json={
            "section_id": section_id,
            "paragraph_id": paragraphs[1]["id"],
            "before": 100,
            "after": 100,
        },
    ).json()
    assert context["items"] == paragraphs
    assert context["at_section_start"] and context["at_section_end"]
    assert client.get(f"/works/{other_work}/sections/{section_id}/paragraphs").status_code == 404
    assert (
        client.post(
            f"/works/{other_work}/source/read", json={"source_range": source_range}
        ).status_code
        == 422
    )
    assert (
        client.get(
            f"/works/{other_work}/sections", params={"cursor": page["next_cursor"]}
        ).status_code
        == 422
    )
    assert client.get(url, params={"cursor": result["next_cursor"]}).status_code == 422
    reversed_range = source_range | {
        "start_paragraph_id": paragraphs[2]["id"],
        "end_paragraph_id": paragraphs[0]["id"],
    }
    assert client.post(read_url, json={"source_range": reversed_range}).status_code == 422
    foreign = client.get(f"/works/{work_id}/sections/{rest['items'][1]['id']}/paragraphs").json()[
        "items"
    ][0]["id"]
    assert (
        client.post(
            read_url, json={"source_range": source_range | {"end_paragraph_id": foreign}}
        ).status_code
        == 404
    )
    assert client.get(url, params={"limit": 1001}).status_code == 422
    assert client.get(url, params={"cursor": "garbage"}).status_code == 422


def test_invalid_input_creates_no_result_and_does_not_echo(client: TestClient) -> None:
    key = str(uuid4())
    bad = b"private-body\xff"
    report = client.post("/work-imports/validate", files={"file": ("bad.txt", bad)}).json()
    assert report["status"] == "invalid"
    assert report["issues"][0]["source_position"]["start_byte"] == len(bad) - 1
    response = client.post(
        "/work-imports", files={"file": ("bad.txt", bad)}, data={"request_id": key}
    )
    assert response.status_code == 422
    assert "private-body" not in response.text
    assert client.get(f"/work-imports/{key}").json()["details"]["status"] == "not_committed"
    for fields in [
        [("file", ("a.txt", sample())), ("file", ("b.txt", sample()))],
        [("file", ("a.txt", sample())), ("original_file", ("b.txt", b"private-body"))],
    ]:
        assert (
            client.post("/work-imports", files=fields, data={"request_id": key}).status_code == 422
        )
    response = client.post(
        "/work-imports",
        files={"file": ("a.txt", sample())},
        data={"request_id": key, "confirmed": "true"},
    )
    assert response.status_code == 422
    response = client.post(f"/works/{uuid4()}/source/read", json={"private-body": "private-body"})
    assert response.status_code == 422 and "private-body" not in response.text


@pytest.mark.parametrize("same_key", [True, False])
def test_concurrent_imports(database: Database, same_key: bool) -> None:
    service = ImportService(database, 1024 * 1024)
    data = sample()
    key = uuid4()
    barrier = Barrier(2)

    def perform(number: int) -> str:
        barrier.wait(timeout=10)
        try:
            result = service.import_source(data, key if same_key or number == 0 else uuid4())
            return str(result.work.id)
        except ServiceError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(perform, range(2)))
    if same_key:
        assert results[0] == results[1] == str(service.result(key).work.id)
    else:
        assert results.count("WORK_NAME_CONFLICT") == 1


def test_mid_transaction_failure_rolls_back_everything(
    client: TestClient, database: Database
) -> None:
    marker = "rollback-" + uuid4().hex
    with database.engine.begin() as connection:
        connection.execute(
            text("""CREATE FUNCTION reject_test_paragraph() RETURNS trigger AS $$
        BEGIN IF NEW.text LIKE 'rollback-%' THEN RAISE EXCEPTION 'injected private-body'; END IF;
        RETURN NEW; END; $$ LANGUAGE plpgsql;
        CREATE TRIGGER reject_test BEFORE INSERT ON paragraphs FOR EACH ROW
        EXECUTE FUNCTION reject_test_paragraph();""")
        )
        before = connection.execute(
            text(
                "SELECT (SELECT count(*) FROM works), "
                "(SELECT count(*) FROM work_sources), "
                "(SELECT count(*) FROM sections), "
                "(SELECT count(*) FROM paragraphs)"
            )
        ).one()
    key = str(uuid4())
    data = f"书名：{uuid4()}\n标题：章\n正常段\n{marker}".encode()
    try:
        response = client.post(
            "/work-imports", files={"file": ("a.txt", data)}, data={"request_id": key}
        )
        assert response.status_code == 500
        assert "private-body" not in response.text and marker not in response.text
        assert client.get(f"/work-imports/{key}").status_code == 404
        with database.engine.connect() as connection:
            after = connection.execute(
                text(
                    "SELECT (SELECT count(*) FROM works), "
                    "(SELECT count(*) FROM work_sources), "
                    "(SELECT count(*) FROM sections), "
                    "(SELECT count(*) FROM paragraphs)"
                )
            ).one()
        assert before == after
    finally:
        with database.engine.begin() as connection:
            connection.execute(
                text(
                    "DROP TRIGGER reject_test ON paragraphs; DROP FUNCTION reject_test_paragraph()"
                )
            )
    assert (
        client.post(
            "/work-imports", files={"file": ("a.txt", data)}, data={"request_id": key}
        ).status_code
        == 201
    )
