#!/usr/bin/env python3
"""Extract the auth-recovery state machine cluster out of XianyuLive (P2-x, step 3).

Creates xianyu_auth_recovery.py:
  - ConnectionState (moved), MANUAL_VERIFICATION_CONTEXTS (moved)
  - class XianyuAuthRecoveryMixin with the cluster methods (state containers STAY
    on XianyuLive; the mixin reaches them via cls/self at runtime)

Design notes (from recon 2026-09-05):
  - cluster state access is 100% cls._x -> mixin-compatible, no state relocation
  - XianyuLive.<classmethod> self-references inside the cluster -> self.<name>
  - tests monkeypatch XianyuAutoAsync.db_manager -> db_manager is a proxy object
    forwarding to XianyuAutoAsync.db_manager at call time (lazy import, no cycle)
"""
import ast
import builtins
import sys
import symtable
from pathlib import Path

ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
SRC = ROOT / "XianyuAutoAsync.py"
OUT = ROOT / "xianyu_auth_recovery.py"
NEW = ROOT / "XianyuAutoAsync.new.py"

CLUSTER = [
    # 327-1125 state machine (53)
    "_cleanup_auth_prewarmed_tokens", "cache_auth_prewarmed_token", "pop_auth_prewarmed_token",
    "clear_auth_prewarmed_token", "_cleanup_manual_refresh_state", "get_manual_refresh_state",
    "mark_manual_refresh_handoff", "consume_manual_refresh_slider_failed_bypass",
    "_cleanup_auth_recovery_locks", "acquire_auth_recovery_lock", "get_auth_recovery_lock_state",
    "release_auth_recovery_lock", "get_init_auth_failure_state", "record_init_auth_failure",
    "clear_init_auth_failure_state", "_cleanup_qr_login_grace_state", "mark_qr_login_grace",
    "get_qr_login_grace_ttl_seconds", "get_qr_login_grace", "update_qr_login_grace",
    "clear_qr_login_grace", "_get_qr_login_grace_until", "_get_qr_login_grace_remaining_seconds",
    "_is_in_qr_login_grace_period", "_set_qr_login_grace_until", "_clear_qr_login_grace_period",
    "_enter_qr_login_grace_period", "_consume_qr_login_grace_period_if_expired",
    "_should_defer_auth_recovery_for_qr_grace", "_cleanup_password_login_failure_backoff",
    "get_password_login_failure_backoff", "clear_password_login_failure_backoff",
    "set_password_login_failure_backoff", "_is_counted_password_login_failure_reason",
    "_get_night_mode_settings", "_is_in_night_mode_window", "_get_effective_keepalive_interval",
    "_get_effective_cookie_refresh_interval", "_compute_token_retry_wait_seconds",
    "_protect_account_for_consecutive_failures", "_get_active_password_login_failure_backoff",
    "_should_skip_token_refresh_for_login_backoff", "classify_password_login_failure",
    "_is_account_risk_login_error", "_is_account_pause_status", "_should_pause_for_manual_verification",
    "_apply_account_pause_state", "_clear_account_pause_state", "_request_stop_after_account_pause",
    "_protect_account_from_risk_login_retry", "_pause_account_for_manual_verification",
    "send_account_paused_notification", "_safe_str", "_mask_secret_value", "_summarize_cookie_string",
    "_new_risk_session_id",
    # risk-event logging family (1162-1250) + manual refresh + password refresh
    "_normalize_risk_trigger_scene", "_build_risk_event_meta", "_create_risk_log",
    "begin_manual_refresh", "end_manual_refresh", "_try_password_login_refresh",
]

HEADER = '''"""XianyuLive 认证恢复状态机（自 XianyuAutoAsync.py 拆出，P2-x 步骤③）。

内容：扫码登录宽限、手动刷新交接、认证恢复锁、init 失败窗口、密码登录退避、
夜间模式、风控暂停与风险事件日志。这些方法全部通过 cls/self 操作宿主
（XianyuLive）类上的状态容器 —— 状态留在宿主类，本模块只承载行为。

依赖约定：
- db_manager 是转发代理：调用时解析 XianyuAutoAsync.db_manager，
  兼容测试对宿主模块属性的替换（XianyuAutoAsync 在模块末尾 bind）。
- XianyuAutoAsync 反向 import 本模块的 ConnectionState / MANUAL_VERIFICATION_CONTEXTS / Mixin，
  本模块严禁在模块级 import XianyuAutoAsync（运行期惰性导入除外）。
"""
import asyncio
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Dict, Optional, Tuple

from loguru import logger

from config import RISK_CONTROL
from utils.notification_dispatcher import (
    build_face_verify_notification,
    dispatch_account_notifications,
    format_notification_template,
    get_notification_template_text,
    guess_verification_type,
    render_notification_template,
)


class _HostDBManagerProxy:
    """调用时转发到 XianyuAutoAsync.db_manager（测试会替换宿主模块属性）。"""

    def __getattr__(self, name):
        import XianyuAutoAsync

        return getattr(XianyuAutoAsync.db_manager, name)


db_manager = _HostDBManagerProxy()


def bind_host_module(module) -> None:
    """预留：宿主模块未来需要向状态机注入依赖时使用。"""
    global _HOST
    _HOST = module


_HOST = None


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


def extract(fragment, src_lines, ctx_names):
    start, end = span(fragment)
    text = "\n".join(src_lines[start - 1:end])
    repls = {}
    for (ln, col, eln, ecol, ident) in names_with_pos(fragment):
        if ident in ctx_names:
            repls[(ln, col, eln, ecol)] = f"ctx.{ident}"
    return rewrite(text, start, repls)


def main():
    src = SRC.read_text(encoding="utf-8")
    src_lines = src.split("\n")
    tree = ast.parse(src)

    st = symtable.symtable(src, str(SRC), "exec")
    rs_globals = {s.get_name() for s in st.get_symbols() if s.is_global()}
    bi = set(dir(builtins))

    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "XianyuLive")
    methods = {m.name: m for m in cls.body if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))}
    missing = [n for n in CLUSTER if n not in methods]
    assert not missing, f"missing methods: {missing}"

    # module-level ConnectionState + MANUAL_VERIFICATION_CONTEXTS
    conn = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "ConnectionState")
    mvc = next(n for n in tree.body if isinstance(n, (ast.Assign, ast.AnnAssign))
               and any(isinstance(t, ast.Name) and t.id == "MANUAL_VERIFICATION_CONTEXTS"
                       for t in (n.targets if isinstance(n, ast.Assign) else [n.target])))

    moved = [methods[n] for n in CLUSTER]
    used = {ident for m in moved for ident in (x[4] for x in names_with_pos(m))}
    locals_ = set(CLUSTER) | {"cls", "self"}
    locals_ |= local_bound(ast.Module(body=moved, type_ignores=[]))
    header_names = {"asyncio", "time", "defaultdict", "datetime", "timedelta", "timezone",
                    "Enum", "Any", "Dict", "Optional", "Tuple", "logger", "RISK_CONTROL",
                    "build_face_verify_notification", "dispatch_account_notifications",
                    "format_notification_template", "get_notification_template_text",
                    "guess_verification_type", "render_notification_template",
                    "db_manager", "ConnectionState", "MANUAL_VERIFICATION_CONTEXTS",
                    "XianyuAuthRecoveryMixin", "_HostDBManagerProxy", "bind_host_module",
                    "_HOST", "XianyuLive"}
    unknown = used - rs_globals - bi - header_names - locals_
    assert not unknown, f"unresolvable names: {sorted(unknown)}"

    # 类外裸引用检查：若模块级代码裸用被搬符号，需在宿主反向 re-import
    moved_set = set(CLUSTER) | {"ConnectionState", "MANUAL_VERIFICATION_CONTEXTS"}
    outside_bare = set()
    for n in tree.body:
        if n is cls or getattr(n, "name", None) in ("ConnectionState",):
            continue
        for x in ast.walk(n):
            if isinstance(x, ast.Name) and x.id in moved_set and isinstance(x.ctx, ast.Load):
                outside_bare.add(x.id)
    print("类外裸引用:", sorted(outside_bare) or "无")

    # ---- emit mixin module ----
    out = [HEADER]
    # ConnectionState body (verbatim, its own refs: none expected beyond stdlib)
    out.append("\n".join(src_lines[span(conn)[0] - 1:span(conn)[1]]))
    out.append("")
    out.append("")
    ms, me = span(mvc)
    out.append("\n".join(src_lines[ms - 1:me]))
    out.append("")
    out.append("")
    out.append("")
    out.append("class XianyuAuthRecoveryMixin:")
    out.append('    """认证恢复 / 风控暂停 / 夜间模式行为集（状态容器在宿主 XianyuLive 上）。"""')
    out.append("")
    for m in moved:
        text = "\n".join(src_lines[span(m)[0] - 1:span(m)[1]])
        # XianyuLive.<x> -> self.<x>  (classmethod reachable via instance)
        text = text.replace("XianyuLive.", "self.")
        out.append(text)
        out.append("")
        out.append("")
    mixin_text = "\n".join(out)
    OUT.write_text(mixin_text, encoding="utf-8")

    # ---- XianyuAutoAsync.new.py ----
    dead = [span(m) for m in moved] + [span(conn), span(mvc)]
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

    # imports
    anchor = 'from loguru import logger' + chr(10)
    assert anchor in text
    text = text.replace(anchor, anchor +
                        "from xianyu_auth_recovery import (\n"
                        "    ConnectionState,\n"
                        "    MANUAL_VERIFICATION_CONTEXTS,\n"
                        "    XianyuAuthRecoveryMixin,\n)\n", 1)

    # class header
    assert "class XianyuLive:" in text
    text = text.replace("class XianyuLive:", "class XianyuLive(XianyuAuthRecoveryMixin):", 1)

    # bind host at module end (proxy pattern support / future injections)
    text = text.rstrip("\n") + "\n\n\n# P2-x: 认证恢复状态机已拆至 xianyu_auth_recovery.py（Mixin 已挂到 XianyuLive）\n"
    (NEW).write_text(text, encoding="utf-8")
    print(f"mixin: {OUT} ({len(mixin_text.splitlines())} lines)")
    print(f"XianyuAutoAsync: {len(src_lines)} -> {len(text.splitlines())} lines")


if __name__ == "__main__":
    main()
