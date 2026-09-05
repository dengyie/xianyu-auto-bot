#!/usr/bin/env python3
"""Strangler-Fig extractor: move a route domain out of reply_server.py (P1 pilot; kept as
reference/tooling for the next domain batches - edit the *_ROUTES/*_HELPERS lists per batch.

Generates:
  app/api/state.py            (ctx proxy: request-time resolution of reply_server globals)
  app/api/routers/login.py    (create_login_router: auth state/helpers/models + 15 routes)
  app/api/routers/cookies.py  (create_cookies_router: 12 core cookies routes)
  reply_server.new.py         (blocks removed + router wiring)

Identifier rewriting is AST-exact: only ast.Name nodes whose id resolves to a
reply_server module-level global (and is not imported locally / builtin) become
`ctx.<name>`. Kwarg names, attribute names, and builtins are never touched, because
ast represents them outside ast.Name.
"""
import ast
import builtins
import sys
import symtable
from pathlib import Path

ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
SRC = ROOT / "reply_server.py"

AUTH_STATE_TARGETS = [
    "login_ip_tracker", "login_user_tracker", "ip_blacklist",
    "username_rate_tracker", "captcha_storage",
    "CAPTCHA_EXPIRE_SECONDS", "CAPTCHA_REQUIRE_AFTER_FAILURES",
]
AUTH_HELPERS = [
    "cleanup_login_trackers", "check_ip_blocked", "check_user_locked",
    "record_login_failure", "record_login_success", "check_username_rate_limit",
    "record_username_rate", "get_response_delay", "is_captcha_required",
    "generate_captcha_image", "generate_captcha_code", "cleanup_expired_captchas",
    "verify_login_captcha", "get_ip_failure_count",
]
AUTH_MODELS = [
    "LoginRequest", "LoginResponse", "ChangePasswordRequest", "RegisterRequest",
    "RegisterResponse", "SendCodeRequest", "SendCodeResponse", "CaptchaRequest",
    "CaptchaResponse", "VerifyCaptchaRequest", "VerifyCaptchaResponse",
]
AUTH_ROUTES = [
    ("get", "/captcha/generate"), ("get", "/captcha/check-required"),
    ("get", "/login.html"), ("get", "/register.html"), ("post", "/login"),
    ("get", "/admin/security/login-stats"), ("post", "/admin/security/unblock-ip/{ip}"),
    ("post", "/admin/security/unlock-user/{username}"), ("post", "/admin/security/blacklist-ip/{ip}"),
    ("post", "/admin/security/update-config"), ("post", "/change-admin-password"),
    ("post", "/generate-captcha"), ("post", "/verify-captcha"),
    ("post", "/send-verification-code"), ("post", "/register"),
]
COOKIES_ROUTES = [
    ("get", "/cookies"), ("get", "/cookies/details"), ("post", "/cookies"),
    ("put", "/cookies/{cid}"), ("post", "/cookie/{cid}/account-info"),
    ("get", "/cookie/{cid}/details"), ("get", "/cookies/{cid}/runtime-status"),
    ("get", "/cookies/{cid}/conversations/{conversation_id}/history"),
    ("post", "/cookies/{cid}/session-keepalive"),
    ("get", "/cookie/{cid}/proxy"), ("post", "/cookie/{cid}/proxy"),
    ("delete", "/cookies/{cid}"),
]

HEADER_IMPORTS = '''from typing import Any, Dict, List, Optional, Tuple, Callable, Awaitable
from collections import defaultdict
from datetime import datetime, timedelta
import asyncio
import base64
import hashlib
import io
import json
import os
import random
import re
import secrets
import time
import urllib.parse
from urllib.parse import unquote
from urllib import request as urllib_request, error as urllib_error

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from fastapi import (APIRouter, BackgroundTasks, Depends, File, Form, Header,
                     HTTPException, Request, Response, UploadFile, status)
from fastapi.responses import (HTMLResponse, JSONResponse, RedirectResponse,
                               StreamingResponse)
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from loguru import logger
from pydantic import BaseModel
'''

STATE_PY = '''"""Strangler-Fig 迁移期上下文（P1 拆分中间层）。

路由模块经 `ctx` 在**请求时**动态解析 reply_server 的模块级符号，保证：
1. 不产生循环导入（router -> state -> (lazy) reply_server）；
2. reply_server 侧的运行时替换依然生效 —— 尤其是测试 conftest 的
   `reply_server.db_manager = ...` 这类补丁，对已迁出的路由同样可见。

迁移收尾（共享 helper 全部下沉 app/api/common 之类）后，本模块应删除。
"""


class ApiContext:
    """属性访问即转发到 reply_server 模块级名字（延迟到调用时解析）。"""

    def __getattr__(self, name: str):
        import reply_server

        return getattr(reply_server, name)


ctx = ApiContext()
'''

LOGIN_FACTORY_PARAMS = ("ctx", "session_service", "security", "verify_dependency",
                        "admin_username", "router")


def span(node):
    start = node.lineno
    if getattr(node, "decorator_list", None):
        start = min(d.lineno for d in node.decorator_list)
    return start, node.end_lineno


def route_key(node):
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return None
    for dec in node.decorator_list:
        if (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
                and isinstance(dec.func.value, ast.Name) and dec.func.value.id == "app"
                and dec.func.attr in {"get", "post", "put", "delete", "websocket"}
                and dec.args and isinstance(dec.args[0], ast.Constant)):
            return (dec.func.attr, dec.args[0].value)
    return None


def local_bound(tree_fragment):
    """Names bound locally anywhere in fragment: Store Names, args, except/with/for
    targets, nested def/class names, and function-level import bindings."""
    bound = set()
    for x in ast.walk(tree_fragment):
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


def rewrite(text: str, base_line: int, repls: dict) -> str:
    # ast col_offset is a UTF-8 BYTE offset; slice on bytes to survive CJK literals
    lines = [l.encode("utf-8") for l in text.split("\n")]
    for (ln, col, eln, ecol), new in sorted(repls.items(), reverse=True):
        i, j = ln - base_line, eln - base_line
        nb = new.encode("utf-8")
        if i == j:
            lines[i] = lines[i][:col] + nb + lines[i][ecol:]
        else:
            lines[i] = lines[i][:col] + nb
    return "\n".join(l.decode("utf-8") for l in lines)


def indent4(text: str) -> str:
    return "\n".join(("    " + ln if ln.strip() else ln) for ln in text.split("\n"))


def extract(fragment, src_lines, ctx_names: set, is_route: bool) -> str:
    start, end = span(fragment)
    text = "\n".join(src_lines[start - 1:end])
    repls = {}
    for (ln, col, eln, ecol, ident) in names_with_pos(fragment):
        if ident not in ctx_names:
            continue
        if any(d.lineno <= ln <= d.end_lineno for d in getattr(fragment, "decorator_list", [])):
            continue  # decorators handled below
        repls[(ln, col, eln, ecol)] = f"ctx.{ident}"
    out = rewrite(text, start, repls)
    if is_route:
        # rewrite decorator-line names: `app` -> `router`; ctx-rewrite the rest
        for dec in fragment.decorator_list:
            d_repls = {}
            for (ln, col, eln, ecol, ident) in names_with_pos(dec):
                if ident == "app" and isinstance(dec, ast.Call) and dec.func.value is \
                        next(n for n in ast.walk(dec) if isinstance(n, ast.Name) and n.id == "app"
                             and n.col_offset == col and n.lineno == ln):
                    d_repls[(ln, col, eln, ecol)] = "router"
                elif ident in ctx_names:
                    d_repls[(ln, col, eln, ecol)] = f"ctx.{ident}"
            out = rewrite(out, start, d_repls)
    return out


def main():
    src = SRC.read_text(encoding="utf-8")
    src_lines = src.split("\n")
    tree = ast.parse(src)

    st = symtable.symtable(src, str(SRC), "exec")
    rs_globals = {s.get_name() for s in st.get_symbols() if s.is_global()}
    bi = set(dir(builtins))

    hb = set()
    for node in ast.parse(HEADER_IMPORTS).body:
        if isinstance(node, ast.Import):
            hb |= {(a.asname or a.name).split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            hb |= {a.asname or a.name for a in node.names}

    routes, funcs, classes, assigns = {}, {}, {}, {}
    for node in tree.body:
        rk = route_key(node)
        if rk:
            routes.setdefault(rk, node)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcs[node.name] = node
        elif isinstance(node, ast.ClassDef):
            classes[node.name] = node
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            for t in (node.targets if isinstance(node, ast.Assign) else [node.target]):
                if isinstance(t, ast.Name):
                    assigns.setdefault(t.id, node)

    for r in AUTH_ROUTES + COOKIES_ROUTES:
        assert r in routes, f"missing route {r}"
    for n in AUTH_HELPERS:
        assert n in funcs, f"missing func {n}"
    for n in AUTH_MODELS:
        assert n in classes, f"missing class {n}"
    for n in AUTH_STATE_TARGETS:
        assert n in assigns, f"missing assign {n}"

    # ---------------- login.py ----------------
    # state containers STAY in reply_server (test contract: reply_server.login_ip_tracker etc.)
    # -> moved helpers access them via ctx at request time (rebinding-safe)
    login_module_names = set(AUTH_HELPERS) | set(AUTH_MODELS)
    login_ctx = rs_globals - hb - bi - login_module_names
    login_expected_locals = login_module_names | set(LOGIN_FACTORY_PARAMS) | {"router"}

    login_nodes = ([assigns[n] for n in AUTH_STATE_TARGETS]
                   + [funcs[n] for n in AUTH_HELPERS]
                   + [classes[n] for n in AUTH_MODELS]
                   + [routes[r] for r in AUTH_ROUTES])
    used = {ident for nd in login_nodes for ident in
            (x[4] for x in names_with_pos(nd))}
    unknown = used - rs_globals - bi - hb - login_expected_locals - local_bound(ast.Module(
        body=login_nodes, type_ignores=[]))
    assert not unknown, f"login.py unresolvable names: {sorted(unknown)}"

    out = ['"""Login / register / captcha / login-security routes (Strangler Fig P1).', "",
           "Mechanically extracted from reply_server.py at main@0aa4100; behavior-preserving.",
           "External (reply_server) symbols resolve via ctx at request time - see app/api/state.py.", '"""', ""]
    out.append(HEADER_IMPORTS.rstrip("\n"))
    out.append("")
    out.append("from app.api.state import ctx  # noqa: F401  (module-level helpers; factory param shadows with same singleton)")
    out.append("")
    out.append("")
    out.append("# 防暴力破解/验证码状态容器留在 reply_server（tests 直接操作 reply_server.login_ip_tracker 等）")
    out.append("# -> 本模块 helpers 经 ctx.<name> 请求时解析访问")
    out.append("")
    out.append("# ========================= 登录安全 helpers =========================")
    for n in AUTH_HELPERS:
        out.append(extract(funcs[n], src_lines, login_ctx, is_route=False))
        out.append("")
        out.append("")
    out.append("# ========================= request/response models =========================")
    for n in AUTH_MODELS:
        out.append(extract(classes[n], src_lines, login_ctx, is_route=False))
        out.append("")
        out.append("")
    out.append("")
    out.append("def create_login_router(ctx, session_service, security, verify_dependency, "
               "admin_username) -> APIRouter:")
    out.append('    """Factory keeps the established create_auth_router dependency style."""')
    out.append("    router = APIRouter()")
    for r in AUTH_ROUTES:
        out.append(indent4(extract(routes[r], src_lines, login_ctx, is_route=True)))
        out.append("")
    out.append("    return router")
    out.append("")
    (ROOT / "app/api/routers/login.py").write_text("\n".join(out), encoding="utf-8")

    # ---------------- cookies.py ----------------
    cookies_ctx = rs_globals - hb - bi - {"router", "ctx"}
    cookie_nodes = [routes[r] for r in COOKIES_ROUTES]
    used = {ident for nd in cookie_nodes for ident in (x[4] for x in names_with_pos(nd))}
    unknown = used - rs_globals - bi - hb - {"router", "ctx"} - local_bound(ast.Module(
        body=cookie_nodes, type_ignores=[]))
    assert not unknown, f"cookies.py unresolvable names: {sorted(unknown)}"

    out = ['"""Core cookies CRUD routes (Strangler Fig P1).', "",
           "Mechanically extracted from reply_server.py at main@0aa4100; behavior-preserving.",
           "External (reply_server) symbols resolve via ctx at request time - see app/api/state.py.", '"""', ""]
    out.append(HEADER_IMPORTS.rstrip("\n"))
    out.append("")
    out.append("")
    out.append("def create_cookies_router(ctx) -> APIRouter:")
    out.append("    router = APIRouter()")
    for r in COOKIES_ROUTES:
        out.append(indent4(extract(routes[r], src_lines, cookies_ctx, is_route=True)))
        out.append("")
    out.append("    return router")
    out.append("")
    (ROOT / "app/api/routers/cookies.py").write_text("\n".join(out), encoding="utf-8")

    # ---------------- app/api/state.py ----------------
    (ROOT / "app/api/state.py").write_text(STATE_PY, encoding="utf-8")

    # ---------------- reply_server.new.py ----------------
    # NOTE: auth state block (login_ip_tracker 等) intentionally KEPT in reply_server
    dead_spans = []
    for n in AUTH_HELPERS:
        dead_spans.append(span(funcs[n]))
    for n in AUTH_MODELS:
        dead_spans.append(span(classes[n]))
    for r in AUTH_ROUTES + COOKIES_ROUTES:
        dead_spans.append(span(routes[r]))

    keep = [True] * (len(src_lines) + 1)
    for a, b in dead_spans:
        for i in range(a, b + 1):
            keep[i] = False
    kept = [ln for i, ln in enumerate(src_lines, 1) if keep[i]]
    # collapse 3+ blank lines to 2
    cleaned, blanks = [], 0
    for ln in kept:
        blanks = blanks + 1 if ln.strip() == "" else 0
        if blanks <= 2:
            cleaned.append(ln)
    text = "\n".join(cleaned)

    # imports
    anchor = "from app.application.auth.sessions import SessionService\n"
    assert anchor in text
    text = text.replace(anchor, anchor +
                        "from app.api.routers.cookies import create_cookies_router\n"
                        "from app.api.routers.login import create_login_router\n"
                        "from app.api.state import ctx\n", 1)

    # router registration at END of module: factory-time Depends/annotation resolution
    # (e.g. ctx.CookieIn) needs every reply_server global already defined.
    reg = ("\n\n"
           "# ========================= Strangler Fig P1: 已拆分路由域注册（app/api/routers/）=========================\n"
           "# 置于模块末尾：factory 装饰期需解析全部模块级符号（如 CookieIn）。\n"
           "app.include_router(\n"
           "    create_login_router(\n"
           "        ctx=ctx,\n"
           "        session_service=session_service,\n"
           "        security=security,\n"
           "        verify_dependency=verify_token,\n"
           "        admin_username=ADMIN_USERNAME,\n"
           "    )\n"
           ")\n"
           "app.include_router(create_cookies_router(ctx=ctx))\n")
    text = text.rstrip("\n") + reg

    (ROOT / "reply_server.new.py").write_text(text, encoding="utf-8")
    old_n = len(src_lines)
    new_n = len(text.split("\n"))
    print(f"reply_server: {old_n} -> {new_n} lines (-{old_n - new_n})")
    print("login.py / cookies.py / state.py written")


if __name__ == "__main__":
    main()
