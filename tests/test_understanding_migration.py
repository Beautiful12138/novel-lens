"""在独立旧版数据库验证不兼容升级的清理边界和保留数据。"""

import os
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from conftest import ROOT, temporary_database
from psycopg import sql
from psycopg.types.json import Jsonb
from sqlalchemy.engine import make_url


def _connect(url: str) -> psycopg.Connection:
    return psycopg.connect(
        make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)
    )


def _seed(conn: psycopg.Connection) -> dict[str, UUID]:
    """直接构造 0015 合法数据，避免运行时的新标注契约污染迁移测试。"""
    ids = {
        key: uuid4()
        for key in (
            "work",
            "part",
            "section",
            "paragraph",
            "annotation",
            "entity",
            "relation",
            "tag",
            "navigation",
            "job",
            "fulltext",
            "annotation_index",
        )
    }

    def insert(table: str, **values: object) -> None:
        conn.execute(
            sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
                sql.Identifier(table),
                sql.SQL(",").join(map(sql.Identifier, values)),
                sql.SQL(",").join(sql.Placeholder() for _ in values),
            ),
            list(values.values()),
        )

    w, s, p = ids["work"], ids["section"], ids["paragraph"]
    digest = "a" * 64
    source = "原文不可变。".encode()
    insert(
        "works",
        id=w,
        name="迁移样例",
        version=1,
        part_count=1,
        section_count=1,
        paragraph_count=1,
        character_count=6,
        source_sha256=digest,
        source_bytes=len(source),
    )
    insert(
        "parts",
        id=ids["part"],
        work_id=w,
        name="正文",
        ordinal=1,
        version=1,
        section_count=1,
        paragraph_count=1,
        character_count=6,
        source_sha256=digest,
        source_bytes=len(source),
    )
    insert(
        "part_sources",
        part_id=ids["part"],
        content=source,
        rule_version="test",
        layout=Jsonb({"original": True}),
    )
    insert(
        "sections",
        id=s,
        work_id=w,
        part_id=ids["part"],
        ordinal=1,
        title="第一章",
        paragraph_count=1,
    )
    insert(
        "paragraphs",
        id=p,
        section_id=s,
        ordinal=1,
        text=source.decode(),
        source_position=Jsonb({"start_byte": 0, "end_byte": len(source), "line": 1}),
    )
    for key, namespace, name in (("tag", "写法", "叙述"), ("navigation", "导航", "作品写法")):
        insert(
            "tags",
            id=ids[key],
            namespace=namespace,
            name=name,
            description="保留普通词表",
            aliases=Jsonb([]),
            categories=Jsonb(["language"]),
        )
        insert("preparation_tags", work_id=w, tag_id=ids[key])
    insert("annotations", id=ids["annotation"], work_id=w, note="旧说明", version=3)
    span = dict(work_id=w, section_id=s, start_paragraph_id=p, end_paragraph_id=p)
    insert("annotation_ranges", annotation_id=ids["annotation"], ordinal=1, **span)
    insert("annotation_tags", annotation_id=ids["annotation"], tag_id=ids["tag"])
    insert(
        "entities",
        id=ids["entity"],
        work_id=w,
        type="character",
        canonical_name="人物",
        aliases=Jsonb([]),
        note="保留",
        version=1,
    )
    insert("annotation_entities", annotation_id=ids["annotation"], entity_id=ids["entity"])
    insert(
        "relations",
        id=ids["relation"],
        work_id=w,
        title="关系保留",
        relation_type="comparison",
        note="独立资产",
        status="active",
        version=1,
    )
    insert("relation_nodes", relation_id=ids["relation"], ordinal=1, **span)
    insert("relation_entities", relation_id=ids["relation"], entity_id=ids["entity"])
    insert("relation_tags", relation_id=ids["relation"], tag_id=ids["tag"])
    insert("style_guides", work_id=w, scope_note="旧认识", version=2)
    insert(
        "style_guide_entries",
        work_id=w,
        ordinal=1,
        title="旧条目",
        kind="baseline",
        description="旧判断",
        applicability="本章",
    )
    insert("style_guide_ranges", entry_ordinal=1, ordinal=1, **span)
    insert(
        "analysis_jobs",
        id=ids["job"],
        work_id=w,
        title="旧任务",
        goal="阅读",
        target_kind="whole_work",
        status="completed",
        recovery=Jsonb({"next_action": "已完成"}),
        completion=Jsonb({"style_guide_version": 2}),
        version=7,
    )
    insert(
        "analysis_targets",
        job_id=ids["job"],
        ordinal=1,
        section_id=s,
        start_paragraph_id=p,
        end_paragraph_id=p,
        start_ordinal=1,
        end_ordinal=1,
    )
    insert("analysis_coverage", job_id=ids["job"], paragraph_id=p, status="processed")
    vector = "[1," + ",".join("0" for _ in range(1023)) + "]"
    for key, kind in (("fulltext", "fulltext"), ("annotation_index", "annotation")):
        index_id = ids[key]
        insert(
            "semantic_indexes",
            id=index_id,
            work_id=w,
            kind=kind,
            request_id=uuid4(),
            fingerprint=digest,
            contract_id=digest,
            source_sha256=digest,
            status="ready",
            cursor=Jsonb({}),
            scanned=True,
            create_response=Jsonb({"id": str(index_id)}),
        )
        insert(
            "semantic_index_heads",
            work_id=w,
            kind=kind,
            active_index_id=index_id,
            target_index_id=index_id,
        )
        extra = {}
        if kind == "annotation":
            insert(
                "semantic_annotation_snapshots",
                index_id=index_id,
                annotation_id=ids["annotation"],
                work_id=w,
                evidence_id=digest,
                ranges=Jsonb([{k: str(v) for k, v in span.items()}]),
            )
            extra = dict(annotation_id=ids["annotation"], evidence_id=digest, range_ordinal=1)
        insert(
            "semantic_index_items",
            id=uuid4(),
            index_id=index_id,
            kind=kind,
            source_key=digest,
            start_ordinal=1,
            end_ordinal=1,
            text_sha256=digest,
            tokens=6,
            bytes=len(source),
            embedding=vector,
            **span,
            **extra,
        )
        insert(
            "semantic_build_receipts",
            index_id=index_id,
            request_id=uuid4(),
            fingerprint=digest,
            response=Jsonb({"kind": kind}),
        )
    insert(
        "reference_clues",
        annotation_id=ids["annotation"],
        work_id=w,
        fingerprint=digest,
        contract_id=digest,
        body="旧标注线索",
        embedding=vector,
    )
    conn.execute(
        "INSERT INTO reference_searches(id,slot,snapshot_at,expires_at,payload) "
        "VALUES (%s,1,now(),now()+interval '1 hour','{}')",
        (uuid4(),),
    )
    for operation in (
        "annotation_create",
        "annotation_update",
        "annotation_set_status",
        "style_guide_create",
        "style_guide_update",
        "analysis_checkpoint",
        "analysis_job_create",
        "analysis_job_update",
        "analysis_job_complete",
        "coverage_mark",
        "entity_create",
        "relation_create",
    ):
        insert(
            "asset_write_requests",
            request_id=uuid4(),
            fingerprint=digest,
            response=Jsonb({"operation": operation, "result": {}}),
        )
    for key in ("tag", "navigation"):
        insert(
            "asset_write_requests",
            request_id=uuid4(),
            fingerprint=digest,
            response=Jsonb({"operation": "tag_create", "result": {"id": str(ids[key])}}),
        )
    for operation in (
        "work_create",
        "part_import",
        "prepare_import",
        "prepare_batch",
        "prepare_finish",
    ):
        insert(
            "catalog_requests",
            request_id=uuid4(),
            work_id=w,
            operation=operation,
            fingerprint=digest,
            response=Jsonb({"job": {"version": 7}}),
        )
    return ids


def _snapshot(
    conn: psycopg.Connection, table: str, condition: str = "true"
) -> list[tuple[Any, ...]]:
    """比较完整行，包括稳定 ID、字节、向量及回执，而不是只比较数量。"""
    return conn.execute(
        sql.SQL(
            "SELECT to_jsonb(t) FROM {} t WHERE " + condition + " ORDER BY to_jsonb(t)::text"
        ).format(sql.Identifier(table))
    ).fetchall()


@pytest.mark.parametrize("navigation_used", [False, True])
def test_0016_discards_analysis_preserves_sources_and_rejects_downgrade(
    monkeypatch: pytest.MonkeyPatch,
    navigation_used: bool,
) -> None:
    raw = os.environ.get("NOVEL_LENS_TEST_DATABASE_URL")
    if not raw:
        pytest.skip("未配置独立 PostgreSQL 测试连接")
    with temporary_database(raw, revision="0015") as url:
        with _connect(url) as conn:
            ids = _seed(conn)
            if navigation_used:
                conn.execute(
                    "INSERT INTO relation_tags VALUES (%s,%s)", (ids["relation"], ids["navigation"])
                )
        preserved = (
            "works",
            "parts",
            "part_sources",
            "sections",
            "paragraphs",
            "entities",
            "relations",
            "relation_nodes",
            "relation_entities",
            "relation_tags",
            "analysis_targets",
        )
        with _connect(url) as conn:
            before = {table: _snapshot(conn, table) for table in preserved}
            indexes = {
                table: _snapshot(conn, table, "kind='fulltext'")
                for table in ("semantic_indexes", "semantic_index_heads", "semantic_index_items")
            }
            receipts = _snapshot(
                conn, "semantic_build_receipts", "index_id='" + str(ids["fulltext"]) + "'"
            )
            retained_tags = {ids["tag"], ids["navigation"]} if navigation_used else {ids["tag"]}
            tag = [
                r for r in _snapshot(conn, "tags") if r[0]["id"] in {str(t) for t in retained_tags}
            ]
            kept_receipts = [
                r
                for r in _snapshot(conn, "asset_write_requests")
                if r[0]["response"]["operation"] in {"entity_create", "relation_create"}
                or r[0]["response"]["result"].get("id") in {str(t) for t in retained_tags}
            ]
            kept_catalog = [
                r
                for r in _snapshot(conn, "catalog_requests")
                if r[0]["operation"] in {"work_create", "part_import", "prepare_import"}
            ]
        monkeypatch.setenv("NOVEL_LENS_DATABASE_URL", url)
        config = Config(str(ROOT / "alembic.ini"))
        command.upgrade(config, "0016")
        with _connect(url) as conn:
            assert {table: _snapshot(conn, table) for table in preserved} == before
            assert {table: _snapshot(conn, table) for table in indexes} == indexes
            assert _snapshot(conn, "semantic_build_receipts") == receipts
            assert _snapshot(conn, "tags") == tag
            for table in (
                "annotations",
                "annotation_ranges",
                "annotation_tags",
                "annotation_entities",
                "semantic_annotation_snapshots",
                "reference_clues",
                "reference_searches",
                "analysis_coverage",
            ):
                assert _snapshot(conn, table) == []
            assert conn.execute(
                "SELECT to_regclass('style_guides'),to_regclass('style_guide_entries'),"
                "to_regclass('style_guide_ranges')"
            ).fetchone() == (None, None, None)
            row = conn.execute(
                "SELECT id,version,status,completion,recovery FROM analysis_jobs"
            ).fetchone()
            assert row is not None
            assert row[:4] == (ids["job"], 8, "running", None)
            assert row[4] == {
                "next_action": "旧分析已清理，请按新标注契约从任务目标开始重新阅读分析。"
            }
            assert _snapshot(conn, "asset_write_requests") == kept_receipts
            assert _snapshot(conn, "catalog_requests") == kept_catalog
            assert {
                r[0] for r in conn.execute("SELECT tag_id FROM preparation_tags")
            } == retained_tags
        with pytest.raises(RuntimeError, match="备份"):
            command.downgrade(config, "0015")
        with _connect(url) as conn:
            assert conn.execute("SELECT version_num FROM alembic_version").fetchone() == ("0016",)
            assert _snapshot(conn, "paragraphs") == before["paragraphs"]
