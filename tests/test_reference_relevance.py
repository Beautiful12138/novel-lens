"""检索优化的机械契约；真实文学相关性另用固定查询集回读判断。"""

import json
from pathlib import Path

from sqlalchemy import text
from test_preparation import drain, import_book
from test_semantic import DeterministicModel

from novel_lens.database import Database
from novel_lens.preparation import PreparationService
from novel_lens.reference import ReferenceService, fusion_score
from novel_lens.reference_contracts import ReferenceQuery, ReferenceResult, ReferenceScope
from novel_lens.reference_keywords import keyword_terms


def test_natural_query_terms_preserve_intent_and_explicit_terms() -> None:
    query = "人物生气的场景"
    assert keyword_terms(query, []) == [query, "生气"]
    negative = "人物心里愤怒，但克制着没有发作；朋友聊天"
    terms = keyword_terms(negative, ["没有发作", "怒意", "怒意"])
    assert terms[0] == negative and terms.count("怒意") == 1
    assert "愤怒" in terms and "聊天" in terms
    assert "发作" not in terms and "没有发作" in terms
    many = "咖啡 学校 飞机 汽车 医院 温暖 寒冷 晴天 雨天 冬天 夏天 日落 黎明 等待"
    assert len(keyword_terms(many, [])) <= 13


def test_literal_matches_are_auxiliary_to_complete_query_semantics() -> None:
    single = {"source_semantic": 1}
    literal = {"source_keyword": 1, "clue_keyword": 1}
    assert fusion_score(single) > fusion_score(literal)
    weak = dict.fromkeys(
        ["source_semantic", "clue_semantic", "source_keyword", "clue_keyword"], 100
    )
    assert fusion_score(single) > fusion_score(weak)
    assert fusion_score({"source_keyword": 1}) > 0


def test_natural_query_locates_late_verbatim_preview_and_freezes_it(
    database: Database, tmp_path: Path
) -> None:
    model = DeterministicModel()
    preparation = PreparationService(database, 1024 * 1024, model)
    later = "窗外的树影缓缓移动。" * 25 + "他生气地把杯子放下。" + "屋里安静下来。" * 30
    book = import_book(preparation, tmp_path, "他刚走进屋子。\n" + later)
    drain(preparation, book, model)
    service = ReferenceService(database, model)
    before = len(model.calls)
    result = service.query(
        ReferenceQuery(
            query="人物生气的场景", scope=[ReferenceScope(work_id=book.work.id)], format="full"
        )
    )
    assert isinstance(result, ReferenceResult)
    assert len(model.calls) == before + 1
    assert result.diagnostics["keyword_terms"] == ["人物生气的场景", "生气"]
    hit = result.items[0]
    assert "source_keyword" in hit.channels
    assert "生气" in hit.excerpt and hit.excerpt in later
    assert len(hit.excerpt) == 200 and hit.excerpt_truncated
    model.fail = True  # 重读快照不访问模型，投影不能丢失定位后的原文。
    compact = service.query(ReferenceQuery(search_id=result.search_id))
    assert compact.items[0].excerpt == hit.excerpt
    assert compact.items[0].source_range == hit.source_range
    model.fail = False
    fallback = service.query(
        ReferenceQuery(query="完全无关", scope=[ReferenceScope(work_id=book.work.id)])
    )
    assert fallback.items[0].excerpt == "他刚走进屋子。"


def test_keyword_ties_use_source_semantics_before_reading_order(
    database: Database, tmp_path: Path
) -> None:
    model = DeterministicModel()
    preparation = PreparationService(database, 1024 * 1024, model)
    book = import_book(preparation, tmp_path, "窗边雨声。\n标题：第二章\n楼下雨声。")
    drain(preparation, book, model)
    # 独立测试库内控制距离；不把常量向量替身当文学相关性的证据。
    with database.engine.begin() as conn:
        conn.execute(
            text("""
            UPDATE semantic_index_items i SET embedding=CAST(:vector AS vector)
            FROM sections s WHERE i.section_id=s.id AND i.work_id=:work AND s.ordinal=1
            """),
            {"work": book.work.id, "vector": json.dumps([0.0, 1.0] + [0.0] * 1022)},
        )
    result = ReferenceService(database, model).query(
        ReferenceQuery(query="雨声", scope=[ReferenceScope(work_id=book.work.id)], format="full")
    )
    assert isinstance(result, ReferenceResult)
    assert result.items[0].section_title == "第二章"
    assert result.items[0].channels == ["source_keyword", "source_semantic"]
    assert result.items[0].excerpt == "楼下雨声。" and not result.items[0].excerpt_truncated
