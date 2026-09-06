#!/usr/bin/env python3
"""Phase B helper: rewrite ctx.X in app/api/routers/* per classification.

Also drops the ctx factory parameter and fixes reply_server wiring + the one
test seam that rebinds manual_cookie_import_sessions.
"""
import ast
import io
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def byte_table(src: str):
    lines = src.splitlines(keepends=True)
    starts, off = [], 0
    for ln in lines:
        starts.append(off)
        off += len(ln.encode('utf-8'))
    return starts


def node_span(node, starts):
    s = starts[node.lineno - 1] + node.col_offset
    e = starts[node.end_lineno - 1] + node.end_col_offset
    return s, e


def rewrite_router(path: Path, classify):
    src = io.open(path, encoding='utf-8').read()
    bsrc = src.encode('utf-8')
    starts = byte_table(src)
    tree = ast.parse(src)

    # module-level names already defined/imported here (collision guard)
    local = set()
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            local.add(n.name)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                local.add(a.asname or a.name.split('.')[0])
        elif isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name):
                    local.add(t.id)

    # collect ctx.X attribute nodes + bare ctx loads
    spans = []      # (s, e, replacement)
    used = set()    # classify keys used
    bare_ctx = 0
    for n in ast.walk(tree):
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == 'ctx':
            name = n.attr
            used.add(name)
            s, e = node_span(n, starts)
            kind, payload = classify.get(name, ('rs', None))
            if kind == 'models':
                rep = name if name not in local else f"reply_server.{name}"
                if name in local:
                    kind, payload = 'rs', None
            elif kind == 'common':
                rep = name if name not in local else f"reply_server.{name}"
                if name in local:
                    kind, payload = 'rs', None
            elif kind == 'state':
                rep = f"state.{payload}"
            elif kind == 'origin':
                rep = name if name not in local else f"reply_server.{name}"
                if name in local:
                    kind, payload = 'rs', None
            elif kind == 'module':
                rep = payload.split()[-1].split('.')[-1] if ' as ' not in payload else payload.split(' as ')[-1]
                rep = {'pandas': 'pd', 'pathlib': 'Path'}.get(rep, rep)
            elif kind == 'dbm':
                rep = "db_manager.db_manager"
            else:
                rep = f"reply_server.{name}"
            spans.append((s, e, rep, kind, payload))
        elif isinstance(n, ast.Name) and n.id == 'ctx' and isinstance(n.ctx, ast.Load):
            # bare ctx (e.g. passed as factory kwarg) -> flagged separately
            parent_is_attr = False
            bare_ctx += 1

    if not used and bare_ctx == 0:
        return None

    # apply replacements descending
    for s, e, rep, kind, payload in sorted(spans, key=lambda x: -x[0]):
        bsrc = bsrc[:s] + rep.encode('utf-8') + bsrc[e:]
    src2 = bsrc.decode('utf-8')

    # drop ctx param from factory signature
    src2 = re.sub(r"def (create_\w+_router)\(ctx\)", r"def \1()", src2)
    src2 = re.sub(r"def (create_login_router)\(ctx, ", r"def \1(", src2)

    # drop ctx import line if present
    src2 = re.sub(r"^from app\.api\.state import ctx.*\n", "", src2, flags=re.M)

    # gather imports to add
    adds = []
    need_models = sorted({k for k, v in ((n, classify.get(n, ('rs', None))) for n in used) if v[0] == 'models'} & set(classify))
    need_models = sorted(n for n in used if classify.get(n, ('rs', None))[0] == 'models')
    need_common = sorted(n for n in used if classify.get(n, ('rs', None))[0] == 'common')
    need_origin = {}
    for n in used:
        k, p = classify.get(n, ('rs', None))
        if k == 'origin':
            need_origin.setdefault(p, []).append(n)
    need_modules = sorted(classify.get(n, ('rs', None))[1] for n in used if classify.get(n, ('rs', None))[0] == 'module')
    if need_models:
        adds.append('from app.api.models import (\n    ' + ',\n    '.join(need_models) + ',\n)')
    if need_common:
        adds.append('from app.api.common import (\n    ' + ',\n    '.join(need_common) + ',\n)')
    if any(classify.get(n, ('rs', None))[0] == 'state' for n in used):
        adds.append('from app.api import state')
    if any(classify.get(n, ('rs', None))[0] == 'dbm' for n in used):
        adds.append('import db_manager')
    if any(classify.get(n, ('rs', None))[0] == 'rs' for n in used):
        adds.append('import reply_server  # noqa: F401  (late-bound seam: runtime rebinds stay visible)')
    for mod, names in sorted(need_origin.items()):
        adds.append(f"from {mod} import {', '.join(sorted(set(names)))}")
    for imp in need_modules:
        adds.append(imp)

    if adds:
        # insert after the last import at top of file
        t2 = ast.parse(src2)
        last_imp = max((n.end_lineno for n in t2.body if isinstance(n, (ast.Import, ast.ImportFrom))), default=0)
        lines = src2.splitlines(keepends=True)
        lines.insert(last_imp, '\n' + '\n'.join(adds) + '\n')
        src2 = ''.join(lines)

    io.open(path, 'w', encoding='utf-8', newline='').write(src2)
    return dict(used=sorted(used), bare_ctx=bare_ctx, kinds=sorted({k for _, _, _, k, _ in spans}))


def run(classify):
    routers = sorted((ROOT / 'app' / 'api' / 'routers').glob('*.py'))
    for p in routers:
        if p.name == '__init__.py':
            continue
        info = rewrite_router(p, classify)
        if info:
            print(f"{p.name}: {len(info['used'])} names, kinds={info['kinds']}, bare_ctx={info['bare_ctx']}")

    # reply_server wiring: create_X_router(ctx=ctx) -> create_X_router()
    rs = ROOT / 'reply_server.py'
    src = io.open(rs, encoding='utf-8').read()
    src2 = re.sub(r"create_(\w+_router)\(ctx=ctx\)", r"create_\1()", src)
    src2 = re.sub(r"\n\s*ctx=ctx,", "", src2)
    n = len(re.findall(r"create_\w+_router\(\)", src2)) - len(re.findall(r"create_\w+_router\(\)", src))
    io.open(rs, 'w', encoding='utf-8', newline='').write(src2)
    print(f"reply_server wiring: {n} create_*_router calls updated")

    # test seam migration: manual_cookie_import_sessions rebind -> state module
    t = ROOT / 'tests' / 'smoke' / 'test_reply_server_manual_cookie_import_flow.py'
    s = io.open(t, encoding='utf-8').read()
    s2 = s.replace("reply_server.manual_cookie_import_sessions",
                   "state.manual_cookie_import_sessions")
    if s2 != s:
        if 'from app.api import state' not in s2:
            s2 = s2.replace('import reply_server', 'import reply_server\n\nfrom app.api import state', 1)
        io.open(t, 'w', encoding='utf-8', newline='').write(s2)
        print("test seam migrated: manual_cookie_import_sessions -> app.api.state")
