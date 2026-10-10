"""商品档案（Item Brief）单元测试：双形态归一化 / 规则抽取 / 缓存指纹 / 模式分流。

对应 docs/AI回复风格重构-开发文档.md §4：
- 形态A：DB 行 item_detail=JSON（detail_params/item_label_data 结构化映射）
- 形态B：纯文本 / 旧三段 dict（title/price/desc）正则抽取
- 形态C：无资料退化档案
- 缓存：md5(text+price) 指纹 + TTL 兜底，test_ 前缀不读写缓存
"""
import pytest

import ai_reply_engine as engine_mod
from ai_reply_engine import AIReplyEngine
from db_manager import DBManager
import db_manager as dbm


@pytest.fixture
def engine(monkeypatch):
    db = DBManager(db_path=":memory:")
    monkeypatch.setattr(dbm, "db_manager", db)
    monkeypatch.setattr(engine_mod, "db_manager", db)
    return AIReplyEngine()


# ===== 归一化：三种形态 =====

def test_normalize_form_a_db_row_with_json_detail(engine):
    detail = {
        "detail": "九成新自用，原装盒充电器都在，包邮",
        "detail_params": {"内存": "256G", "颜色": "白色"},
        "item_label_data": {"1": "自用一手", "2": "无拆修"},
    }
    info = {"item_title": "iPhone 13", "item_price": "3699",
            "item_detail": detail, "item_detail_parsed": detail}
    raw = engine._normalize_item_raw(info)
    assert raw["title"] == "iPhone 13"
    assert raw["price_text"] == "3699"
    assert "九成新" in raw["text"]
    assert raw["parsed"]["detail_params"]["内存"] == "256G"


def test_normalize_form_b_json_string_detail_extract_body(engine):
    """item_detail 为 JSON 字符串且未带 parsed 时补解析，取 {"detail": ...} 正文。"""
    info = {"item_title": "Kindle", "item_price": "200",
            "item_detail": '{"detail": "用了两年，屏幕没坏"}'}
    raw = engine._normalize_item_raw(info)
    assert raw["text"] == "用了两年，屏幕没坏"
    assert raw["parsed"]["detail"] == "用了两年，屏幕没坏"


def test_normalize_form_b_legacy_segments(engine):
    raw = engine._normalize_item_raw({"title": "手机", "price": "100", "desc": "九成新"})
    assert raw == {"title": "手机", "price_text": "100", "text": "九成新", "parsed": {}}


def test_normalize_form_c_none_fallback(engine):
    raw = engine._normalize_item_raw(None)
    assert raw["title"] == "未知商品"
    assert raw["price_text"] == ""
    assert raw["text"] == ""


# ===== 规则抽取 =====

def test_rules_extract_form_b_text(engine):
    text = "九成新，用了两年，屏幕有轻微划痕，原装盒还在，包邮，不退不换，自用出"
    raw = engine._normalize_item_raw({"title": "小米手机", "price": "800", "desc": text})
    brief = engine._brief_from_rules(raw)
    assert "成新" in brief["condition"]
    assert "划痕" in brief["flaws"]
    assert "原装盒" in brief["accessories"]
    assert brief["delivery"] == "包邮"
    assert "不退不换" in brief["after_sales"]
    assert "用了两年" in brief["usage"]
    assert brief["source"] == "自用"
    assert brief["quality"] in ("partial", "full")


def test_rules_extract_form_a_specs_mapping(engine):
    parsed = {"detail_params": {"内存": "256G", "颜色": "白色", "成色": 9}}
    raw = engine._normalize_item_raw({"item_title": "x", "item_detail": parsed,
                                      "item_detail_parsed": parsed, "item_price": "1"})
    brief = engine._brief_from_rules(raw)
    assert brief["specs"] == {"内存": "256G", "颜色": "白色", "成色": "9"}


def test_brief_quality_grades():
    base = {"condition": "九成新"}
    assert engine_mod.AIReplyEngine._brief_quality(base) == "low"
    partial = {**base, "flaws": ["划痕"], "usage": "两年"}
    assert engine_mod.AIReplyEngine._brief_quality(partial) == "partial"
    full = {**partial, "accessories": ["原装盒"], "delivery": "包邮", "specs": {"内存": "256G"}}
    assert engine_mod.AIReplyEngine._brief_quality(full) == "full"


# ===== LLM 消化与合并 =====

def test_digest_brief_llm_schema_normalized(engine, monkeypatch):
    def fake_invoke(self, settings, messages, *args, **kwargs):
        return '前置说明 {"condition": "99新", "flaws": ["划痕"], "specs": {"屏幕": "OLED"}, "junk": [1]}'
    monkeypatch.setattr(engine_mod.AIReplyEngine, "_invoke_provider", fake_invoke)
    raw = engine._normalize_item_raw({"title": "x", "price": "1", "desc": "描述文本"})
    digest = engine._digest_brief_with_llm(raw, {})
    assert digest["condition"] == "99新"
    assert digest["flaws"] == ["划痕"]
    assert digest["specs"] == {"屏幕": "OLED"}
    assert "junk" not in digest


def test_digest_brief_invalid_output_returns_none(engine, monkeypatch):
    monkeypatch.setattr(engine_mod.AIReplyEngine, "_invoke_provider",
                        lambda self, settings, messages, *a, **k: "不是JSON")
    raw = engine._normalize_item_raw({"title": "x", "price": "1", "desc": "描述文本"})
    assert engine._digest_brief_with_llm(raw, {}) is None


def test_merge_rule_priority_over_digest(engine):
    brief = {"condition": "九成新", "flaws": [], "specs": {}, "usage": ""}
    digest = {"condition": "全新", "flaws": ["划痕"], "usage": "三年",
              "specs": {"屏幕": "OLED"}, "highlights": ["成色好"]}
    merged = engine._merge_brief(brief, digest)
    assert merged["condition"] == "九成新"   # 规则提取优先，不被 LLM 覆盖
    assert merged["flaws"] == ["划痕"]       # 规则为空时用 digest 补
    assert merged["specs"] == {"屏幕": "OLED"}
    assert merged["highlights"] == ["成色好"]


# ===== 缓存：指纹 + TTL + test_ 前缀 =====

PARTIAL_TEXT = "九成新，用了两年，屏幕有划痕，配件齐全，自用没毛病"


def _settings(mode="cache_llm", ttl=2592000):
    return {"reply_style": "veteran", "item_brief_mode": mode,
            "item_brief_ttl": ttl, "custom_prompts": "",
            "max_bargain_rounds": 3, "max_discount_percent": 10,
            "max_discount_amount": 100}


def test_cache_hit_skips_rebuild(engine, monkeypatch):
    digest_calls = []
    monkeypatch.setattr(engine, "_digest_brief_with_llm",
                        lambda raw, s: digest_calls.append(1) or None)
    info = {"item_title": "手机", "item_price": "100", "item_detail": PARTIAL_TEXT}
    engine.resolve_item_brief(info, "item-100", _settings())
    engine.resolve_item_brief(info, "item-100", _settings())
    assert digest_calls == [1]   # 第二次命中缓存，不再消化


def test_cache_rebuild_on_price_change(engine, monkeypatch):
    digest_calls = []
    monkeypatch.setattr(engine, "_digest_brief_with_llm",
                        lambda raw, s: digest_calls.append(1) or None)
    info = {"item_title": "手机", "item_price": "100", "item_detail": PARTIAL_TEXT}
    engine.resolve_item_brief(info, "item-100", _settings())
    engine.resolve_item_brief({"item_title": "手机", "item_price": "80",
                               "item_detail": PARTIAL_TEXT}, "item-100", _settings())
    assert digest_calls == [1, 1]   # 价格变化 → 指纹失效 → 重建


def test_cache_ttl_expiry_rebuilds(engine, monkeypatch):
    digest_calls = []
    monkeypatch.setattr(engine, "_digest_brief_with_llm",
                        lambda raw, s: digest_calls.append(1) or None)
    info = {"item_title": "手机", "item_price": "100", "item_detail": PARTIAL_TEXT}
    engine.resolve_item_brief(info, "item-100", _settings())
    with dbm.db_manager.lock:
        dbm.db_manager.conn.execute("UPDATE ai_item_cache SET last_updated = '2020-01-01 00:00:00'")
        dbm.db_manager.conn.commit()
    engine.resolve_item_brief(info, "item-100", _settings())
    assert digest_calls == [1, 1]   # 超 TTL → 重建


def test_test_prefix_never_caches(engine, monkeypatch):
    digest_calls = []
    monkeypatch.setattr(engine, "_digest_brief_with_llm",
                        lambda raw, s: digest_calls.append(1) or None)
    info = {"item_title": "手机", "item_price": "100", "item_detail": PARTIAL_TEXT}
    engine.resolve_item_brief(info, "test_item", _settings())
    engine.resolve_item_brief(info, "test_item", _settings())
    assert digest_calls == [1, 1]
    with dbm.db_manager.lock:
        row = dbm.db_manager.conn.execute(
            "SELECT COUNT(*) FROM ai_item_cache WHERE item_id = 'test_item'").fetchone()
    assert row[0] == 0


def test_mode_off_skips_rules_and_cache(engine, monkeypatch):
    monkeypatch.setattr(engine, "_digest_brief_with_llm",
                        lambda raw, s: pytest.fail("mode=off 不应触发LLM消化"))
    monkeypatch.setattr(engine, "_brief_from_rules",
                        lambda raw: pytest.fail("mode=off 不应跑规则抽取"))
    info = {"item_title": "手机", "item_price": "100", "item_detail": PARTIAL_TEXT}
    brief = engine.resolve_item_brief(info, "item-200", _settings(mode="off"))
    assert brief["quality"] == "low"
    assert brief["title"] == "手机"
    with dbm.db_manager.lock:
        row = dbm.db_manager.conn.execute(
            "SELECT COUNT(*) FROM ai_item_cache WHERE item_id = 'item-200'").fetchone()
    assert row[0] == 0


# ===== 渲染 =====

def test_render_omits_empty_fields_and_warns_on_low():
    fallback = AIReplyEngine._brief_from_fallback(
        {"title": "手机", "price_text": "100", "text": "九成新", "parsed": {}})
    rendered = AIReplyEngine._render_item_brief(fallback)
    assert "标题：手机" in rendered
    assert "标价：100元" in rendered
    assert "品牌/型号" not in rendered       # 空字段行省略
    assert "⚠" in rendered                   # low 档警示防编造


def test_render_full_brief_lists_sections():
    brief = {"title": "iPhone", "price_display": "3699", "brand": "Apple", "model": "13",
             "condition": "九成新", "flaws": ["划痕"], "accessories": ["原装盒"],
             "specs": {"内存": "256G"}, "source": "自用", "usage": "两年",
             "delivery": "包邮", "after_sales": "不退不换", "highlights": ["无拆修"],
             "quality": "full"}
    rendered = AIReplyEngine._render_item_brief(brief)
    for line in ("品牌/型号：Apple 13", "成色：九成新", "瑕疵：划痕", "配件：原装盒",
                 "内存:256G", "来源/使用：自用 两年", "发货：包邮",
                 "售后：不退不换", "卖点（仅可引用，不得夸大）：无拆修"):
        assert line in rendered
    assert "⚠" not in rendered