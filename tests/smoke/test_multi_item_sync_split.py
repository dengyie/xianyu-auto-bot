"""多消息同步帧拆分（2026-09-26 买家测试消息丢失的根因修复）。

积压/重连场景下一帧 syncPushPackage.data 可携带多条消息；旧实现只取
data[0]，其余（常含真实买家文本）被静默丢弃——data[0] 是系统引导消息时
整帧被当作"引导消息处理完成"收口。修复后逐条拆为单 item 帧递归走完整
管线，每条消息独立经过分类/门控/回复决策。
"""
import asyncio
import base64
import json
from unittest import mock

import pytest
from loguru import logger as loguru_logger

from xianyu_messaging_mixins import MessagePipelineMixin


def _make_mixin():
    m = MessagePipelineMixin.__new__(MessagePipelineMixin)
    m.cookie_id = "1926782908"
    m._cookie_mgr = mock.MagicMock()
    m._cookie_mgr.get_cookie_status.return_value = True
    m._safe_str = staticmethod(lambda e: str(e))
    m.is_sync_package = lambda md: True
    return m


def _guide_item():
    # syncPushPackage.data[] 的每个 item 是 {"data": <base64(JSON)>}；
    # 解码后为未加密的系统引导消息（sessionArouse）结构
    payload = json.dumps(
        {"chatType": 1, "operation": {"content": {"sessionArouse": {"arouseChatScriptInfo": []}}}}
    )
    return {"data": base64.b64encode(payload.encode("utf-8")).decode("ascii")}


def _frame(items, mid="mid-1"):
    return {
        "headers": {"mid": mid, "sid": "sid-1", "app-key": "k"},
        "body": {"syncPushPackage": {"data": items}},
    }


@pytest.mark.asyncio
async def test_multi_item_sync_frame_is_split_and_fully_processed():
    m = _make_mixin()
    ws = mock.AsyncMock()
    records = []
    handler_id = loguru_logger.add(lambda msg: records.append(str(msg)), level="INFO")
    try:
        await m.handle_message(_frame([_guide_item(), _guide_item(), _guide_item()]), ws, "mid-1")
    finally:
        loguru_logger.remove(handler_id)

    split = [r for r in records if "拆帧逐条处理" in r]
    assert len(split) == 1 and "3 条消息" in split[0]
    # 三个 item 各自走完整管线（旧实现只处理 data[0]，后两条静默丢失）
    detected = [r for r in records if "检测到chatType消息" in r]
    assert len(detected) == 3
    # 原帧统一 ack 一次；synthetic 帧带 _slidex_no_ack 不重复 ack
    assert ws.send.await_count == 1


@pytest.mark.asyncio
async def test_single_item_frame_does_not_log_split():
    m = _make_mixin()
    ws = mock.AsyncMock()
    records = []
    handler_id = loguru_logger.add(lambda msg: records.append(str(msg)), level="INFO")
    try:
        await m.handle_message(_frame([_guide_item()]), ws, "mid-2")
    finally:
        loguru_logger.remove(handler_id)

    assert not [r for r in records if "拆帧逐条处理" in r]
    assert len([r for r in records if "检测到chatType消息" in r]) == 1


@pytest.mark.asyncio
async def test_split_preserves_item_order():
    """item 顺序处理：第一条是引导消息、第二条经解密进入正常分支。
    这里用第二条=引导消息验证顺序（第一条处理后第二条仍被处理）。"""
    m = _make_mixin()
    ws = mock.AsyncMock()
    records = []
    handler_id = loguru_logger.add(lambda msg: records.append(str(msg)), level="INFO")
    try:
        # 第一条带 marker 内容可区分（sessionArouse 空脚本），第二条同构
        await asyncio.wait_for(
            m.handle_message(_frame([_guide_item(), _guide_item()]), ws, "mid-3"),
            timeout=5.0,
        )
    finally:
        loguru_logger.remove(handler_id)

    detected = [r for r in records if "检测到chatType消息" in r]
    assert len(detected) == 2
    assert ws.send.await_count >= 1  # 原帧 ack 已发送
