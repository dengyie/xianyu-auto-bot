#!/usr/bin/env python3
"""Extract CookieMixin + DeliveryMixin from XianyuLive (P2-x step 4d)."""
import ast
import builtins
import sys
import symtable
from pathlib import Path

ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
SRC = ROOT / "XianyuAutoAsync.py"

COOKIE = [
    "_extract_cookie_value", "_reload_latest_cookies_from_db", "_serialize_cookies",
    "_sync_session_cookie_header", "_set_runtime_cookie_state", "_persist_runtime_cookie_state",
    "_extract_set_cookie_updates", "_apply_response_cookie_updates",
    "_build_cookie_string_with_updates", "_build_x5_cookie_snapshot",
    "_log_x5_cookie_snapshot", "protected_merge_cookie_dicts", "_merge_cookie_dicts",
    "_log_cookie_merge_summary", "_update_cookies_and_restart", "update_config_cookies",
    "_verify_cookie_validity", "cookie_refresh_loop", "_execute_cookie_refresh",
    "enable_cookie_refresh", "refresh_cookies_from_qr_login",
    "_refresh_cookies_via_browser_page", "reset_qr_cookie_refresh_flag",
    "get_qr_cookie_refresh_remaining_time", "_refresh_cookies_via_browser",
]
DELIVERY = [
    "_resolve_delivery_log_buyer_nick", "can_auto_delivery", "mark_delivery_sent",
    "_activate_delivery_lock", "_record_delivery_log", "_format_delivery_log_reason",
    "_get_pending_delivery_finalization_meta",
    "_mark_delivery_platform_confirm_no_longer_required",
    "_mark_delivery_pending_platform_confirm", "_persist_delivery_finalization_state",
    "_summarize_delivery_progress", "_has_bargain_success_evidence",
    "_apply_bargain_amount_override", "_has_delivery_progress_evidence",
    "_is_auto_delivery_trigger", "_handle_auto_delivery", "_update_card_image_url",
    "_resolve_delivery_notification_buyer_name", "_auto_delivery",
    "_process_delivery_content_with_description", "_build_delivery_steps",
    "_can_batch_text_delivery", "_format_delivery_unit_text",
    "_apply_delivery_unit_numbering", "_get_api_card_content",
    "_get_yifan_api_card_content", "_call_yifan_api_with_account",
]

HEADER_TMPL = '''"""{title}（自 XianyuAutoAsync.py 拆出，P2-x 步骤④d）。

方法经 self/cls 操作宿主实例状态；XianyuAutoAsync 模块级剩余符号经 `_host`
代理调用时解析；db_manager 逐方法保留原 seam（惰性导入=包属性，否则=宿主绑定）。
"""
import asyncio
import json
import re
import time
from typing import Any, Dict, Optional, Tuple

from loguru import logger


class _HostProxy:
    """属性访问转发到 XianyuAutoAsync 模块级符号（调用时解析）。"""

    def __getattr__(self, name):
        import XianyuAutoAsync

        return getattr(XianyuAutoAsync, name)


_host = _HostProxy()


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


def extract_group(src, group_names, out_path, title, mixin_name, hb_extra, moved_names_so_far):
    src_lines = src.split("\n")
    tree = ast.parse(src)
    st = symtable.symtable(src, str(SRC), "exec")
    rs_globals = {s.get_name() for s in st.get_symbols() if s.is_global()}
    bi = set(dir(builtins))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "XianyuLive")
    methods = {m.name: m for m in cls.body if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))}
    missing = [n for n in group_names if n not in methods]
    assert not missing, f"missing: {missing}"

    hb = {"asyncio", "json", "re", "time", "Any", "Dict", "Optional", "Tuple", "logger",
          "_host", "_HostProxy", "_db_package", "_db_host", mixin_name, "XianyuLive",
          "OrderMixin", "ItemMixin", "XianyuAuthRecoveryMixin", "TokenMixin",
          "MessagePipelineMixin", "SendMixin", "NotificationMixin", "CookieMixin",
          "DeliveryMixin"} | set(group_names) | hb_extra | moved_names_so_far

    moved = [methods[n] for n in group_names]
    used = {ident for m in moved for ident in (x[4] for x in names_with_pos(m))}
    unknown = used - rs_globals - bi - hb - local_bound(ast.Module(body=moved, type_ignores=[]))
    assert not unknown, f"unresolvable: {sorted(unknown)}"

    parts = []
    for n in group_names:
        m = methods[n]
        a, b = span(m)
        text = "\n".join(src_lines[a - 1:b])
        lazy = "from db_manager import db_manager" in text
        repls = {}
        for (ln, col, eln, ecol, ident) in names_with_pos(m):
            if ident == "db_manager" and ident in rs_globals:
                repls[(ln, col, eln, ecol)] = "_db_package()" if lazy else "_db_host()"
            elif ident in rs_globals and ident not in bi and ident not in hb:
                repls[(ln, col, eln, ecol)] = f"_host.{ident}"
        text = rewrite(text, a, repls)
        text = text.replace("XianyuLive.", "self.")
        parts.append(text)

    out = [HEADER_TMPL.format(title=title).rstrip("\n"), "", "",
           f"class {mixin_name}:", f'    """{title}方法簇。"""', ""]
    out.extend(parts)
    out.append("")
    out_path.write_text("\n".join(out), encoding="utf-8")

    dead = [span(methods[n]) for n in group_names]
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
    new_src = "\n".join(cleaned)
    return new_src, len(src_lines), len(new_src.split("\n"))


def main():
    src = SRC.read_text(encoding="utf-8")
    print(f"start: {len(src.splitlines())} lines")

    # ---- CookieMixin ----
    src, a, b = extract_group(src, COOKIE, ROOT / "xianyu_cookie_mixin.py",
                              "Cookie 运行时状态/合并/刷新/验证", "CookieMixin", set(), set())
    print(f"cookie: {a} -> {b}")
    SRC.write_text(src, encoding="utf-8")

    # ---- DeliveryMixin (from updated source) ----
    src, a, b = extract_group(src, DELIVERY, ROOT / "xianyu_delivery_mixin.py",
                              "自动发货/交付内容/卡密 API", "DeliveryMixin", set(), set(COOKIE))
    print(f"delivery: {a} -> {b}")
    SRC.write_text(src, encoding="utf-8")

    # ---- wiring: imports + MRO ----
    anchor = "from xianyu_token_mixins import TokenMixin\n"
    src = SRC.read_text(encoding="utf-8")
    assert anchor in src
    src = src.replace(anchor,
                      anchor +
                      "from xianyu_cookie_mixin import CookieMixin\n" +
                      "from xianyu_delivery_mixin import DeliveryMixin\n", 1)
    old_cls = "class XianyuLive(MessagePipelineMixin, SendMixin, NotificationMixin, OrderMixin, ItemMixin, XianyuAuthRecoveryMixin):"
    assert old_cls in src
    src = src.replace(old_cls,
                      "class XianyuLive(DeliveryMixin, CookieMixin, MessagePipelineMixin, SendMixin, NotificationMixin, OrderMixin, ItemMixin, XianyuAuthRecoveryMixin):", 1)
    SRC.write_text(src, encoding="utf-8")
    print(f"final: {len(src.splitlines())} lines")


if __name__ == "__main__":
    main()
