"""AutoReplyPauseManager 按账号+会话复合键隔离（移植上游 #109）。"""
import time

from XianyuAutoAsync import AutoReplyPauseManager


def test_pause_keys_are_isolated_per_account():
    mgr = AutoReplyPauseManager()
    # 不同账号出现相同 chat_id 字符串：互不污染
    mgr.pause_chat("chat-1", "account-a")
    assert mgr.is_chat_paused("chat-1", "account-a") is True
    assert mgr.is_chat_paused("chat-1", "account-b") is False
    assert mgr.get_remaining_pause_time("chat-1", "account-b") == 0
    assert mgr.get_remaining_pause_time("chat-1", "account-a") > 0


def test_pause_expiry_removes_entry_and_longer_pause_wins():
    mgr = AutoReplyPauseManager()

    # 2 分钟后过期
    mgr.paused_chats[("a", "chat-1")] = time.time() + 120
    # 已过期的暂停记录读取后即被清理
    mgr.paused_chats[("a", "chat-2")] = time.time() - 1
    assert mgr.is_chat_paused("chat-2", "a") is False
    assert ("a", "chat-2") not in mgr.paused_chats

    # 已有更长暂停时保持原暂停（重复触发不缩短；time.time 精度内允许 ε 延长）
    mgr.pause_chat("chat-1", "a")
    longer = mgr.paused_chats[("a", "chat-1")]
    mgr.pause_chat("chat-1", "a")
    assert mgr.paused_chats[("a", "chat-1")] >= longer

    mgr.cleanup_expired_pauses()
    assert ("a", "chat-1") in mgr.paused_chats
