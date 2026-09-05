#!/usr/bin/env python3
"""Route-domain batch extractor for reply_server.py (Strangler Fig, P2 batch mode).

Per batch: moves ONLY route handlers (helpers/models/state stay in reply_server;
handlers resolve them via ctx at request time) into a domain router module.

Usage:
  python tools/split_route_domain.py <repo_root> BATCH_KEY

Batches are defined in BATCHES below. For each batch:
  - route handlers are cut out of reply_server.py (AST-exact, byte-offset safe)
  - identifiers resolving to reply_server globals are rewritten to ctx.<name>
  - routes are appended (indented) into the target factory before `return router`
    (target module must already exist with `def create_<x>_router(ctx)`)
  - reply_server.new.py is written; caller reviews then replaces reply_server.py

Notes carried over from P1 (do not regress):
  - ast col_offset is a UTF-8 BYTE offset -> rewrite slices bytes, not str
  - local bindings include function-level imports and nested def/class names
  - decorator lines: `@app.` -> `@router.`; other decorator names go through ctx
  - include_router at reply_server module END is required (decoration-time
    resolution of late globals like CookieIn)
"""
import ast
import builtins
import sys
import symtable
from pathlib import Path

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

# ---------------- batch definitions (edit per batch) ----------------
# (method, path) as registered in reply_server; module = app/api/routers/<module>.py
BATCHES = {
    "B1_cookies_ext": {
        "module": "cookies",
        "routes": [
            ("put", "/cookies/{cid}/status"),
            ("put", "/cookies/{cid}/auto-confirm"), ("get", "/cookies/{cid}/auto-confirm"),
            ("put", "/cookies/{cid}/auto-comment"), ("get", "/cookies/{cid}/auto-comment"),
            ("get", "/cookies/{cid}/comment-templates"), ("post", "/cookies/{cid}/comment-templates"),
            ("put", "/cookies/{cid}/comment-templates/{template_id}"),
            ("delete", "/cookies/{cid}/comment-templates/{template_id}"),
            ("put", "/cookies/{cid}/comment-templates/{template_id}/activate"),
            ("put", "/cookies/{cid}/remark"), ("get", "/cookies/{cid}/remark"),
            ("put", "/cookies/{cid}/pause-duration"), ("get", "/cookies/{cid}/pause-duration"),
            ("get", "/cookies/check"),
        ],
    },
    "B2_settings_notif_keywords": {
        "module": "settings",
        "new_module": ("settings", "create_settings_router",
                       "Settings / registration / user-settings routes (Strangler Fig P2-B2)."),
        "routes": [
            ("get", "/system-settings"), ("put", "/system-settings/{key}"),
            ("get", "/registration-status"), ("get", "/login-info-status"),
            ("put", "/registration-settings"), ("put", "/login-info-settings"),
            ("get", "/login-captcha-settings"), ("put", "/login-captcha-settings"),
            ("get", "/api/login-captcha-enabled"),
            ("get", "/user-settings"), ("put", "/user-settings/{key}"), ("get", "/user-settings/{key}"),
            ("get", "/api/sales"), ("get", "/api/sales/summary"),
        ],
    },
    "B2b_notifications": {
        "module": "notifications",
        "new_module": ("notifications", "create_notifications_router",
                       "Notification channels / messages / templates routes (Strangler Fig P2-B2b)."),
        "routes": [
            ("get", "/notification-channels"), ("post", "/notification-channels"),
            ("get", "/notification-channels/{channel_id}"), ("put", "/notification-channels/{channel_id}"),
            ("delete", "/notification-channels/{channel_id}"),
            ("get", "/message-notifications"), ("get", "/message-notifications/{cid}"),
            ("post", "/message-notifications/{cid}"), ("delete", "/message-notifications/account/{cid}"),
            ("delete", "/message-notifications/{notification_id}"),
            ("get", "/notification-templates"), ("post", "/notification-templates/test"),
            ("get", "/notification-templates/{template_type}"), ("put", "/notification-templates/{template_type}"),
            ("post", "/notification-templates/{template_type}/reset"),
            ("get", "/notification-templates/{template_type}/default"),
        ],
    },
    "B2c_keywords_replies": {
        "module": "keywords",
        "new_module": ("keywords", "create_keywords_router",
                       "Keywords + default-replies routes (Strangler Fig P2-B2c)."),
        "routes": [
            ("get", "/keywords/{cid}"), ("get", "/keywords-with-item-id/{cid}"),
            ("post", "/keywords/{cid}"), ("post", "/keywords-with-item-id/{cid}"),
            ("get", "/keywords-export/{cid}"), ("post", "/keywords-import/{cid}"),
            ("post", "/keywords/{cid}/image"), ("post", "/keywords/{cid}/image-batch"),
            ("get", "/keywords-with-type/{cid}"), ("delete", "/keywords/{cid}/{index}"),
            ("get", "/debug/keywords-table-info"),
            ("get", "/default-replies/{cid}"), ("put", "/default-replies/{cid}"),
            ("get", "/default-replies"), ("delete", "/default-replies/{cid}"),
            ("post", "/default-replies/{cid}/clear-records"),
        ],
    },
    "B3_accounts_login": {
        "module": "accountlogin",
        "new_module": ("accountlogin", "create_account_login_router",
                       "QR/password/manual-cookie face-verification login routes (Strangler Fig P2-B3)."),
        "routes": [
            ("post", "/manual-cookie-import"), ("get", "/manual-cookie-import/check/{session_id}"),
            ("post", "/password-login"), ("get", "/password-login/check/{session_id}"),
            ("post", "/password-login/cancel/{session_id}"),
            ("get", "/face-verification/screenshot/{account_id}"),
            ("delete", "/face-verification/screenshot/{account_id}"),
            ("post", "/qr-login/generate"), ("get", "/qr-login/check/{session_id}"),
            ("post", "/qr-login/submit-cookies/{session_id}"), ("post", "/qr-login/submit-url/{session_id}"),
            ("post", "/qr-login-lite/generate"), ("get", "/qr-login-lite/check/{session_id}"),
            ("post", "/qr-login/refresh-cookies"), ("post", "/qr-login/reset-cooldown/{cookie_id}"),
            ("get", "/qr-login/cooldown-status/{cookie_id}"),
        ],
    },
    "B4_trading_items": {
        "module": "trading",
        "new_module": ("trading", "create_trading_router",
                       "Items / cards / delivery-rules / product-publish routes (Strangler Fig P2-B4)."),
        "routes": [
            ("get", "/items/{cid}"), ("post", "/upload-image"),
            ("get", "/cards"), ("post", "/cards"), ("get", "/cards/{card_id}"),
            ("put", "/cards/{card_id}"), ("put", "/cards/{card_id}/image"), ("delete", "/cards/{card_id}"),
            ("get", "/delivery-rules"), ("get", "/delivery-rules/stats"), ("get", "/delivery-logs/recent"),
            ("post", "/delivery-rules"), ("get", "/delivery-rules/{rule_id}"),
            ("put", "/delivery-rules/{rule_id}"), ("delete", "/delivery-rules/{rule_id}"),
            ("get", "/items"), ("post", "/items/search"), ("post", "/items/search_multiple"),
            ("get", "/items/cookie/{cookie_id}"), ("get", "/items/{cookie_id}/{item_id}"),
            ("put", "/items/{cookie_id}/{item_id}"), ("delete", "/items/{cookie_id}/{item_id}"),
            ("delete", "/items/batch"), ("put", "/items/{cookie_id}/{item_id}/multi-spec"),
            ("put", "/items/{cookie_id}/{item_id}/multi-quantity-delivery"),
            ("post", "/items/get-all-from-account"), ("post", "/items/get-by-page"),
            ("get", "/product-materials"), ("post", "/product-materials"),
            ("get", "/product-materials/{material_id}"), ("put", "/product-materials/{material_id}"),
            ("delete", "/product-materials/{material_id}"),
            ("get", "/publish-logs"), ("delete", "/publish-logs/old"),
            ("post", "/product-publish"), ("post", "/product-publish/batch"),
            ("get", "/product-publish/batch/{batch_id}"), ("post", "/item-publish"),
            ("get", "/itemReplays"), ("get", "/itemReplays/cookie/{cookie_id}"),
            ("put", "/item-reply/{cookie_id}/{item_id}"), ("delete", "/item-reply/{cookie_id}/{item_id}"),
            ("delete", "/item-reply/batch"), ("get", "/item-reply/{cookie_id}/{item_id}"),
        ],
    },
    "B5_admin_ops": {
        "module": "adminops",
        "new_module": ("adminops", "create_admin_ops_router",
                       "Admin / ops / logs / backup / update / files / groups / blacklist routes (P2-B5)."),
        "routes": [
            ("get", "/ai-reply-settings/{cookie_id}"), ("put", "/ai-reply-settings/{cookie_id}"),
            ("get", "/ai-reply-settings"), ("get", "/ai-config-presets"),
            ("post", "/ai-config-presets"), ("delete", "/ai-config-presets/{preset_id}"),
            ("post", "/ai-reply-test/{cookie_id}"),
            ("get", "/api/task-logs"), ("get", "/api/auto-comment/logs"),
            ("post", "/api/auto-comment/batch-rate"),
            ("get", "/logs"), ("get", "/risk-control-logs"), ("get", "/logs/stats"), ("post", "/logs/clear"),
            ("get", "/admin/slider-verification-stats"), ("delete", "/admin/risk-control-logs/{log_id}"),
            ("get", "/admin/users"), ("delete", "/admin/users/{user_id}"),
            ("put", "/admin/users/{user_id}/admin-status"),
            ("get", "/admin/risk-control-logs"), ("get", "/admin/cookies"), ("get", "/admin/audit-logs"),
            ("get", "/admin/logs"), ("get", "/admin/log-files"), ("get", "/admin/logs/export"),
            ("get", "/admin/stats"),
            ("get", "/admin/backup/download"), ("post", "/admin/backup/upload"), ("get", "/admin/backup/list"),
            ("get", "/admin/data/{table_name}"), ("get", "/admin/data/{table_name}/export"),
            ("delete", "/admin/data/{table_name}/{record_id}"), ("delete", "/admin/data/{table_name}"),
            ("get", "/backup/export"), ("post", "/backup/import"), ("post", "/system/reload-cache"),
            ("get", "/api/update/check"), ("post", "/api/update/apply"), ("get", "/api/update/progress"),
            ("get", "/api/update/local-hashes"), ("post", "/api/update/cleanup-backups"),
            ("get", "/api/update/file-changes"), ("post", "/api/update/save-hashes"),
            ("get", "/api/update/saved-hashes"), ("post", "/api/update/restart"),
            ("post", "/accounts/{cid}/polish-items"),
            ("post", "/scheduled-tasks"), ("get", "/scheduled-tasks"), ("put", "/scheduled-tasks/{task_id}"),
            ("delete", "/scheduled-tasks/{task_id}"), ("put", "/scheduled-tasks/{task_id}/toggle"),
            ("post", "/api/analytics/error"),
            ("get", "/api/files"), ("get", "/api/files/{file_id}/download"), ("post", "/api/files"),
            ("put", "/api/files/{file_id}"), ("delete", "/api/files/{file_id}"),
            ("get", "/api/files/{file_id}/download-token"), ("get", "/api/files/{file_id}/direct"),
            ("post", "/api/groups"), ("get", "/api/groups"), ("get", "/api/groups/{group_id}/members"),
            ("delete", "/api/groups/{group_id}"), ("post", "/api/groups/{group_id}/members"),
            ("delete", "/api/groups/{group_id}/members/{user_id}"),
            ("get", "/api/blacklist/personal"), ("post", "/api/blacklist/personal"),
            ("post", "/api/blacklist/personal/batch-delete"), ("delete", "/api/blacklist/personal/{record_id}"),
            ("get", "/api/blacklist/personal/export"), ("post", "/api/blacklist/personal/import"),
            ("get", "/api/blacklist/platform"),
            ("get", "/api/announcement"),
        ],
    },
    "B6b_patch_blacklist": {
        "module": "adminops",
        "routes": [
            ("patch", "/api/blacklist/personal/{record_id}/toggle"),
        ],
    },
    "B6_orders_chat_misc": {
        "module": "orderschat",
        "new_module": ("orderschat", "create_orders_chat_router",
                       "Orders / chat / send-message / dashboard page routes (Strangler Fig P2-B6)."),
        "routes": [
            ("post", "/send-message"), ("post", "/xianyu/reply"),
            ("post", "/api/orders/history-sync"), ("get", "/api/orders/history-sync/{job_id}"),
            ("post", "/api/orders/history-sync/{job_id}/cancel"), ("post", "/api/orders/recover"),
            ("get", "/api/orders"), ("get", "/api/orders/stream"),
            ("delete", "/api/orders/{order_id}"), ("post", "/api/orders/{order_id}/confirm-retry"),
            ("post", "/api/orders/{order_id}/deliver"), ("post", "/api/orders/{order_id}/refresh"),
            ("get", "/api/chat/sessions"), ("get", "/api/chat/messages"), ("post", "/api/chat/send"),
            ("get", "/api/chat/stream"), ("get", "/api/chat/accounts"),
            ("get", "/api/chat/keywords/{cid}/item/{item_id}"),
            ("post", "/api/chat/keywords/{cid}/item/{item_id}"),
            ("post", "/api/chat/keywords/{cid}/copy"), ("get", "/api/chat/items/{cid}"),
            ("get", "/"), ("get", "/admin"), ("get", "/download"),
        ],
    },
}


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
                and dec.func.attr in {"get", "post", "put", "delete", "patch", "websocket"}
                and dec.args and isinstance(dec.args[0], ast.Constant)):
            return (dec.func.attr, dec.args[0].value)
    return None


def local_bound(tree_fragment):
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


def main():
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
    batch_key = sys.argv[2] if len(sys.argv) > 2 else list(BATCHES)[0]
    batch = BATCHES[batch_key]
    rs_path = root / "reply_server.py"
    src = rs_path.read_text(encoding="utf-8")
    src_lines = src.split("\n")
    tree = ast.parse(src)

    st = symtable.symtable(src, str(rs_path), "exec")
    rs_globals = {s.get_name() for s in st.get_symbols() if s.is_global()}
    bi = set(dir(builtins))
    hb = set()
    for node in ast.parse(HEADER_IMPORTS).body:
        if isinstance(node, ast.Import):
            hb |= {(a.asname or a.name).split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            hb |= {a.asname or a.name for a in node.names}

    routes = {}
    dup_hits = []
    for node in tree.body:
        rk = route_key(node)
        if rk:
            if rk in routes:
                dup_hits.append((rk, node.lineno))
            routes.setdefault(rk, node)

    wanted = batch["routes"]
    missing = [r for r in wanted if r not in routes]
    assert not missing, f"missing routes {missing}"
    moved_dups = [d for d in dup_hits if d[0] in wanted]
    if moved_dups:
        print(f"NOTE: duplicate registrations also cut (dead code): {moved_dups}")

    ctx_names = rs_globals - hb - bi - {"router", "ctx"}
    nodes = []
    for r in wanted:
        nodes.append(routes[r])
    for (rk, lineno) in moved_dups:
        for node in tree.body:
            if route_key(node) == rk and node.lineno == lineno:
                nodes.append(node)

    used = {ident for nd in nodes for ident in (x[4] for x in names_with_pos(nd))}
    unknown = used - rs_globals - bi - hb - {"router", "ctx"} - local_bound(ast.Module(
        body=nodes, type_ignores=[]))
    assert not unknown, f"unresolvable names: {sorted(unknown)}"

    extracted = []
    for nd in nodes:
        text = extract_one(nd, src_lines, ctx_names)
        extracted.append(indent4(text))
        extracted.append("")

    # ---- target module: create or append ----
    mod_dir = root / "app" / "api" / "routers"
    if "new_module" in batch:
        mod_name, factory, doc = batch["new_module"]
        target = mod_dir / f"{mod_name}.py"
        assert not target.exists(), f"{target} already exists"
        out = [f'"""{doc}', "",
               "Mechanically extracted from reply_server.py; behavior-preserving.",
               'External (reply_server) symbols resolve via ctx at request time - see app/api/state.py.', '"""', ""]
        out.append(HEADER_IMPORTS.rstrip("\n"))
        out.append("")
        out.append("")
        out.append(f"def {factory}(ctx) -> APIRouter:")
        out.append("    router = APIRouter()")
        out.extend(extracted)
        out.append("    return router")
        out.append("")
        target.write_text("\n".join(out), encoding="utf-8")
        mod_dot = f"app.api.routers.{mod_name}"
        wire = f"app.include_router({factory}(ctx=ctx))\n"
        include_line = f"from {mod_dot} import {factory}\n"
    else:
        mod_name = batch["module"]
        factory = f"create_{mod_name}_router"
        target = mod_dir / f"{mod_name}.py"
        ttext = target.read_text(encoding="utf-8")
        anchor = "    return router"
        assert ttext.rstrip().endswith("    return router"), f"unexpected tail in {target}"
        ttext = ttext.rstrip("\n")[: ttext.rstrip("\n").rfind(anchor)]
        nl = "\n"
        ttext = ttext.rstrip("\n") + "\n" + nl.join(extracted).rstrip("\n") + "\n" + anchor + "\n"
        target.write_text(ttext, encoding="utf-8")
        include_line = None
        wire = None
        # import already present (module pre-wired)
        assert f"routers.{mod_name}" in (root / "reply_server.py").read_text(encoding="utf-8")

    # ---- reply_server.new.py: cut moved handlers, wire new module import+include ----
    dead = [span(nd) for nd in nodes]
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

    if include_line:
        anchor_imp = "from app.api.state import ctx\n"
        assert anchor_imp in text
        text = text.replace(anchor_imp, include_line + anchor_imp, 1)
        tail_anchor = "app.include_router(create_cookies_router(ctx=ctx))"
        assert tail_anchor in text
        text = text.replace(tail_anchor, tail_anchor + "\n" + wire.rstrip("\n"), 1)

    (root / "reply_server.new.py").write_text(text, encoding="utf-8")
    print(f"{batch_key}: moved {len(nodes)} handlers; reply_server {len(src_lines)} -> {len(text.splitlines())} lines")


def extract_one(fragment, src_lines, ctx_names):
    start, end = span(fragment)
    text = "\n".join(src_lines[start - 1:end])
    repls = {}
    for (ln, col, eln, ecol, ident) in names_with_pos(fragment):
        if ident not in ctx_names:
            continue
        if any(d.lineno <= ln <= d.end_lineno for d in getattr(fragment, "decorator_list", [])):
            continue
        repls[(ln, col, eln, ecol)] = f"ctx.{ident}"
    out = rewrite(text, start, repls)
    # decorator lines
    for dec in fragment.decorator_list:
        d_repls = {}
        for (ln, col, eln, ecol, ident) in names_with_pos(dec):
            if ident == "app":
                d_repls[(ln, col, eln, ecol)] = "router"
            elif ident in ctx_names:
                d_repls[(ln, col, eln, ecol)] = f"ctx.{ident}"
        out = rewrite(out, start, d_repls)
    return out.replace("@app.", "@router.", 1)


if __name__ == "__main__":
    main()
