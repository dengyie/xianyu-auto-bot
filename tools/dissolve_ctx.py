#!/usr/bin/env python3
"""Dissolve the P1-era ApiContext proxy (app/api/state.py).

Phase A: move shared symbols out of reply_server into real homes:
  - app/api/models.py  (Pydantic request/response models)
  - app/api/common.py  (pure helpers + constants; extended)
  - app/api/state.py   (real shared state containers; proxy replaced)
reply_server imports them back so its own code and test seams keep working.

Phase B: rewrite routers: ctx.X -> direct references; drop ctx factory param.
  - models/common names -> bare names (import added)
  - state containers    -> state.X attribute access (keeps rebind seams)
  - db_manager          -> db_manager.db_manager (package-attr late binding)
  - origin-imported     -> import from origin module
  - everything else     -> reply_server.X (late binding, stays behind)

Phase C: reply_server include_router wiring loses ctx=ctx.
Phase D: the one test that rebinds manual_cookie_import_sessions moves to the
state-module seam.
"""
import ast
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

RS = ROOT / 'reply_server.py'
MODELS_P = ROOT / 'app' / 'api' / 'models.py'
COMMON_P = ROOT / 'app' / 'api' / 'common.py'
STATE_P = ROOT / 'app' / 'api' / 'state.py'

MODELS = """AIConfigPreset AIReplySettings ActionEvent AddMembersRequest AutoCommentBatchRateRequest AutoCommentUpdate
AutoConfirmUpdate BatchDeleteRequest ChatSendRequest ClientErrorRequest CommentTemplateCreate CommentTemplateUpdate
CookieAccountInfo CookieIn CookieStatusIn CopyKeywordsRequest CreateGroupRequest DefaultReplyIn ItemDetailUpdate
ItemSearchMultipleRequest ItemSearchRequest ItemToDelete KeywordIn KeywordWithItemIdIn LoginInfoSettingUpdate
ManualCookieImportRequest MessageNotificationIn NotificationChannelIn NotificationChannelUpdate NotificationTemplateIn
OrderHistorySyncRequest OrderRecoverRequest PauseDurationUpdate PersonalBlacklistBatchDeleteRequest
PersonalBlacklistCreateRequest PersonalBlacklistToggleRequest ProductBatchPublishRequest ProductMaterialRequest
ProductMaterialUpdateRequest ProductSinglePublishRequest ProxyConfig QRLoginSubmitCookiesRequest QRLoginSubmitUrlRequest
RegistrationSettingUpdate RemarkUpdate RequestModel ResponseData ResponseModel SaveItemKeywordsRequest
SendMessageRequest SendMessageResponse SystemSettingIn TestNotificationIn""".split()

COMMON_FUNCS = """_dedupe_int_list _dedupe_str_list _parse_enabled_flag _parse_form_bool _parse_random_delay
_parse_run_hour _model_to_dict _find_first_nested_value _extract_merchant_rate_item_meta _extract_merchant_rate_order_id
_normalize_task_log_limit _normalize_task_log_offset _normalize_task_log_row _task_log_created_at_sort_value
_estimate_base64_bytes _sanitize_material_images _validate_publish_images _parse_optional_non_negative_float
_normalize_product_publish_data _is_sensitive_admin_data_field _redact_admin_table_data
_is_password_login_verification_timeout_message _is_timed_out_verification_risk_log _normalize_history_optional_text
_empty_slider_session_stats _evaluate_screenshot_freshness _build_face_verification_screenshot_info
_validate_system_setting_value""".split()

COMMON_CONSTS = """TASK_LOG_TYPE_LABELS PRODUCT_PUBLISH_DELIVERY_CHOICES PRODUCT_PUBLISH_MAX_BASE64_CHARS
PRODUCT_PUBLISH_MAX_IMAGES PRODUCT_PUBLISH_MAX_IMAGE_BYTES ORDER_SALES_TIME_SQL CAPTCHA_EXPIRE_SECONDS
NIGHT_MODE_SYSTEM_SETTING_KEYS PASSWORD_LOGIN_TERMINAL_STATUSES _SCREENSHOT_STALE_GAP_SECONDS""".split()

STATE_NAMES = """SESSION_TOKENS DOWNLOAD_TOKENS TOKEN_EXPIRE_TIME session_service qr_check_locks qr_check_processed
login_ip_tracker login_user_tracker ip_blacklist username_rate_tracker captcha_storage order_history_sync_jobs
order_history_sync_tasks password_login_sessions manual_cookie_import_sessions qr_lite_sessions""".split()
# BRUTE_FORCE_CONFIG handled via STATE too; static_dir -> STATIC_DIR rewritten.

# ctx-name -> origin module (mirrors reply_server's own imports)
ORIGINS = {
    'ForbiddenOrder': 'app.application.orders.delivery',
    'ManualDeliveryContextLoader': 'app.application.orders.delivery',
    'MissingOrderAccount': 'app.application.orders.delivery',
    'OrderNotFound': 'app.application.orders.delivery',
    'SUPPORTED_NOTIFICATION_TEMPLATE_TYPES': 'utils.notification_dispatcher',
    'ai_reply_engine': 'ai_reply_engine',
    'blacklist_service': 'utils.blacklist_service',
    'chat_event_hub': 'chat_event_hub',
    'get_client_ip': 'utils.client_ip',
    'get_file_log_collector': 'file_log_collector',
    'get_local_now': 'utils.time_utils',
    'get_updater': 'auto_updater',
    'image_manager': 'utils.image_utils',
    'local_date_to_utc_end_exclusive': 'utils.time_utils',
    'local_date_to_utc_start': 'utils.time_utils',
    'order_event_hub': 'order_event_hub',
    'publish_chat_message': 'chat_event_hub',
    'publish_order_update_event': 'order_event_hub',
    'qr_login_manager': 'utils.qr_login',
}

MODULE_IMPORTS = {  # ctx-name -> import line
    'pd': 'import pandas as pd',
    'uuid': 'import uuid',
    'queue': 'import queue',
    'Path': 'from pathlib import Path',
    'cookie_manager': 'import cookie_manager',
}


def read(p):
    return io.open(p, encoding='utf-8').read()


def byte_table(src: str):
    lines = src.splitlines(keepends=True)
    starts, off = [], 0
    for ln in lines:
        starts.append(off)
        off += len(ln.encode('utf-8'))
    return starts, len(src.encode('utf-8'))


def node_span(node, starts, bsrc):
    s = starts[node.lineno - 1] + node.col_offset
    e = starts[node.end_lineno - 1] + node.end_col_offset
    return s, e


def collect_imports_needed(segment_src: str, rs_origin: dict, rs_imports: dict) -> list:
    """Names loaded in segment -> import lines, using reply_server's own import map."""
    tree = ast.parse(segment_src)
    needed = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
            needed.add(n.id)
        elif isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name):
            needed.add(n.value.id)
    out = []
    for name in sorted(needed):
        if name in rs_origin:
            mod, attr = rs_origin[name]
            if mod == '#import':
                out.append(f"import {attr}")
            else:
                out.append(f"from {mod} import {attr}")
        elif name in rs_imports:
            out.append(rs_imports[name])
    return sorted(set(out))


def main():
    src = read(RS)
    bsrc = src.encode('utf-8')
    starts, blen = byte_table(src)
    tree = ast.parse(src)

    # ---- index reply_server top-level ----
    defs = {}
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defs[n.name] = n
        elif isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name):
                    defs[t.id] = n
        elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
            defs[n.target.id] = n

    rs_origin, rs_imports = {}, {}
    for n in tree.body:
        if isinstance(n, ast.ImportFrom) and n.module:
            for a in n.names:
                rs_origin[a.asname or a.name] = (n.module, a.name)
        elif isinstance(n, ast.Import):
            for a in n.names:
                nm = a.asname or a.name.split('.')[0]
                rs_origin[nm] = ('#import', a.name)
                rs_imports[nm] = f"import {a.name}" + (f" as {a.asname}" if a.asname else "")
    # typing + fastapi/pydantic names for moved segments
    for n in tree.body:
        if isinstance(n, ast.ImportFrom) and n.module in ('typing', 'pydantic', 'fastapi', 'datetime', 'starlette.requests') or (
            isinstance(n, ast.ImportFrom) and n.module and n.module.startswith(('fastapi', 'pydantic', 'starlette'))
        ):
            for a in n.names:
                if a.name != '*':
                    rs_imports.setdefault(a.asname or a.name, f"from {n.module} import {a.name}")

    # ---- gather segments to move ----
    moves = {'models': [], 'common': [], 'state': []}
    for name in MODELS:
        node = defs[name]
        assert isinstance(node, ast.ClassDef), name
        moves['models'].append((name, node))
    for name in COMMON_FUNCS + COMMON_CONSTS:
        node = defs[name]
        assert not isinstance(node, ast.ClassDef), name
        moves['common'].append((name, node))
    for name in STATE_NAMES:
        node = defs[name]
        assert not isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)), name
        moves['state'].append((name, node))
    # BRUTE_FORCE_CONFIG + static_dir
    moves['state'].append(('BRUTE_FORCE_CONFIG', defs['BRUTE_FORCE_CONFIG']))
    moves['state'].append(('static_dir', defs['static_dir']))

    # ---- build replacement spans for reply_server ----
    spans = []  # (start,end,replacement)
    seg_src = {}
    for kind, pairs in moves.items():
        for name, node in pairs:
            s, e = node_span(node, starts, bsrc)
            seg = bsrc[s:e].decode('utf-8')
            if name == 'static_dir':
                seg = seg.replace("os.path.dirname(__file__)",
                                  "os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))")
                seg = seg.replace('static_dir', 'STATIC_DIR', 1)
            seg_src[(kind, name)] = seg
            spans.append((s, e, kind, name))

    # remove in reverse document order
    spans.sort(key=lambda x: -x[0])
    for s, e, kind, name in spans:
        bsrc = bsrc[:s] + bsrc[e:]
    src2 = bsrc.decode('utf-8')

    # ---- write models.py (topologically sorted: cross-referencing models first) ----
    model_nodes = {name: ast.parse(seg_src[('models', name)]).body[0] for name in MODELS}
    order, seen = [], set()

    def visit(name, stack):
        if name in seen:
            return
        assert name not in stack, f"cycle: {stack + [name]}"
        for nn in ast.walk(model_nodes[name]):
            if isinstance(nn, ast.Name) and isinstance(nn.ctx, ast.Load) and nn.id in model_nodes and nn.id != name:
                visit(nn.id, stack + [name])
        seen.add(name)
        order.append(name)

    for nm in MODELS:
        visit(nm, [])
    model_blocks = [seg_src[('models', n)] for n in order]
    mimports = set()
    for n in MODELS:
        for ln in collect_imports_needed(seg_src[('models', n)], rs_origin, rs_imports):
            mimports.add(ln)
    models_header = '"""Shared Pydantic request/response models (extracted from reply_server, P1 closeout)."""\n\n'
    models_body = models_header + '\n\n'.join(sorted(mimports)) + '\n\n\n' + '\n\n\n'.join(model_blocks) + '\n'
    MODELS_P.write_text(models_body, encoding='utf-8')

    # ---- extend common.py ----
    common_blocks = [seg_src[('common', n)] for n in COMMON_FUNCS + COMMON_CONSTS]
    cimports = set()
    for n in COMMON_FUNCS + COMMON_CONSTS:
        for ln in collect_imports_needed(seg_src[('common', n)], rs_origin, rs_imports):
            cimports.add(ln)
    csrc = read(COMMON_P)
    csrc += '\n\n# ── P1 closeout: pure helpers + constants extracted from reply_server ──\n'
    csrc += '\n'.join(sorted(cimports)) + '\n\n\n' + '\n\n\n'.join(common_blocks) + '\n'
    COMMON_P.write_text(csrc, encoding='utf-8')

    # ---- rewrite state.py ----
    state_blocks = [seg_src[('state', n)] for n in STATE_NAMES + ['BRUTE_FORCE_CONFIG', 'static_dir']]
    simports = set()
    for n in STATE_NAMES + ['BRUTE_FORCE_CONFIG', 'static_dir']:
        for ln in collect_imports_needed(seg_src[('state', n)], rs_origin, rs_imports):
            if 'SessionService' in ln or 'sessions' in ln:
                continue
            simports.add(ln)
    state_body = '''"""Shared API-layer state (P1 closeout: real home, was the ApiContext proxy).

 reply_server imports these names back at module scope, so runtime rebinds on
 either module attribute surface stay visible to routers that read `state.X`
 at call time.
"""

import asyncio
import os
from collections import defaultdict
from typing import Any, Dict

from app.application.auth.sessions import SessionService

''' + '\n'.join(sorted(simports)) + '\n\n\n' + '\n\n\n'.join(state_blocks) + '\n'
    STATE_P.write_text(state_body, encoding='utf-8')

    # ---- import-backs into reply_server ----
    back = []
    back.append('from app.api.models import (\n    ' + ',\n    '.join(MODELS) + ',\n)')
    back.append('from app.api.common import (\n    ' + ',\n    '.join(sorted(set(COMMON_FUNCS + COMMON_CONSTS))) + ',\n)')
    back.append('from app.api.state import (  # noqa: F401  (shared singletons re-exported for legacy seams)\n    '
                + ',\n    '.join(STATE_NAMES + ['BRUTE_FORCE_CONFIG', 'STATIC_DIR as static_dir']) + ',\n)')
    ins = '\n\n' + '\n\n'.join(back) + '\n'
    # re-parse: removals shifted line numbers; insert before first executable stmt
    tree2 = ast.parse(src2)
    first_exec = None
    for n in tree2.body:
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            continue
        if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str):
            continue
        first_exec = n
        break
    assert first_exec is not None
    starts2, _ = byte_table(src2)
    bsrc2 = src2.encode('utf-8')
    ins_at = starts2[first_exec.lineno - 1]
    src2 = (bsrc2[:ins_at] + ins.strip('\n').encode('utf-8') + b'\n\n' + bsrc2[ins_at:]).decode('utf-8')
    # drop reply_server's own proxy import line
    src2 = src2.replace('from app.api.state import ctx\n', '', 1)
    RS.write_text(src2, encoding='utf-8')
    print(f"reply_server: removed {len(spans)} defs, import-backs placed before first-exec line {first_exec.lineno}")

    # ---- Phase B: routers ----
    classify = {}
    for n in MODELS:
        classify[n] = ('models', n)
    for n in COMMON_FUNCS + COMMON_CONSTS:
        classify[n] = ('common', n)
    for n in STATE_NAMES:
        classify[n] = ('state', n)
    classify['BRUTE_FORCE_CONFIG'] = ('state', 'BRUTE_FORCE_CONFIG')
    classify['static_dir'] = ('state', 'STATIC_DIR')
    for n, mod in ORIGINS.items():
        classify[n] = ('origin', mod)
    for n, imp in MODULE_IMPORTS.items():
        classify[n] = ('module', imp)
    classify['db_manager'] = ('dbm', None)

    import routers_rewrite
    routers_rewrite.run(classify)


if __name__ == '__main__':
    main()
