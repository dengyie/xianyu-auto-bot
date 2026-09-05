#!/usr/bin/env python3
"""Extract order + item method clusters from XianyuLive into xianyu_trading_mixins.py.

Same shape as the auth-recovery split: state stays on the host class, remaining
module-level symbols of XianyuAutoAsync are reached via a lazy _host proxy at
call time (monkeypatch-safe, no import cycle).
"""
import ast
import builtins
import sys
import symtable
from pathlib import Path

ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
SRC = ROOT / "XianyuAutoAsync.py"
OUT = ROOT / "xianyu_trading_mixins.py"
NEW = ROOT / "XianyuAutoAsync.new.py"

ORDER = [
    "_init_order_status_handler", "_lookup_delivery_order_by_sid",
    "_select_buyer_identity_for_order_write", "_extract_order_message_context",
    "_preload_basic_order_info", "_retry_order_detail_after_delay",
    "_schedule_order_detail_retry", "_extract_order_id_for_comment",
    "_get_normalized_local_order_status", "_get_order_expected_delivery_quantity",
    "_resolve_external_order_status", "_normalize_order_amount_text",
    "_parse_order_amount_float", "_mark_order_bargain_flow",
    "_resolve_delivery_progress_order_status", "_sync_order_delivery_progress",
    "_get_order_status_priority", "_reserve_order_detail_force_refresh",
    "_should_accept_order_detail_status_correction",
    "_should_reject_order_detail_status_update",
    "_maybe_force_refresh_order_detail_for_signal", "_extract_order_id_from_update_key",
    "_extract_order_id_from_candidate_text", "_collect_order_id_candidate_texts",
    "_extract_order_id", "fetch_order_detail_info", "_auto_deliver_recovered_pending_order",
]
ITEM = [
    "_ensure_item_owned_by_current_account", "save_item_info_to_db", "save_item_detail_only",
    "fetch_item_detail_from_api", "_add_to_item_cache", "_cleanup_item_cache",
    "_fetch_item_detail_from_browser", "save_items_list_to_db", "_fetch_item_details",
    "get_item_info", "_is_item_owned_by_self", "extract_item_id_from_message",
    "get_item_specific_reply", "get_item_list_info", "get_all_items",
    "_get_item_polish_module", "polish_item", "_polish_item_backup", "polish_all_items",
]

HEADER = '''"""XianyuLive 的订单与商品方法簇（自 XianyuAutoAsync.py 拆出，P2-x 步骤④）。

方法经 self/cls 操作宿主实例状态；XianyuAutoAsync 模块级剩余符号
（db_manager、order_status_handler、各种模块函数/常量）通过 `_host` 代理在
调用时解析 —— 兼容测试对宿主模块属性的替换，且无导入环。
"""
import asyncio
import json
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

def _db_package():
    """惰性包属性：等价于原方法体内的 from db_manager import db_manager（调用时取包现值）。"""
    from db_manager import db_manager

    return db_manager


def _db_host():
    """宿主绑定：等价于原模块级 from-import 名字（import 期绑定）。"""
    import XianyuAutoAsync

    return XianyuAutoAsync.db_manager



class _HostProxy:
    """属性访问转发到 XianyuAutoAsync 模块级符号（调用时解析）。"""

    def __getattr__(self, name):
        import XianyuAutoAsync

        return getattr(XianyuAutoAsync, name)


_host = _HostProxy()
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
    missing = [n for n in ORDER + ITEM if n not in methods]
    assert not missing, f"missing: {missing}"

    hb = {"asyncio", "json", "re", "time", "Any", "Dict", "List", "Optional", "Tuple",
          "logger", "_host", "_HostProxy", "OrderMixin", "ItemMixin", "XianyuLive"}

    moved = [methods[n] for n in ORDER + ITEM]
    used = {ident for m in moved for ident in (x[4] for x in names_with_pos(m))}
    unknown = used - rs_globals - bi - hb - set(ORDER) - set(ITEM) - local_bound(ast.Module(body=moved, type_ignores=[]))
    assert not unknown, f"unresolvable: {sorted(unknown)}"

    def build(names):
        parts = []
        for n in names:
            m = methods[n]
            a, b = span(m)
            text = "\n".join(src_lines[a - 1:b])
            lazy = 'from db_manager import db_manager' in text
            repls = {}
            for (ln, col, eln, ecol, ident) in names_with_pos(m):
                if ident == "db_manager" and ident in rs_globals:
                    repls[(ln, col, eln, ecol)] = '_db_package()' if lazy else '_db_host()'
                elif ident in rs_globals and ident not in bi and ident not in hb and ident not in set(ORDER) and ident not in set(ITEM):
                    repls[(ln, col, eln, ecol)] = f"_host.{ident}"
            text = rewrite(text, a, repls)
            text = text.replace("XianyuLive.", "self.")
            parts.append(text)
        return parts

    out = [HEADER.rstrip("\n"), "", "", "class OrderMixin:", '    """订单详情/状态同步/交付恢复方法簇（状态在宿主 XianyuLive 上）。"""', ""]
    out.extend(t for t in build(ORDER))
    out.append("")
    out.append("")
    out.append("class ItemMixin:")
    out.append('    """商品详情缓存/搜索/擦亮方法簇。"""')
    out.append("")
    out.extend(t for t in build(ITEM))
    OUT.write_text("\n".join(out), encoding="utf-8")

    dead = [span(methods[n]) for n in ORDER + ITEM]
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

    anchor = "from xianyu_auth_recovery import (\n"
    assert anchor in text
    text = text.replace(anchor,
                        "from xianyu_trading_mixins import ItemMixin, OrderMixin\n" + anchor, 1)
    old_cls = "class XianyuLive(XianyuAuthRecoveryMixin):"
    assert old_cls in text
    text = text.replace(old_cls, "class XianyuLive(OrderMixin, ItemMixin, XianyuAuthRecoveryMixin):", 1)
    NEW.write_text(text, encoding="utf-8")
    print(f"mixins: {OUT}")
    print(f"XianyuAutoAsync: {len(src_lines)} -> {len(text.splitlines())} lines")


if __name__ == "__main__":
    main()
