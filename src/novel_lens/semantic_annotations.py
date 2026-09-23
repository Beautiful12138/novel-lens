"""标注原文快照、实时证据核验及范围规划；不触发资产写入或文学判断。"""

from typing import Any

from sqlalchemy import Connection, text

from novel_lens.semantic_contracts import AnnotationSemanticCoverage

# 同一 SQL 快照内读取全部引用与段落摘要；按原文坐标排序，不受输入数组顺序、
# note、标签或一般版本号影响。摘要留在数据库，不向应用传输超长原文。
CURRENT_EVIDENCE = """
WITH normalized_ranges AS (
    SELECT r.annotation_id, s.ordinal AS section_order, p0.ordinal AS lo, p1.ordinal AS hi,
        jsonb_build_object('section_id',r.section_id,
            'start_paragraph_id',r.start_paragraph_id,'end_paragraph_id',r.end_paragraph_id,
            'section_ordinal',s.ordinal,'start_ordinal',p0.ordinal,'end_ordinal',p1.ordinal,
            'text_sha256',(SELECT encode(sha256(convert_to(string_agg(
                encode(sha256(convert_to(p.text,'UTF8')),'hex'),'' ORDER BY p.ordinal),
                'UTF8')),'hex') FROM paragraphs p
                WHERE p.section_id=r.section_id AND p.ordinal BETWEEN p0.ordinal AND p1.ordinal)
        ) AS ref
    FROM annotation_ranges r JOIN annotations a ON a.id=r.annotation_id
    JOIN sections s ON s.id=r.section_id AND s.work_id=a.work_id
    JOIN paragraphs p0 ON p0.id=r.start_paragraph_id AND p0.section_id=s.id
    JOIN paragraphs p1 ON p1.id=r.end_paragraph_id AND p1.section_id=s.id
    WHERE a.work_id=:work
), range_sets AS (
    SELECT annotation_id,jsonb_agg(ref ORDER BY section_order,lo,hi) AS ranges
    FROM normalized_ranges GROUP BY annotation_id
), current_evidence AS (
    SELECT a.id AS annotation_id,a.version AS annotation_version,r.ranges,
        encode(sha256(convert_to(w.source_sha256 || r.ranges::text,'UTF8')),'hex') AS evidence_id
    FROM annotations a JOIN works w ON w.id=a.work_id
    JOIN range_sets r ON r.annotation_id=a.id WHERE a.work_id=:work
)
"""

# 查询与阻塞分页均在过滤过期身份后再排序／限量，避免陈旧高分挤占有效候选。
VALID_ITEMS_JOIN = """
JOIN current_evidence e ON e.annotation_id=i.annotation_id AND e.evidence_id=i.evidence_id
"""


def snapshot(conn: Connection, index: dict[str, Any]) -> None:
    """在创建代的事务中一次捕获当前全部标注；重放创建请求不刷新该快照。"""
    conn.execute(
        text(
            CURRENT_EVIDENCE
            + """
        INSERT INTO semantic_annotation_snapshots
            (index_id,annotation_id,work_id,evidence_id,ranges)
        SELECT :id,annotation_id,:work,evidence_id,ranges FROM current_evidence
        """
        ),
        {"work": index["work_id"], "id": index["id"]},
    )


def coverage(conn: Connection, index: dict[str, Any]) -> AnnotationSemanticCoverage:
    """按当前标注互斥分类；范围重叠按段落存在性核验，不累加切片长度。"""
    row = (
        conn.execute(
            text(
                CURRENT_EVIDENCE
                + """, classified AS (
        SELECT CASE WHEN snap.annotation_id IS NULL THEN 'not_indexed'
            WHEN snap.evidence_id<>e.evidence_id THEN 'stale'
            WHEN NOT EXISTS (
                SELECT 1 FROM jsonb_array_elements(e.ranges) WITH ORDINALITY r(ref,ordinal)
                JOIN paragraphs p ON p.section_id=(r.ref->>'section_id')::uuid
                    AND p.ordinal BETWEEN (r.ref->>'start_ordinal')::int
                                      AND (r.ref->>'end_ordinal')::int
                WHERE NOT EXISTS (SELECT 1 FROM semantic_index_items i
                    WHERE i.index_id=:id AND i.annotation_id=e.annotation_id
                    AND i.evidence_id=e.evidence_id AND i.range_ordinal=r.ordinal
                    AND i.embedding IS NOT NULL
                    AND p.ordinal BETWEEN i.start_ordinal AND i.end_ordinal)
            ) THEN 'covered'
            WHEN EXISTS (SELECT 1 FROM semantic_index_items i WHERE i.index_id=:id
                AND i.annotation_id=e.annotation_id AND i.evidence_id=e.evidence_id
                AND i.blocked_reason IS NOT NULL) THEN 'blocked'
            ELSE 'pending' END AS category
        FROM current_evidence e LEFT JOIN semantic_annotation_snapshots snap
            ON snap.index_id=:id AND snap.annotation_id=e.annotation_id
        ) SELECT count(*) AS total,
            count(*) FILTER (WHERE category='covered') AS covered,
            count(*) FILTER (WHERE category='stale') AS stale,
            count(*) FILTER (WHERE category='not_indexed') AS not_indexed,
            count(*) FILTER (WHERE category='blocked') AS blocked,
            count(*) FILTER (WHERE category='pending') AS pending FROM classified
        """
            ),
            {"work": index["work_id"], "id": index["id"]},
        )
        .mappings()
        .one()
    )
    return AnnotationSemanticCoverage(**row, complete=row["covered"] == row["total"])


def position(
    conn: Connection, index: dict[str, Any], cursor: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """找下一有效快照范围；已过期来源跳过但仍在动态覆盖中报告，不改写目标快照。"""
    after = dict(cursor)
    inclusive = True
    while True:
        with conn.begin():
            row = (
                conn.execute(
                    text(
                        CURRENT_EVIDENCE
                        + f"""
                SELECT snap.* FROM semantic_annotation_snapshots snap
                JOIN current_evidence e ON e.annotation_id=snap.annotation_id
                    AND e.evidence_id=snap.evidence_id
                WHERE snap.index_id=:id AND snap.annotation_id {">=" if inclusive else ">"} :after
                ORDER BY snap.annotation_id LIMIT 1
                """
                    ),
                    {"work": index["work_id"], "id": index["id"], "after": after["annotation"]},
                )
                .mappings()
                .first()
            )
        if row is None:
            return None
        if str(row["annotation_id"]) != after["annotation"]:
            after = {
                "annotation": str(row["annotation_id"]),
                "range": 0,
                "section": 0,
                "paragraph": 0,
                "overlap_start": None,
            }
        if after["range"] < len(row["ranges"]):
            ref = dict(row["ranges"][after["range"]])
            ref.update(
                annotation_id=row["annotation_id"],
                evidence_id=row["evidence_id"],
                range_ordinal=after["range"] + 1,
            )
            return ref, after
        inclusive = False


def validate_batch(conn: Connection, index: dict[str, Any], identities: dict[Any, str]) -> bool:
    """先锁标注主行，再读取证据；资产修改也先锁同一行，因此发布期间引用不会变化。"""
    if not identities:
        return True
    conn.execute(
        text("""
        SELECT id FROM annotations WHERE work_id=:work AND id=ANY(:ids)
        ORDER BY id FOR UPDATE
    """),
        {"work": index["work_id"], "ids": list(identities)},
    )
    rows = conn.execute(
        text(
            CURRENT_EVIDENCE
            + """
        SELECT annotation_id,evidence_id FROM current_evidence WHERE annotation_id=ANY(:ids)
    """
        ),
        {"work": index["work_id"], "ids": list(identities)},
    ).all()
    return {row[0]: row[1] for row in rows} == identities
