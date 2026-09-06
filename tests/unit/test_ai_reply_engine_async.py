"""ai_reply_engine 原生异步路径回归测试（P3 收尾）。

关键语义：
- generate_reply_async 是原生协程：防抖 asyncio.sleep、会话串行 asyncio.Lock、
  DB/provider 走 to_thread —— 不再整段 to_thread(self.generate_reply)。
- 空回复拦截、新鲜度跳过与同步路径一致。
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
    # 统一启用 AI，避免每个用例都造设置
    monkeypatch.setattr(eng, "is_ai_enabled", lambda cookie_id: True)
    return eng


def _run(coro):
    return asyncio.run(coro)


def test_async_lock_is_asyncio_lock_per_chat(engine):
    lock = engine._get_achat_lock("chat-a")
    assert isinstance(lock, asyncio.Lock)
    assert engine._get_achat_lock("chat-a") is lock
    assert engine._get_achat_lock("chat-b") is not lock


def test_generate_reply_async_saves_and_returns_stripped_reply(engine, monkeypatch):
    calls = []
    monkeypatch.setattr(engine_mod.AIReplyEngine, "_invoke_provider",
                        lambda self, settings, messages: "  你好，在的～  ")
    saved = engine_mod.AIReplyEngine.save_conversation

    def spy_save(self, chat_id, cookie_id, user_id, item_id, role, content, intent=None):
        calls.append(role)
        return saved(self, chat_id, cookie_id, user_id, item_id, role, content, intent=intent)

    monkeypatch.setattr(engine_mod.AIReplyEngine, "save_conversation", spy_save)

    reply = _run(engine.generate_reply_async(
        "在吗", {"title": "手机", "price": "100", "desc": "九成新"},
        "chat-1", "ck-1", "u-1", "item-1", skip_wait=True))
    assert reply == "你好，在的～"
    assert calls == ["user", "assistant"]


def test_generate_reply_async_intercepts_empty_reply(engine, monkeypatch):
    monkeypatch.setattr(engine_mod.AIReplyEngine, "_invoke_provider",
                        lambda self, settings, messages: "   ")
    reply = _run(engine.generate_reply_async(
        "在吗", {"title": "手机", "price": "100", "desc": ""},
        "chat-2", "ck-1", "u-1", "item-1", skip_wait=True))
    assert reply is None


def test_generate_reply_async_skips_stale_message(engine):
    # 先落一条更新的用户消息，再生成旧消息 -> 新鲜度检查应跳过
    engine.save_conversation("chat-3", "ck-1", "u-1", "item-1", "user", "更新的消息", intent=None)
    reply = _run(engine.generate_reply_async(
        "旧消息", {"title": "手机", "price": "100", "desc": ""},
        "chat-3", "ck-1", "u-1", "item-1", skip_wait=True))
    assert reply is None


def test_generate_reply_sync_path_still_works(engine, monkeypatch):
    """同步路径（管理端测试路由用）语义不变：仍走 threading.Lock + 同样 helper。"""
    monkeypatch.setattr(engine_mod.AIReplyEngine, "_invoke_provider",
                        lambda self, settings, messages: "同步回复")
    reply = engine.generate_reply(
        "在吗", {"title": "手机", "price": "100", "desc": ""},
        "chat-4", "ck-1", "u-1", "item-1", skip_wait=True)
    assert reply == "同步回复"
