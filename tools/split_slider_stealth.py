#!/usr/bin/env python3
"""Split XianyuSliderStealth: extract password-login + stealth-script clusters into mixins.

- utils/slider_stealth_mixins.py: PasswordLoginMixin + StealthScriptMixin
- methods reach remaining module-level singletons/funcs of
  utils.xianyu_slider_stealth via a lazy _host proxy (call-time resolution,
  monkeypatch-safe, no import cycle)
- the shadowed dead duplicate _get_stealth_script (first def) is cut without move
"""
import ast
import builtins
import sys
import symtable
from pathlib import Path

ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
SRC = ROOT / "utils" / "xianyu_slider_stealth.py"
OUT = ROOT / "utils" / "slider_stealth_mixins.py"
NEW = ROOT / "utils" / "xianyu_slider_stealth.new.py"

PASSWORD_LOGIN = [
    "login_with_password_playwright", "login_with_password_headful",
    "_is_password_login_scene", "_has_completed_login_cookies", "_page_has_keep_login_prompt",
    "_get_password_login_selectors", "_probe_login_form_state", "_find_login_form_with_retry",
    "_prepare_login_page_after_cleanup", "_page_has_login_form", "_probe_context_login_success",
    "_recover_from_missing_login_inputs", "_wait_for_context_login",
    "_probe_context_login_during_slider", "_check_login_success_by_element", "_check_login_error",
    "_start_password_login_slider_risk_log", "_finish_password_login_slider_risk_log",
    "_get_password_scene_final_retry_template", "_get_cookies_after_success", "_fail_login",
]
STEALTH = ["_get_stealth_script", "_get_light_stealth_script", "_get_random_browser_features", "_get_browser_family"]
PREEXISTING_BARE = {"_mark_detached_runtime"}  # 既有潜伏 NameError（嵌套 def 在别的方法内），保持裸引用行为不变
DEAD_CUT = [("_get_stealth_script", 0)]  # first (shadowed) definition

HEADER = '''"""XianyuSliderStealth 的密码登录与隐身注入 Mixin（自 xianyu_slider_stealth.py 拆出）。

方法经 self/cls 操作宿主实例；宿主模块的剩余模块级符号（单例、常量、函数）
通过 `_host` 代理在调用时解析 —— 兼容运行期替换，且无导入环。
"""
import asyncio
import base64
import hashlib
import io
import json
import math
import os
import random
import re
import secrets
import time
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse, parse_qs

import numpy as np
from loguru import logger


class _HostProxy:
    """属性访问转发到 utils.xianyu_slider_stealth 模块级符号（调用时解析）。"""

    def __getattr__(self, name):
        import utils.xianyu_slider_stealth as _m

        return getattr(_m, name)


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

    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "XianyuSliderStealth")
    methods = {}
    for m in cls.body:
        if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
            methods.setdefault(m.name, []).append(m)

    missing = [n for n in PASSWORD_LOGIN + STEALTH if n not in methods]
    assert not missing, f"missing: {missing}"

    hb = {"asyncio", "base64", "hashlib", "hmac", "io", "json", "math", "os", "random", "re",
          "secrets", "string", "time", "np", "logger", "urlparse", "parse_qs",
          "Any", "Callable", "Dict", "List", "Optional", "Tuple", "_host", "_HostProxy",
          "PasswordLoginMixin", "StealthScriptMixin", "XianyuSliderStealth"}

    def build_group(names, taken):
        nodes = []
        for n in names:
            nodes.append(methods[n][-1])  # living definition (last wins)
        used = {ident for m in nodes for ident in (x[4] for x in names_with_pos(m))}
        unknown = used - rs_globals - bi - hb - set(names) - taken - PREEXISTING_BARE - local_bound(ast.Module(body=nodes, type_ignores=[]))
        assert not unknown, f"unresolvable: {sorted(unknown)}"
        parts = []
        for m in nodes:
            a, b = span(m)
            text = "\n".join(src_lines[a - 1:b])
            # positions come from the ORIGINAL file AST: text lines map 1:1 to file lines
            frag_repls = {}
            for (ln, col, eln, ecol, ident) in names_with_pos(m):
                if ident in rs_globals and ident not in bi and ident not in hb and ident not in PREEXISTING_BARE and ident not in set(names):
                    frag_repls[(ln, col, eln, ecol)] = f"_host.{ident}"
            text = rewrite(text, a, frag_repls)
            # class-name self-references last (pure text, positions no longer matter)
            text = text.replace("XianyuSliderStealth.", "self.")
            parts.append(text)
        return parts
        return parts

    login_parts = build_group(PASSWORD_LOGIN, set())
    stealth_parts = build_group(STEALTH, set(PASSWORD_LOGIN))

    out = [HEADER.rstrip("\n"), "", "", "class PasswordLoginMixin:", '    """密码登录（Playwright/Headful）全流程与登录态探测。"""', ""]
    for t in login_parts:
        out.append(t)
        out.append("")
    out.append("")
    out.append("class StealthScriptMixin:")
    out.append('    """隐身注入脚本与浏览器特征伪装。"""')
    out.append("")
    for t in stealth_parts:
        out.append(t)
        out.append("")
    OUT.write_text("\n".join(out), encoding="utf-8")

    # ---- cut from source: moved methods (ALL defs incl. shadowed) + dead dup ----
    dead = []
    for n in PASSWORD_LOGIN + STEALTH:
        for m in methods.get(n, []):
            dead.append(span(m))
    for name, idx in DEAD_CUT:
        dead.append(span(methods[name][idx]))
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
    assert "class XianyuSliderStealth:" in text
    text = text.replace("class XianyuSliderStealth:", "class XianyuSliderStealth(StealthScriptMixin, PasswordLoginMixin):", 1)
    anchor = "from loguru import logger\n"
    if anchor not in text:
        anchor = "import numpy as np\n"
    assert anchor in text, "no import anchor"
    text = text.replace(anchor, anchor + "from utils.slider_stealth_mixins import PasswordLoginMixin, StealthScriptMixin\n", 1)
    NEW.write_text(text, encoding="utf-8")
    print(f"mixins: {OUT}")
    print(f"slider module: {len(src_lines)} -> {len(text.splitlines())} lines")


if __name__ == "__main__":
    main()
