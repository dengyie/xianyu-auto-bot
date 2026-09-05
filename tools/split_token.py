#!/usr/bin/env python3
"""Extract notification/message-pipeline/send clusters from XianyuLive (P2-x step 4b)."""
import ast
import builtins
import sys
import symtable
from pathlib import Path

ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
SRC = ROOT / "XianyuAutoAsync.py"
OUT = ROOT / "xianyu_token_mixins.py"
NEW = ROOT / "XianyuAutoAsync.new.py"

TOKEN = [
    'preflight_token_after_manual_refresh', 'refresh_token', '_refresh_token_impl',
    '_is_normal_token_expiry', '_is_token_related_error', 'token_refresh_loop',
    '_get_mtop_token',
]
NOTIFY = [
    "send_notification", "_send_qq_notification", "_send_dingtalk_notification",
    "_send_feishu_notification", "_send_bark_notification", "_send_email_notification",
    "_send_webhook_notification", "_send_wechat_notification", "_send_telegram_notification",
    "send_token_refresh_notification", "_build_scheduled_token_refresh_error_message",
    "send_delivery_failure_notification",
]
PIPELINE = [
    "_mark_non_heartbeat_message", "_record_message_stream_watchdog_trigger",
    "_maybe_notify_message_stream_stale", "message_stream_watchdog_loop",
    "message_debounce_delay", "_get_message_priority", "_enqueue_message",
    "_message_worker", "_start_message_queue_workers", "_stop_message_queue_workers",
    "_unwrap_message_for_dedupe", "_extract_message_id", "_extract_message_id_from_chat_payload",
    "_cleanup_message_reply_state", "_reserve_message_reply", "_finalize_message_reply",
    "_release_message_reply", "_schedule_debounced_reply", "_process_chat_message_reply",
    "_handle_message_with_semaphore", "is_chat_message", "handle_message",
]
SEND = [
    "create_chat", "send_msg", "send_msg_once", "send_image_msg", "send_image_from_file",
    "send_heartbeat", "send_delivery_steps_once", "_send_delivery_steps",
    "_build_delivery_send_groups", "_finalize_delivery_after_send",
    "_send_recovered_delivery_without_sid", "get_default_reply", "get_keyword_reply",
    "get_ai_reply", "get_api_reply", "debug_message_structure",
    "_extract_image_url_from_message", "_extract_message_card_payload",
    "_extract_message_button_text", "_extract_message_card_title", "_classify_message_route",
    "_handle_simple_message_auto_delivery", "_extract_buyer_id_from_message_meta",
]

HEADER = '''"""XianyuLive 的通知分发 / 消息管线 / 发送与回复内容 Mixin（P2-x 步骤④b）。

方法经 self/cls 操作宿主实例状态；XianyuAutoAsync 模块级剩余符号经 `_host`
代理调用时解析（兼容测试替换）；db_manager 逐方法保留原 seam
（方法体内惰性导入 = 包属性，否则 = 宿主绑定）。
"""
import asyncio
import base64
import json
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger


class _HostProxy:
    """属性访问转发到 XianyuAutoAsync 模块级符号（调用时解析）。"""

    def __getattr__(self, name):
        import XianyuAutoAsync

        return getattr(XianyuAutoAsync, name)


_host = _HostProxy()

# 自宿主模块迁入（被搬方法的默认参数在类创建期求值，不能用 _host）
DELIVERY_BATCH_MAX_UNITS = 10
DELIVERY_BATCH_MAX_CHARS = 1200


def _db_package():
    """惰性包属性：等价于原方法体内的 from db_manager import db_manager。"""
    from db_manager import db_manager

    return db_manager


def _db_host():
    """宿主绑定：等价于原模块级 from-import 名字（import 期绑定）。"""
    import XianyuAutoAsync

    return XianyuAutoAsync.db_manager
'''


def span(node):
    start = node.lineno
    if getattr(node, "decorator_list", None):
        start = min(d.lineno for d in node.decorator_list)
    return start, node.end_lineno


def local_bound(fragment):
    bound = set()
    for x in ast.walk(fragment):
        if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Store):
            bound.add(x.id)
        elif isinstance(x, ast.arg):
            bound.add(x.arg)
        elif isinstance(x, ast.ExceptHandler) and x.name:
            bound.add(x.name)
        elif isinstance(x, ast.comprehension):
            for t in ast.walk(x.target):
                if isinstance(t, ast.Name):
                    bound.add(t.id)
        elif isinstance(x, ast.withitem) and x.optional_vars:
            for t in ast.walk(x.optional_vars):
                if isinstance(t, ast.Name):
                    bound.add(t.id)
        elif isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(x.name)
        elif isinstance(x, ast.Import):
            for a in x.names:
                bound.add((a.asname or a.name).split(".")[0])
        elif isinstance(x, ast.ImportFrom):
            for a in x.names:
                bound.add(a.asname or a.name)
    return bound


def names_with_pos(fragment):
    return [(n.lineno, n.col_offset, n.end_lineno, n.end_col_offset, n.id)
            for n in ast.walk(fragment) if isinstance(n, ast.Name)]


def rewrite(text, base_line, repls):
    lines = [l.encode("utf-8") for l in text.split("\n")]
    for (ln, col, eln, ecol), new in sorted(repls.items(), reverse=True):
        i, j = ln - base_line, eln - base_line
        nb = new.encode("utf-8")
        if i == j:
            lines[i] = lines[i][:col] + nb + lines[i][ecol:]
        else:
            lines[i] = lines[i][:col] + nb
    return "\n".join(l.decode("utf-8") for l in lines)


def main():
    src = SRC.read_text(encoding="utf-8")
    src_lines = src.split("\n")
    tree = ast.parse(src)

    st = symtable.symtable(src, str(SRC), "exec")
    rs_globals = {s.get_name() for s in st.get_symbols() if s.is_global()}
    bi = set(dir(builtins))

    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "XianyuLive")
    methods = {m.name: m for m in cls.body if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))}
    missing = [n for n in TOKEN if n not in methods]
    assert not missing, f"missing: {missing}"

    hb = {"asyncio", "base64", "json", "os", "re", "time", "Any", "Dict", "List", "Optional",
          "Tuple", "logger", "_host", "_HostProxy", "_db_package", "_db_host", "TokenMixin",
          "NotificationMixin", "MessagePipelineMixin", "SendMixin", "TokenMixin", "XianyuLive",
          "DELIVERY_BATCH_MAX_UNITS", "DELIVERY_BATCH_MAX_CHARS"}

    moved = [methods[n] for n in TOKEN]
    used = {ident for m in moved for ident in (x[4] for x in names_with_pos(m))}
    unknown = used - rs_globals - bi - hb - set(TOKEN) - local_bound(ast.Module(body=moved, type_ignores=[]))
    assert not unknown, f"unresolvable: {sorted(unknown)}"

    def build(names):
        parts = []
        for n in names:
            m = methods[n]
            a, b = span(m)
            text = "\n".join(src_lines[a - 1:b])
            lazy = "from db_manager import db_manager" in text
            repls = {}
            for (ln, col, eln, ecol, ident) in names_with_pos(m):
                if ident == "db_manager" and ident in rs_globals:
                    repls[(ln, col, eln, ecol)] = "_db_package()" if lazy else "_db_host()"
                elif ident in rs_globals and ident not in bi and ident not in hb and ident not in set(TOKEN):
                    repls[(ln, col, eln, ecol)] = f"_host.{ident}"
            text = rewrite(text, a, repls)
            text = text.replace("XianyuLive.", "self.")
            parts.append(text)
        return parts

    NL = chr(10)
    out = [HEADER.rstrip(NL), "", "",
           "class TokenMixin:",
'    """mtop token 刷新循环/预检/错误分类。"""', ""]
    out.extend(t for t in build(TOKEN))
    OUT.write_text("\n".join(out), encoding="utf-8")

    const_dead = []
    for n in tree.body:
        if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in ("DELIVERY_BATCH_MAX_UNITS", "DELIVERY_BATCH_MAX_CHARS") for t in n.targets):
            const_dead.append((n.lineno, n.end_lineno))
    dead = [span(methods[n]) for n in TOKEN]
    keep = [True] * (len(src_lines) + 1)
    for a, b in dead:
        for i in range(a, b + 1):
            keep[i] = False
    kept = [ln for i, ln in enumerate(src_lines, 1) if keep[i]]
    cleaned, blanks = [], 0
    for ln in kept:
        blanks = blanks + 1 if ln.strip() == "" else 0
        if blanks <= 2:
            cleaned.append(ln)
    text = "\n".join(cleaned)

    anchor = "from xianyu_trading_mixins import ItemMixin, OrderMixin\n"
    assert anchor in text
    text = text.replace(anchor,
                        "from xianyu_token_mixins import TokenMixin\n" + anchor, 1)
    old_cls = "class XianyuLive(MessagePipelineMixin, SendMixin, NotificationMixin, OrderMixin, ItemMixin, XianyuAuthRecoveryMixin):"
    assert old_cls in text
    text = text.replace(old_cls, "class XianyuLive(TokenMixin, MessagePipelineMixin, SendMixin, NotificationMixin, OrderMixin, ItemMixin, XianyuAuthRecoveryMixin):", 1)
    NEW.write_text(text, encoding="utf-8")
    print(f"mixins: {OUT}")
    print(f"XianyuAutoAsync: {len(src_lines)} -> {len(text.splitlines())} lines")


if __name__ == "__main__":
    main()
