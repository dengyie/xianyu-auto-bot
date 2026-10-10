"""回复风格 v2 单元测试：legacy 逐字节冻结 / veteran prompt / 后置校验 / 设置往返。

对应 docs/AI回复风格重构-开发文档.md §3/§5/§6/§9：
- legacy 链路逐字节不变（回滚保证）
- veteran 黑词重试一次 + 超长句界截断（截断过短触发纠正重试）
- reply_style/item_brief_mode/item_brief_ttl 三字段 DB 往返
"""
import asyncio

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
    eng = AIReplyEngine()
    monkeypatch.setattr(eng, "is_ai_enabled", lambda cookie_id: True)
    return eng


# ===== legacy 冻结（§9.2 回滚保证） =====

FROZEN_LEGACY_PROMPTS = {
    "price": """【议价场景】
策略：根据议价次数递减优惠
- 第1次：可小幅优惠，表达诚意
- 第2次：中等优惠，强调已是优惠价
- 第3次及以后：最大优惠或坚持底线
语气友好但坚定，突出商品价值和优势。""",
    "tech": """【技术/产品问题】
基于商品信息回答，不要自行发挥。
如果问题超出商品信息范围，回复："等等，这个我需要看一看\"""",
    "default": """【一般咨询】
基于商品信息回答物流、售后等问题。
如果问题超出商品信息范围，回复："等等，这个我需要看一看"
如果客户明确询问退款，回复："虚拟产品，一旦发出是不可以退款的\"""",
}


def test_legacy_default_prompts_byte_frozen(engine):
    assert engine.legacy_default_prompts == FROZEN_LEGACY_PROMPTS


def test_legacy_unified_prompt_exact(engine):
    settings = {"custom_prompts": "", "max_bargain_rounds": 3,
                "max_discount_percent": 10, "max_discount_amount": 100}
    prompt = engine._build_unified_system_prompt({}, settings)
    g = FROZEN_LEGACY_PROMPTS
    expected = f"""你是一位专业的电商客服AI助手。请根据用户消息和上下文，直接生成合适的回复。

## 核心原则
1. **准确理解意图**：只根据用户实际说的内容判断，不要过度解读
2. **不要主动提及敏感话题**：用户没提到的（如退款、砍价）不要主动提
3. **基于商品信息回答**：只回答商品信息中有的内容
4. **避免重复**：结合对话历史，不要重复之前说过的话
5. **语言简洁友好**：回复要自然、简短，尽量别超过20个字

## 场景处理指南

### 当用户明确要求降价/优惠/砍价时
{g['price']}
- 议价限制：最多3轮，最大优惠10%或100元

### 当用户询问产品技术/功能/使用问题时
{g['tech']}

### 其他一般咨询（物流、售后、商品介绍等）
{g['default']}

## 特别注意
- 用户只是问价格≠用户在砍价，正常回答价格即可
- 用户咨询售后≠用户要退款，正常解答即可
- 如果用户的问题超过你的回答范围，比如发图片，可以说"等等，这个问题我需要看看"，不要自己回答

请直接输出回复内容，不要输出分析过程。"""
    assert prompt == expected


def test_legacy_item_desc_format(engine):
    desc = AIReplyEngine._legacy_item_desc({"title": "手机", "price": "100", "desc": "九成新"})
    assert desc == "商品标题: 手机\n商品价格: 100.0元\n商品描述: 九成新"


# ===== veteran prompt =====

def test_veteran_prompt_boundary_values(engine):
    settings = {"custom_prompts": "", "max_bargain_rounds": 2,
                "max_discount_percent": 5, "max_discount_amount": 30}
    prompt = engine._build_veteran_system_prompt({}, settings)
    assert "最多 2 轮议价" in prompt
    assert "最多让 5% 或 30 元" in prompt
    assert "老卖家" in prompt
    assert "10–35 字" in prompt
    assert "商品档案" in prompt


def test_veteran_prompt_uses_custom_overrides(engine):
    settings = {"custom_prompts": "", "max_bargain_rounds": 3,
                "max_discount_percent": 10, "max_discount_amount": 100}
    prompt = engine._build_veteran_system_prompt({"price": "自定义议价"}, settings)
    assert "自定义议价" in prompt


def test_resolve_reply_style_falls_back_to_legacy(engine):
    assert engine._resolve_reply_style({"reply_style": "veteran"}) == "veteran"
    assert engine._resolve_reply_style({"reply_style": "legacy"}) == "legacy"
    assert engine._resolve_reply_style({}) == "legacy"
    assert engine._resolve_reply_style({"reply_style": "VETERAN"}) == "legacy"  # 仅小写精确匹配
    assert engine._resolve_reply_style({"reply_style": "weird"}) == "legacy"


def test_resolve_brief_mode_validated(engine):
    assert engine._resolve_brief_mode({"item_brief_mode": "off"}) == "off"
    assert engine._resolve_brief_mode({"item_brief_mode": "rule_only"}) == "rule_only"
    assert engine._resolve_brief_mode({}) == "cache_llm"
    assert engine._resolve_brief_mode({"item_brief_mode": "junk"}) == "cache_llm"


# ===== 后置校验（§6） =====

def test_blackword_retry_no_pollution(engine, monkeypatch):
    calls = []

    def fake_invoke(self, settings, messages, *args, **kwargs):
        calls.append([dict(m) for m in messages])
        return "东西在的，你看下"

    monkeypatch.setattr(engine_mod.AIReplyEngine, "_invoke_provider", fake_invoke)
    messages = [{"role": "system", "content": "S"}, {"role": "user", "content": "U"}]
    reply = engine._apply_style_guard({}, messages, "您好~亲，这个很划算哦~")
    assert reply == "东西在的，你看下"
    assert len(calls) == 1
    assert calls[0][0]["content"] == "S" + engine_mod._STYLE_CORRECTION
    assert messages[0]["content"] == "S"   # 浅拷贝，原列表不被污染


def test_blackword_retry_fails_passes_through(engine, monkeypatch):
    monkeypatch.setattr(engine_mod.AIReplyEngine, "_invoke_provider",
                        lambda self, settings, messages, *a, **k: None)
    messages = [{"role": "system", "content": "S"}, {"role": "user", "content": "U"}]
    reply = engine._apply_style_guard({}, messages, "您好，在的")
    assert reply == "您好，在的"   # 重试仍失败 → 放行原回复


def test_truncate_at_sentence_boundary():
    t = engine_mod.AIReplyEngine._truncate_at_sentence
    # 句末标点在界内 → 截到标点
    assert t("好的。" + "a" * 70, 60) == "好的。"
    # 无标点 → 硬截
    assert t("a" * 70, 60) == "a" * 60
    # 标点位置过短（截断后 < 5 字）→ 返回短句，由调用方触发纠正重试（§6）
    assert t("ab。" + "a" * 70, 60) == "ab。"
    # 未超长 → 原样返回
    assert t("九成新，配件齐全", 60) == "九成新，配件齐全"


def test_guard_short_truncation_triggers_retry(engine, monkeypatch):
    """截断后不足 5 字：走纠正重试拿合规回复，而不是放行超长原文。"""
    calls = []

    def fake_invoke(self, settings, messages, *args, **kwargs):
        calls.append([dict(m) for m in messages])
        if messages[0]["content"].endswith(engine_mod._STYLE_CORRECTION):
            return "守底价，考虑好找我"
        return "好的。" + "啊" * 70   # 唯一句界在开头，截断后 3 字 < 5 → 触发纠正重试

    monkeypatch.setattr(engine_mod.AIReplyEngine, "_invoke_provider", fake_invoke)
    messages = [{"role": "system", "content": "S"}, {"role": "user", "content": "U"}]
    reply = engine._apply_style_guard({}, messages, "好的。" + "啊" * 70)
    assert reply == "守底价，考虑好找我"
    assert len(calls) == 1                        # 只发生一次调用：截断过短触发的纠正重试
    assert calls[0][0]["content"].endswith(engine_mod._STYLE_CORRECTION)


def test_legacy_generation_skips_guard(engine, monkeypatch):
    """legacy 档不进后置校验：即使回复含客服腔也逐字节放行。"""
    monkeypatch.setattr(engine_mod.AIReplyEngine, "_apply_style_guard",
                        lambda *a, **k: pytest.fail("legacy 不应触发风格校验"))
    monkeypatch.setattr(engine_mod.AIReplyEngine, "_invoke_provider",
                        lambda self, settings, messages: "您好，亲~收到哦~")
    reply = asyncio.run(engine.generate_reply_async(
        "在吗", {"title": "手机", "price": "100", "desc": "九成新"},
        "chat-legacy", "ck-1", "u-1", "item-1", skip_wait=True))
    assert reply == "您好，亲~收到哦~"


def test_veteran_generation_applies_guard_and_brief(engine, monkeypatch):
    dbm.db_manager.save_ai_reply_settings("ck-v", {
        "ai_enabled": True, "reply_style": "veteran",
        "item_brief_mode": "off", "api_key": "k"})
    calls = []

    def fake_invoke(self, settings, messages, *args, **kwargs):
        if messages[0]["content"].endswith(engine_mod._STYLE_CORRECTION):
            return "成色九成新，配件都齐"
        calls.append([dict(m) for m in messages])
        return "您好，亲~在的呢~东西好着呢"   # 含黑词 → 触发重试

    monkeypatch.setattr(engine_mod.AIReplyEngine, "_invoke_provider", fake_invoke)
    reply = asyncio.run(engine.generate_reply_async(
        "在吗", {"title": "手机", "price": "100", "desc": "九成新"},
        "chat-vet", "ck-v", "u-1", "item-1", skip_wait=True))
    assert reply == "成色九成新，配件都齐"
    assert len(calls) == 1
    user_prompt = calls[0][1]["content"]
    assert "## 商品档案" in user_prompt
    assert "标价：100元" in user_prompt
    assert "本单已议价0次" in user_prompt


# ===== 设置三字段 DB 往返 =====

def test_settings_roundtrip_three_fields():
    db = DBManager(db_path=":memory:")
    db.save_ai_reply_settings("ck-r", {
        "ai_enabled": True, "reply_style": "veteran",
        "item_brief_mode": "rule_only", "item_brief_ttl": 600})
    settings = db.get_ai_reply_settings("ck-r")
    assert settings["reply_style"] == "veteran"
    assert settings["item_brief_mode"] == "rule_only"
    assert settings["item_brief_ttl"] == 600


def test_settings_defaults_when_missing_fields():
    db = DBManager(db_path=":memory:")
    db.save_ai_reply_settings("ck-d", {"ai_enabled": True})
    settings = db.get_ai_reply_settings("ck-d")
    assert settings["reply_style"] == "legacy"
    assert settings["item_brief_mode"] == "cache_llm"
    assert settings["item_brief_ttl"] == 2592000


def test_settings_no_row_returns_defaults():
    db = DBManager(db_path=":memory:")
    settings = db.get_ai_reply_settings("ck-none")
    assert settings["reply_style"] == "legacy"
    assert settings["item_brief_mode"] == "cache_llm"
    assert settings["item_brief_ttl"] == 2592000
