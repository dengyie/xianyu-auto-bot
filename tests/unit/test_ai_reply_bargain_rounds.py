"""议价轮次计数单元测试（§5.2/D3）：只收明确压价词，问价词不计数。"""
import pytest

from ai_reply_engine import AIReplyEngine


def test_counts_explicit_bargain_words():
    context = [
        {"role": "user", "content": "300行不行"},
        {"role": "assistant", "content": "可以，成色你自己看"},
        {"role": "user", "content": "再便宜点呗"},
    ]
    assert AIReplyEngine._get_bargain_rounds(context) == 2


def test_price_inquiry_not_counted():
    context = [
        {"role": "user", "content": "这个多少钱"},
        {"role": "assistant", "content": "标价100"},
        {"role": "user", "content": "请问价格还能谈吗"},
    ]
    # "多少钱"/"价格" 是问价不是砍价；"还能谈吗" 无压价词
    assert AIReplyEngine._get_bargain_rounds(context) == 0


def test_assistant_bargain_words_not_counted():
    context = [{"role": "assistant", "content": "可以便宜点给你"}]
    assert AIReplyEngine._get_bargain_rounds(context) == 0


def test_empty_context_returns_zero():
    assert AIReplyEngine._get_bargain_rounds([]) == 0
    assert AIReplyEngine._get_bargain_rounds(None) == 0


def test_each_bargain_keyword_variant():
    for word in ("行不行", "少点", "便宜", "砍价", "再低", "优惠点", "刀"):
        context = [{"role": "user", "content": f"这个{word}如何"}]
        assert AIReplyEngine._get_bargain_rounds(context) == 1, word
