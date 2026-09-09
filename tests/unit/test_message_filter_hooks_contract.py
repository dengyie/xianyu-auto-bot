"""接收期消息过滤钩子契约（review 修复批：#110 部分移植的 P1 回归锚）。

上游 #110 的动作执行钩子位于 handle_message 接收期（execute_actions=True，
覆盖 user/system 源全部回复路径），与管线内决策期钩子（False）和 AI 发送前
钩子（True）三分工。本文件锚定该结构不回退——纯函数级 handle_message mock
面过宽（解密/订单提取/路由），故采用源码契约测试（本仓库 test_batch_rate
既有先例）。
"""
import re
from pathlib import Path

MIXIN = Path(__file__).resolve().parents[2] / "xianyu_messaging_mixins.py"


def _handle_message_body() -> str:
    text = MIXIN.read_text(encoding="utf-8")
    m = re.search(r"    async def handle_message\(.*?(?=\n    (?:async )?def |\nclass |\Z)", text, re.S)
    assert m, "handle_message 方法未找到"
    return m.group(0)


def test_handle_message_has_action_executing_receive_hook():
    body = _handle_message_body()
    # 接收期钩子：execute_actions=True，位于 allow_auto_reply 早退之前
    assert re.search(
        r"message_filter_result = await self\._apply_message_filters\(.*?execute_actions=True,",
        body,
        re.S,
    ), "handle_message 缺少接收期动作钩子（execute_actions=True）——#110 动作在非 AI 路径将失效"


def test_receive_hook_covers_user_and_system_sources():
    body = _handle_message_body()
    assert "message_source='system' if is_system_message else 'user'" in body, (
        "接收期钩子必须区分 system/user 源（上游原始语义）"
    )


def test_receive_hook_runs_before_allow_auto_reply_gate():
    """顺序与上游 eb95a5a 一致：钩子 → allow_auto_reply 门 → skip 短路。

    钩子必须在门之前（否则非 user_chat 路由的动作不会执行），
    skip 短路在门之后（只影响本会进入自动回复链的消息）。
    """
    body = _handle_message_body()
    hook_pos = body.find("message_filter_result = await self._apply_message_filters(")
    gate_pos = body.find("if not allow_auto_reply:")
    skip_pos = body.find("if message_filter_result.get('skip_auto_reply'):")
    assert hook_pos != -1 and skip_pos != -1 and gate_pos != -1
    assert hook_pos < gate_pos < skip_pos, (
        "接收期钩子必须在 allow_auto_reply 门之前（#110 动作对非 user_chat 路由也执行），"
        "skip 短路随后"
    )


def test_pipeline_keeps_decision_and_ai_hooks():
    text = MIXIN.read_text(encoding="utf-8")
    m = re.search(r"    async def _process_chat_message_reply\(.*?(?=\n    (?:async )?def |\nclass |\Z)", text, re.S)
    assert m, "_process_chat_message_reply 未找到"
    body = m.group(0)
    # 决策期只读 + AI 发送前拦截两钩子仍在管线内
    assert "execute_actions=False" in body, "管线决策期钩子（execute_actions=False）丢失"
    assert re.search(
        r"message_source='ai',\s*\n\s*execute_actions=True,",
        body,
    ), "AI 发送前钩子（execute_actions=True）丢失"
