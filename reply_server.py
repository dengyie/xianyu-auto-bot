from fastapi import FastAPI, HTTPException, Depends, status, UploadFile, File, Form, Request, Header, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, Response, StreamingResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from typing import List, Tuple, Optional, Dict, Any, Callable, Awaitable
from pathlib import Path
import urllib.parse
from urllib.parse import unquote
from urllib import request as urllib_request, error as urllib_error
import hashlib
import secrets
import time
import json
import os
import re
import uuid
import base64
import inspect
from datetime import datetime, timedelta
import uvicorn
import pandas as pd
import io
import asyncio
import concurrent.futures
import queue
from collections import defaultdict
from contextlib import asynccontextmanager, suppress

import cookie_manager
from db_manager import db_manager
from config import RISK_CONTROL
from file_log_collector import setup_file_logging, get_file_log_collector
from ai_reply_engine import ai_reply_engine
from utils.qr_login import qr_login_manager
from utils.qr_login_lite import qrcode_login_lite
from utils.xianyu_utils import trans_cookies
from utils.image_utils import image_manager
from utils.audit_logger import record_audit_event, status_from_http_status_code
from utils.blacklist_service import blacklist_service
from utils.auto_rate_task import auto_rate_task_loop
from utils.client_ip import get_client_ip
from utils.time_utils import (
    LOCAL_TIMEZONE,
    get_local_now,
    local_date_to_utc_end_exclusive,
    local_date_to_utc_start,
    parse_db_timestamp,
    utc_timestamp_to_local_date_string,
    utc_timestamp_to_local_datetime,
)
from utils.notification_dispatcher import (
    build_face_verify_notification,
    SUPPORTED_NOTIFICATION_TEMPLATE_TYPES,
    dispatch_account_notifications_sync,
    render_notification_template,
    resolve_verification_type_label,
)
from chat_event_hub import chat_event_hub, publish_chat_message
from order_event_hub import order_event_hub, publish_order_update_event
from app.api.routers.auth import create_auth_router
from app.application.auth.sessions import SessionService
from app.api.routers.cookies import create_cookies_router
from app.api.routers.login import create_login_router
from app.api.routers.settings import create_settings_router
from app.api.routers.notifications import create_notifications_router
from app.api.routers.keywords import create_keywords_router
from app.api.routers.accountlogin import create_account_login_router
from app.api.routers.trading import create_trading_router
from app.api.routers.adminops import create_admin_ops_router
from app.api.routers.orderschat import create_orders_chat_router
from app.api.state import ctx
from app.api.common import (
    is_sales_eligible_order_status,
    format_sse_event,
    mask_cookie_value,
    mask_secret_value,
    mask_sensitive_text,
    normalize_order_status_value,
    parse_order_amount_value,
    safe_client_error,
    ORDER_STATUS_ALIASES,
    SALES_ELIGIBLE_ORDER_STATUSES,
    SENSITIVE_FIELD_PATTERNS,
)
from app.application.orders.delivery import (
    ForbiddenOrder,
    ManualDeliveryContextLoader,
    MissingOrderAccount,
    OrderNotFound,
)
from app.domain.accounts.ownership import (
    AccountForbidden,
    AccountOwnershipPolicy,
    MissingAccountId,
)

from loguru import logger

PROJECT_ROOT = Path(__file__).resolve().parent


def ensure_runtime_directories(root: Path) -> None:
    """Create runtime-owned directories relative to the project, never the CWD."""
    for directory in (
        root / "logs",
        root / "data",
        root / "backups",
        root / "static" / "uploads" / "images",
    ):
        directory.mkdir(parents=True, exist_ok=True)


ensure_runtime_directories(PROJECT_ROOT)

# ==================== ?????? ====================
import logging as _logging
_analytics_fmt = "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level} | {extra[user]} | {extra[action]} | {message}"
# Remove loguru-style format, use standard logging for analytics
import logging as _analytics_logging
_analytics_logger = _analytics_logging.getLogger("analytics")
_analytics_logger.setLevel(_analytics_logging.INFO)
_analytics_logger.propagate = False
_afh = _analytics_logging.FileHandler(PROJECT_ROOT / "logs" / "analytics.log", encoding="utf-8")
_afh.setFormatter(_analytics_logging.Formatter("%(asctime)s | %(levelname)s | %(user)s | %(action)s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
_analytics_logger.addHandler(_afh)

class ActionEvent:
    LOGIN = "login"
    LOGOUT = "logout"
    FILE_UPLOAD = "file_upload"
    FILE_DOWNLOAD = "file_download"
    FILE_DELETE = "file_delete"
    FILE_EDIT = "file_edit"
    FILE_LIST = "file_list"
    GROUP_CREATE = "group_create"
    GROUP_DELETE = "group_delete"
    GROUP_ADD_MEMBER = "group_add_member"
    GROUP_REMOVE_MEMBER = "group_remove_member"

def track(user="system", action="", target="-", result="success", detail=""):
    extra = {"user": user, "action": action}
    msg = f"{target} | {result} | {detail}"
    _analytics_logger.info(msg, extra=extra)
    for h in _analytics_logger.handlers:
        h.flush()


class _LegacySlidexConfig:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def _load_slider_runtime():
    try:
        from slidex.stealth import (
            XianyuSliderStealth,
            probe_cookie_verification_from_cookie,
        )
        from slidex import SlidexConfig
        from slidex._concurrency import concurrency_manager
        return XianyuSliderStealth, probe_cookie_verification_from_cookie, SlidexConfig, concurrency_manager
    except ModuleNotFoundError as exc:
        if exc.name != "slidex":
            raise
        from utils.xianyu_slider_stealth import (
            XianyuSliderStealth,
            concurrency_manager,
            probe_cookie_verification_from_cookie,
        )
        return XianyuSliderStealth, probe_cookie_verification_from_cookie, _LegacySlidexConfig, concurrency_manager


def _create_slider_instance(slider_cls, **kwargs):
    try:
        return slider_cls(**kwargs)
    except TypeError:
        kwargs.pop("slidex_config", None)
        return slider_cls(**kwargs)


# 刮刮乐远程控制路由
try:
    from api_captcha_remote import router as captcha_router
    CAPTCHA_ROUTER_AVAILABLE = True
except ImportError:
    logger.warning("⚠️ api_captcha_remote 未找到，刮刮乐远程控制功能不可用")
    CAPTCHA_ROUTER_AVAILABLE = False

# 关键字文件路径
KEYWORDS_FILE = Path(__file__).parent / "回复关键字.txt"

# 简单的用户认证配置
ADMIN_USERNAME = "admin"
# DEFAULT_ADMIN_PASSWORD removed - admin password now randomly generated by db_manager (package) on first init  # 系统初始化时的默认密码
SESSION_TOKENS = {}  # 存储会话token: {token: {'user_id': int, 'username': str, 'timestamp': float}}

DOWNLOAD_TOKENS = {}  # 下载一次性token: {token_str: {user_id, file_id, exp}}
TOKEN_EXPIRE_TIME = 24 * 60 * 60  # token过期时间：24小时
session_service = SessionService(SESSION_TOKENS, TOKEN_EXPIRE_TIME)

# HTTP Bearer认证
security = HTTPBearer(auto_error=False)

# 扫码登录检查锁 - 防止并发处理同一个session
qr_check_locks = defaultdict(lambda: asyncio.Lock())
qr_check_processed = {}  # 记录已处理的session: {session_id: {'processed': bool, 'timestamp': float}}

# ========================= 防暴力破解配置 =========================
# IP 登录失败记录: {ip: {'attempts': int, 'first_attempt': float, 'last_attempt': float, 'blocked_until': float}}
login_ip_tracker = {}
# 用户名登录失败记录: {username: {'attempts': int, 'first_attempt': float, 'last_attempt': float, 'locked_until': float}}
login_user_tracker = {}
# 永久黑名单IP列表
ip_blacklist = set()
username_rate_tracker: dict = {}

# 验证码存储: {captcha_id: {'code': str, 'created_at': float, 'ip': str}}
captcha_storage = {}
CAPTCHA_EXPIRE_SECONDS = 300  # 验证码5分钟过期
CAPTCHA_REQUIRE_AFTER_FAILURES = 2  # 失败2次后要求验证码

# Codex识别
def is_codex_browser(user_agent: str) -> bool:
    if not user_agent:
        return False
    return 'Codex' in user_agent

# 防暴力破解参数
BRUTE_FORCE_CONFIG = {
    'ip_max_attempts': 5,           # 单IP最大尝试次数
    'ip_window_seconds': 300,       # IP计数窗口时间（5分钟）
    'ip_block_seconds': 1800,       # IP封禁时间（30分钟）
    'user_max_attempts': 10,        # 单用户名最大尝试次数
    'user_window_seconds': 600,     # 用户名计数窗口时间（10分钟）
    'user_lock_seconds': 3600,      # 用户名锁定时间（1小时）
    'auto_blacklist_threshold': 20, # 自动加入永久黑名单的失败次数阈值
    'response_delay_base': 1,       # 基础响应延迟（秒）
    'response_delay_multiplier': 0.5,  # 每次失败增加的延迟（秒）
    'max_response_delay': 10,       # 最大响应延迟（秒）
    'captcha_require_failures': 2,  # 失败多少次后需要验证码
    'username_rate_per_minute': 5,
    'username_rate_window': 60,
}


ORDER_SALES_TIME_SQL = "COALESCE(NULLIF(platform_paid_at, ''), NULLIF(platform_created_at, ''), created_at)"

ORDER_HISTORY_SYNC_JOB_RETENTION_SECONDS = 3600
order_history_sync_jobs: Dict[str, Dict[str, Any]] = {}
order_history_sync_tasks: Dict[str, asyncio.Task] = {}
ANNOUNCEMENT_CACHE_TTL_SECONDS = 300
announcement_cache: Dict[str, Any] = {
    'expires_at': 0.0,
    'current': None,
    'history': [],
    'last_success_current': None,
    'last_success_history': [],
    'has_remote_success': False,
}


def _get_announcement_remote_url() -> str:
    configured_url = str(os.getenv('DASHBOARD_ANNOUNCEMENT_URL') or '').strip()
    if configured_url:
        return configured_url

    owner = str(os.getenv('UPDATE_GITHUB_OWNER') or 'dengyie').strip() or 'dengyie'
    repo = str(os.getenv('UPDATE_GITHUB_REPO') or 'xianyu-auto-bot').strip() or 'xianyu-auto-bot'
    branch = str(os.getenv('DASHBOARD_ANNOUNCEMENT_BRANCH') or 'main').strip() or 'main'
    file_path = str(os.getenv('DASHBOARD_ANNOUNCEMENT_FILE') or 'announcement.json').strip().lstrip('/')
    return f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{file_path}"


def _get_announcement_local_path() -> Path:
    file_path = str(os.getenv('DASHBOARD_ANNOUNCEMENT_FILE') or 'announcement.json').strip().lstrip('/')
    return Path(__file__).parent / file_path


def _parse_announcement_datetime(value: Any) -> Optional[datetime]:
    raw_value = str(value or '').strip()
    if not raw_value:
        return None

    normalized_value = raw_value.replace('Z', '+00:00') if raw_value.endswith('Z') else raw_value
    try:
        parsed = datetime.fromisoformat(normalized_value)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=LOCAL_TIMEZONE)
    return parsed.astimezone(LOCAL_TIMEZONE)


def _build_announcement_id(payload: Dict[str, Any]) -> str:
    raw_id = str(payload.get('id') or '').strip()
    if raw_id:
        return raw_id

    stable_source = json.dumps(
        {
            'level': str(payload.get('level') or '').strip(),
            'title': str(payload.get('title') or '').strip(),
            'message': str(payload.get('message') or '').strip(),
            'action_text': str(payload.get('action_text') or '').strip(),
            'action_type': str(payload.get('action_type') or '').strip(),
            'action_url': str(payload.get('action_url') or '').strip(),
            'dismissible': payload.get('dismissible', True),
            'start_at': str(payload.get('start_at') or '').strip(),
            'end_at': str(payload.get('end_at') or '').strip(),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return f"announcement-{hashlib.sha1(stable_source.encode('utf-8')).hexdigest()[:12]}"


def _coerce_announcement_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)

    normalized = str(value).strip().lower()
    if normalized in {'1', 'true', 'yes', 'y', 'on', 'enabled'}:
        return True
    if normalized in {'0', 'false', 'no', 'n', 'off', 'disabled', ''}:
        return False
    return default


def _empty_dashboard_announcement_snapshot() -> Dict[str, Any]:
    return {
        'current': None,
        'history': [],
    }


def _normalize_dashboard_announcement_entry(payload: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(payload, dict):
        return None

    enabled = _coerce_announcement_bool(payload.get('enabled'), default=False)
    start_at = _parse_announcement_datetime(payload.get('start_at'))
    end_at = _parse_announcement_datetime(payload.get('end_at'))
    title = str(payload.get('title') or '').strip()
    message = str(payload.get('message') or '').strip()
    summary = str(payload.get('summary') or payload.get('brief') or payload.get('short_message') or '').strip()
    if not title and not message and not summary:
        return None

    level = str(payload.get('level') or 'info').strip().lower()
    if level not in {'info', 'success', 'warning', 'danger'}:
        level = 'info'

    action_type = str(payload.get('action_type') or '').strip().lower()
    if action_type not in {'', 'url', 'changelog', 'update'}:
        action_type = ''

    action_url = str(payload.get('action_url') or '').strip()
    if action_type == 'url' and not action_url:
        action_type = ''

    action_text = str(payload.get('action_text') or '').strip()
    if action_type and not action_text:
        action_text = '查看详情' if action_type == 'url' else '立即查看'
    if not action_type:
        action_text = ''

    published_at = _parse_announcement_datetime(payload.get('published_at'))
    now = get_local_now()
    if not enabled:
        status = 'disabled'
    elif start_at and now < start_at:
        status = 'scheduled'
    elif end_at and now > end_at:
        status = 'expired'
    else:
        status = 'active'

    return {
        'id': _build_announcement_id(payload),
        'enabled': enabled,
        'status': status,
        'level': level,
        'title': title,
        'summary': summary,
        'message': message,
        'action_text': action_text,
        'action_type': action_type,
        'action_url': action_url,
        'dismissible': _coerce_announcement_bool(payload.get('dismissible'), default=True),
        'published_at': published_at.isoformat() if published_at else '',
        'start_at': start_at.isoformat() if start_at else '',
        'end_at': end_at.isoformat() if end_at else '',
    }


def _normalize_dashboard_announcement_snapshot(payload: Any) -> Optional[Dict[str, Any]]:
    announcements_payload = payload if isinstance(payload, list) else payload.get('announcements') if isinstance(payload, dict) else None
    if not isinstance(announcements_payload, list):
        return None

    history: List[Dict[str, Any]] = []
    for item in announcements_payload:
        normalized_item = _normalize_dashboard_announcement_entry(item)
        if normalized_item:
            history.append(normalized_item)

    history.sort(
        key=lambda item: item.get('published_at') or item.get('start_at') or item.get('end_at') or '',
        reverse=True,
    )

    current_id = ''
    for item in history:
        if item.get('status') == 'active':
            current_id = str(item.get('id') or '').strip()
            break

    normalized_history: List[Dict[str, Any]] = []
    current_announcement: Optional[Dict[str, Any]] = None
    for item in history:
        normalized_item = dict(item)
        normalized_item['is_current'] = bool(current_id and normalized_item.get('id') == current_id)
        normalized_history.append(normalized_item)
        if normalized_item['is_current'] and current_announcement is None:
            current_announcement = dict(normalized_item)

    return {
        'current': current_announcement,
        'history': normalized_history,
    }


def _try_load_dashboard_announcement_snapshot_from_remote() -> Tuple[bool, Optional[Dict[str, Any]]]:
    remote_url = _get_announcement_remote_url()
    try:
        request = urllib_request.Request(
            remote_url,
            headers={
                'User-Agent': 'XianyuDashboardAnnouncement/1.0',
                'Accept': 'application/json',
            }
        )
        with urllib_request.urlopen(request, timeout=8) as response:
            status_code = getattr(response, 'status', 200)
            if status_code != 200:
                logger.warning(f"获取远端公告失败: http_status={status_code}, url={remote_url}")
                return False, None
            raw_content = response.read().decode('utf-8')
    except urllib_error.HTTPError as exc:
        logger.warning(f"获取远端公告失败: http_status={exc.code}, url={remote_url}")
        return False, None
    except Exception as exc:
        logger.warning(f"获取远端公告异常: url={remote_url}, error={mask_sensitive_text(exc)}")
        return False, None

    try:
        payload = json.loads(raw_content)
    except json.JSONDecodeError as exc:
        logger.warning(f"解析远端公告失败: url={remote_url}, error={exc}")
        return False, None

    snapshot = _normalize_dashboard_announcement_snapshot(payload)
    if snapshot is None:
        logger.warning(f"远端公告格式无效: url={remote_url}")
        return False, None

    return True, snapshot


def _try_load_dashboard_announcement_snapshot_from_local() -> Optional[Dict[str, Any]]:
    local_path = _get_announcement_local_path()
    if not local_path.exists():
        return None

    try:
        payload = json.loads(local_path.read_text(encoding='utf-8'))
    except Exception as exc:
        logger.warning(f"读取本地公告文件失败: path={local_path}, error={mask_sensitive_text(exc)}")
        return None

    snapshot = _normalize_dashboard_announcement_snapshot(payload)
    if snapshot is None:
        logger.warning(f"本地公告格式无效: path={local_path}")
        return None

    return snapshot


def _get_dashboard_announcement_payload(force_refresh: bool = False) -> Dict[str, Any]:
    now_ts = time.time()
    if not force_refresh and announcement_cache.get('expires_at', 0) > now_ts:
        return {
            'current': announcement_cache.get('current'),
            'history': list(announcement_cache.get('history') or []),
        }

    loaded_remote, remote_snapshot = _try_load_dashboard_announcement_snapshot_from_remote()
    if loaded_remote and remote_snapshot is not None:
        announcement_cache.update({
            'expires_at': now_ts + ANNOUNCEMENT_CACHE_TTL_SECONDS,
            'current': remote_snapshot.get('current'),
            'history': list(remote_snapshot.get('history') or []),
            'last_success_current': remote_snapshot.get('current'),
            'last_success_history': list(remote_snapshot.get('history') or []),
            'has_remote_success': True,
        })
        return remote_snapshot

    if announcement_cache.get('has_remote_success'):
        snapshot = {
            'current': announcement_cache.get('last_success_current'),
            'history': list(announcement_cache.get('last_success_history') or []),
        }
    else:
        snapshot = _try_load_dashboard_announcement_snapshot_from_local() or _empty_dashboard_announcement_snapshot()

    announcement_cache.update({
        'expires_at': now_ts + ANNOUNCEMENT_CACHE_TTL_SECONDS,
        'current': snapshot.get('current'),
        'history': list(snapshot.get('history') or []),
    })
    return snapshot


# 账号密码登录会话管理
password_login_sessions = {}  # {session_id: {'account_id': str, 'account': str, 'show_browser': bool, 'status': str, 'verification_url': str, 'qr_code_url': str, 'slider_instance': object, 'task': asyncio.Task, 'timestamp': float}}
password_login_locks = defaultdict(lambda: asyncio.Lock())
manual_cookie_import_sessions = {}  # {session_id: {'account_id': str, 'status': str, 'verification_url': str, 'screenshot_path': str, 'slider_instance': object, 'task': asyncio.Task, 'timestamp': float}}
manual_cookie_import_locks = defaultdict(lambda: asyncio.Lock())
PASSWORD_LOGIN_TERMINAL_STATUSES = {'success', 'failed', 'cancelled'}

# ── 轻量扫码登录(qr_login_lite)会话表 ───────────────────────────
# value: {state, qr_data_url, error_message, account_info, started_at, finished, user_id}
# state: pending | waiting | success | error | expired
qr_lite_sessions: Dict[str, Dict[str, Any]] = {}
QR_LITE_SESSION_TTL = 600  # 10 分钟未完结即清理

# 不再需要单独的密码初始化，由数据库初始化时处理


QR_CHECK_RECORD_TTL = 3600  # 扫码检查记录存活上限（秒）
# 保护清理逻辑的互斥锁，避免并发 check 请求同时遍历/删除字典
_qr_check_cleanup_lock = asyncio.Lock()


async def cleanup_qr_check_records():
    """清理过期的扫码检查记录。

    使用全局锁串行化：防止并发 check 请求同时遍历+删除字典
    (RuntimeError: dictionary changed size during iteration)，也防止
    两个请求同时 del 同一个 session 的 lock 后又各自新建出并发不互斥的锁。
    跳过 processing=True 的记录——后台 Cookie 处理任务仍持有该 session 的锁。
    """
    current_time = time.time()

    async with _qr_check_cleanup_lock:
        # 先快照再判定，避免边遍历边删
        snapshot = list(qr_check_processed.items())
        expired_sessions = []
        for session_id, record in snapshot:
            if record.get('processing'):
                continue
            if current_time - record.get('timestamp', 0) > QR_CHECK_RECORD_TTL:
                expired_sessions.append(session_id)

        for session_id in expired_sessions:
            qr_check_processed.pop(session_id, None)
            # defaultdict 访问会副作用创建新 lock，所以只在确已存在时删
            if session_id in qr_check_locks:
                del qr_check_locks[session_id]


async def _qr_check_cleanup_loop(interval: int = 300):
    """后台定期清理扫码检查记录，保证即使客户端停止轮询也能回收内存。

    与请求路径触发互补：请求路径只在有新 check 请求时清理；客户端登录
    完成或放弃后再不轮询时，记录会一直滞留直到下次轮询——本循环兜底。
    """
    while True:
        try:
            await cleanup_qr_check_records()
        except Exception as e:  # noqa: BLE001 守护循环绝不能因单次异常退出
            logger.warning(f"扫码检查记录后台清理异常: {e}")
        await asyncio.sleep(interval)


def _qr_runtime_handoff_error(account_info: Optional[Dict[str, Any]]) -> Optional[str]:
    """Return an error when QR cookies were not handed to the account runtime."""
    if not isinstance(account_info, dict):
        return None

    if account_info.get('task_restarted') is False and not account_info.get('fallback_reason'):
        return (
            account_info.get('warning_message')
            or account_info.get('error_message')
            or '扫码登录已获取Cookie，但账号任务未启动'
        )
    return None


async def _await_cookie_manager_handoff(result: Any) -> None:
    """Wait for CookieManager handoff when it returns an awaitable task."""
    if result is None:
        return
    if asyncio.isfuture(result) or inspect.isawaitable(result):
        await result


def _consume_cookie_manager_handoff(result: Any) -> None:
    """Surface synchronous CookieManager handoff failures to sync route handlers."""
    if result is None:
        return
    if hasattr(result, "result") and callable(result.result):
        try:
            result.result(timeout=30)
        except TypeError:
            result.result()


def load_keywords() -> List[Tuple[str, str]]:
    """读取关键字→回复映射表

    文件格式支持：
        关键字<空格/制表符/冒号>回复内容
    忽略空行和以 # 开头的注释行
    """
    mapping: List[Tuple[str, str]] = []
    if not KEYWORDS_FILE.exists():
        return mapping

    with KEYWORDS_FILE.open('r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            # 尝试用\t、空格、冒号分隔
            if '\t' in line:
                key, reply = line.split('\t', 1)
            elif ' ' in line:
                key, reply = line.split(' ', 1)
            elif ':' in line:
                key, reply = line.split(':', 1)
            else:
                # 无法解析的行，跳过
                continue
            mapping.append((key.strip(), reply.strip()))
    return mapping


KEYWORDS_MAPPING = load_keywords()


# 认证相关模型


def verify_token(credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)) -> Optional[Dict[str, Any]]:
    """验证token并返回用户信息"""
    if not credentials:
        return None

    return session_service.verify(credentials.credentials, db_manager.get_user_by_id)


def _remove_session_tokens_for_user(user_id: int) -> int:
    """Remove all in-memory session tokens for a user after permission changes."""
    return session_service.revoke_user(user_id)

def verify_admin_token(credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)) -> Dict[str, Any]:
    """验证管理员token"""
    user_info = verify_token(credentials)
    if not user_info:
        raise HTTPException(status_code=401, detail="未授权访问")

    # 检查是否是管理员（优先使用is_admin字段，兼容旧的admin用户名判断）
    is_admin = user_info.get('is_admin', False) or user_info['username'] == ADMIN_USERNAME
    if not is_admin:
        raise HTTPException(status_code=403, detail="需要管理员权限")

    return user_info


def require_auth(user_info: Optional[Dict[str, Any]] = Depends(verify_token)):
    """需要认证的依赖，返回用户信息"""
    if not user_info:
        raise HTTPException(status_code=401, detail="未授权访问")
    return user_info


def get_current_user(user_info: Dict[str, Any] = Depends(require_auth)) -> Dict[str, Any]:
    """获取当前登录用户信息"""
    return user_info


def get_current_user_optional(user_info: Optional[Dict[str, Any]] = Depends(verify_token)) -> Optional[Dict[str, Any]]:
    """获取当前用户信息（可选，不强制要求登录）"""
    return user_info


def get_user_log_prefix(user_info: Dict[str, Any] = None) -> str:
    """获取用户日志前缀"""
    if user_info:
        return f"【{user_info['username']}#{user_info['user_id']}】"
    return "【系统】"


def require_admin(current_user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    """要求管理员权限"""
    # 优先使用is_admin字段，兼容旧的admin用户名判断
    is_admin = current_user.get('is_admin', False) or current_user['username'] == 'admin'
    if not is_admin:
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return current_user


def log_with_user(level: str, message: str, user_info: Dict[str, Any] = None):
    """带用户信息的日志记录"""
    prefix = get_user_log_prefix(user_info)
    full_message = f"{prefix} {message}"

    if level.lower() == 'info':
        logger.info(full_message)
    elif level.lower() == 'error':
        logger.error(full_message)
    elif level.lower() == 'warning':
        logger.warning(full_message)
    elif level.lower() == 'debug':
        logger.debug(full_message)
    else:
        logger.info(full_message)


def _audit_actor_from_request(request: Request) -> Optional[Dict[str, Any]]:
    try:
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            return None
        token = auth_header.split(" ", 1)[1]
        token_data = SESSION_TOKENS.get(token)
        if not token_data:
            return None
        if time.time() - token_data.get('timestamp', 0) > TOKEN_EXPIRE_TIME:
            return None
        return {
            "user_id": token_data.get("user_id"),
            "username": token_data.get("username"),
            "is_admin": bool(token_data.get("is_admin", False)),
        }
    except Exception:
        return None


def _should_audit_request(path: str) -> bool:
    if not path:
        return False
    excluded_prefixes = (
        "/static/",
        "/favicon",
        "/captcha/",
        "/health",
        "/docs",
        "/redoc",
        "/openapi.json",
    )
    return not any(path.startswith(prefix) for prefix in excluded_prefixes)


def audit_event(
    *,
    category: str,
    action: str,
    status: str = "success",
    actor: Optional[Dict[str, Any]] = None,
    request: Optional[Request] = None,
    resource_type: str = None,
    resource_id: Any = None,
    duration_ms: int = None,
    message: str = None,
    details: Optional[Dict[str, Any]] = None,
) -> int:
    return record_audit_event(
        db_manager,
        category=category,
        action=action,
        status=status,
        actor=actor,
        request=request,
        resource_type=resource_type,
        resource_id=resource_id,
        duration_ms=duration_ms,
        message=message,
        details=details,
    )


def match_reply(cookie_id: str, message: str) -> Optional[str]:
    """根据 cookie_id 及消息内容匹配回复
    只有启用的账号才会匹配关键字回复
    """
    mgr = cookie_manager.manager
    if mgr is None:
        return None

    # 检查账号是否启用
    if not mgr.get_cookie_status(cookie_id):
        return None  # 禁用的账号不参与自动回复

    # 优先账号级关键字
    if mgr.get_keywords(cookie_id):
        for k, r in mgr.get_keywords(cookie_id):
            if k in message:
                return r

    # 全局关键字
    for k, r in KEYWORDS_MAPPING:
        if k in message:
            return r
    return None


class RequestModel(BaseModel):
    cookie_id: str
    msg_time: str
    user_url: str
    send_user_id: str
    send_user_name: str
    item_id: str
    send_message: str
    chat_id: str


class ResponseData(BaseModel):
    send_msg: str


class ResponseModel(BaseModel):
    code: int
    data: ResponseData


@asynccontextmanager
async def app_lifespan(app: FastAPI):
    ensure_runtime_directories(PROJECT_ROOT)
    setup_file_logging(root=PROJECT_ROOT)
    logger.info("Web服务器启动，文件日志收集器已初始化")
    scheduled_task = asyncio.create_task(
        scheduled_task_checker(),
        name="scheduled-task-checker",
    )
    app.state.scheduled_task = scheduled_task
    logger.info("定时任务调度器已启动")
    auto_rate_task = asyncio.create_task(
        auto_rate_task_loop(),
        name="auto-rate-task-loop",
    )
    app.state.auto_rate_task = auto_rate_task
    logger.info("自动补评价任务已启动")
    qr_cleanup_task = asyncio.create_task(
        _qr_check_cleanup_loop(),
        name="qr-check-cleanup-loop",
    )
    app.state.qr_cleanup_task = qr_cleanup_task
    try:
        yield
    finally:
        scheduled_task.cancel()
        with suppress(asyncio.CancelledError):
            await scheduled_task
        auto_rate_task.cancel()
        with suppress(asyncio.CancelledError):
            await auto_rate_task
        qr_cleanup_task.cancel()
        with suppress(asyncio.CancelledError):
            await qr_cleanup_task


app = FastAPI(
    title="Xianyu Management API",
    version="1.0.0",
    description="闲鱼管理系统API",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=app_lifespan,
)
app.state.maintenance_lock = asyncio.Lock()
app.state.maintenance_mode = False
app.include_router(
    create_auth_router(
        session_service=session_service,
        verify_dependency=verify_token,
        security=security,
        admin_username=ADMIN_USERNAME,
    )
)

# 注册刮刮乐远程控制路由
if CAPTCHA_ROUTER_AVAILABLE:
    app.include_router(captcha_router)
    logger.info("✅ 已注册刮刮乐远程控制路由: /api/captcha")
else:
    logger.warning("⚠️ 刮刮乐远程控制路由未注册")

# 添加请求日志中间件
@app.middleware("http")
async def reject_during_database_maintenance(request: Request, call_next):
    if request.url.path != "/health/live" and getattr(app.state, "maintenance_mode", False):
        return JSONResponse(status_code=503, content={"detail": "系统正在执行数据库维护"})
    return await call_next(request)


@app.middleware("http")
async def log_requests(request, call_next):
    start_time = time.time()

    # 获取用户信息
    user_info = "未登录"
    try:
        # 从请求头中获取Authorization
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.split(" ")[1]
            if token in SESSION_TOKENS:
                token_data = SESSION_TOKENS[token]
                # 检查token是否过期
                if time.time() - token_data['timestamp'] <= TOKEN_EXPIRE_TIME:
                    user_info = f"【{token_data['username']}#{token_data['user_id']}】"
    except Exception:
        pass

    logger.info(f"🌐 {user_info} API请求: {request.method} {request.url.path}")

    response = await call_next(request)

    process_time = time.time() - start_time
    logger.info(f"✅ {user_info} API响应: {request.method} {request.url.path} - {response.status_code} ({process_time:.3f}s)")

    request_path = request.url.path
    if _should_audit_request(request_path):
        audit_event(
            category="request",
            action="http_request",
            status=status_from_http_status_code(response.status_code),
            actor=_audit_actor_from_request(request),
            request=request,
            duration_ms=int(process_time * 1000),
            message=f"{request.method} {request_path} -> {response.status_code}",
            details={
                "status_code": response.status_code,
                "query": dict(request.query_params),
                "user_agent": request.headers.get("User-Agent"),
            },
        )

    return response

# 提供前端静态文件
import os
static_dir = os.path.join(os.path.dirname(__file__), 'static')
if not os.path.exists(static_dir):
    os.makedirs(static_dir, exist_ok=True)


# ???????????
@app.middleware("http")
async def add_cache_headers(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/static/js/") or request.url.path.startswith("/static/css/"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response

app.mount('/static', StaticFiles(directory=static_dir), name='static')

# 确保图片上传目录存在
uploads_dir = os.path.join(static_dir, 'uploads', 'images')
if not os.path.exists(uploads_dir):
    os.makedirs(uploads_dir, exist_ok=True)
    logger.info(f"创建图片上传目录: {uploads_dir}")

# 健康检查端点
@app.get('/health/live')
async def liveness_check():
    """Constant-time process liveness check, including during maintenance."""
    return {"status": "alive", "timestamp": time.time()}


@app.get('/health')
@app.get('/health/ready')
async def health_check():
    """健康检查端点，用于Docker健康检查和负载均衡器"""
    try:
        # 检查Cookie管理器状态
        manager_status = "ok" if cookie_manager.manager is not None else "error"

        # 检查数据库连接
        from db_manager import db_manager
        try:
            db_manager.get_all_cookies()
            db_status = "ok"
        except Exception:
            db_status = "error"

        # 获取系统状态
        import psutil
        cpu_percent = psutil.cpu_percent(interval=None)
        memory_info = psutil.virtual_memory()

        status = {
            "status": "healthy" if manager_status == "ok" and db_status == "ok" else "unhealthy",
            "timestamp": time.time(),
            "services": {
                "cookie_manager": manager_status,
                "database": db_status
            },
            "system": {
                "cpu_percent": cpu_percent,
                "memory_percent": memory_info.percent,
                "memory_available": memory_info.available
            }
        }

        if status["status"] == "unhealthy":
            raise HTTPException(status_code=503, detail=status)

        return status

    except HTTPException:
        raise
    except Exception as e:
        return {
            "status": "unhealthy",
            "timestamp": time.time(),
            "error": str(e)
        }


# 重定向根路径到登录页面


# ========================= 验证码API =========================


# ========================= 验证码API结束 =========================


# 登录页面路由


# 注册页面路由


# 管理页面 - 由前端JS检查管理员权限


# 文件下载页面

# 登录接口


# 销售额数据查询接口


# 周销售额和月销售额查询接口


# ========================= 防暴力破解管理API =========================


# ========================= 防暴力破解管理API结束 =========================


# 修改管理员密码接口


# 生成图形验证码接口


# 验证图形验证码接口


# 发送验证码接口（需要先验证图形验证码）


# 用户注册接口


# ------------------------- 发送消息接口 -------------------------

# /send-message API key must be explicitly configured.
API_SECRET_ENV = "SEND_MESSAGE_API_KEY"
XIANYU_REPLY_API_KEY_ENV = "XIANYU_REPLY_API_KEY"

class SendMessageRequest(BaseModel):
    api_key: str
    cookie_id: str
    chat_id: str
    to_user_id: str
    message: str


class SendMessageResponse(BaseModel):
    success: bool
    message: str


def verify_api_key(api_key: str) -> bool:
    """验证API秘钥"""
    try:
        # 从系统设置中获取QQ回复消息秘钥
        from db_manager import db_manager
        qq_secret_key = (
            db_manager.get_system_setting('qq_reply_secret_key')
            or os.getenv(API_SECRET_ENV)
            or ''
        ).strip()

        if not qq_secret_key:
            logger.warning("send-message API key is not configured")
            return False

        return secrets.compare_digest(api_key, qq_secret_key)
    except Exception as e:
        logger.error(f"验证API秘钥时发生异常: {e}")
        return False


def require_xianyu_reply_api_key(
    internal_api_key: Optional[str] = Header(default=None, alias="X-Internal-API-Key"),
) -> None:
    """Authenticate the internal automatic-reply callback."""
    configured_key = (os.getenv(XIANYU_REPLY_API_KEY_ENV) or "").strip()
    if not configured_key:
        logger.error("xianyu reply API key is not configured")
        raise HTTPException(status_code=503, detail="内部回复服务未配置")
    if not internal_api_key or not secrets.compare_digest(internal_api_key, configured_key):
        raise HTTPException(status_code=401, detail="内部服务认证失败")


# ------------------------- 账号 / 关键字管理接口 -------------------------


class CookieIn(BaseModel):
    id: str
    value: str


class ManualCookieImportRequest(BaseModel):
    account_id: str
    cookie: str
    show_browser: bool = False


class QRLoginSubmitCookiesRequest(BaseModel):
    """扫码风控验证后，用户侧成功 Cookie 回传（哪边成功用哪边）。"""
    cookies: str


class QRLoginSubmitUrlRequest(BaseModel):
    """扫码风控验证后，用户粘贴成功/回调 URL，由服务端换 Cookie。"""
    url: str


class CookieStatusIn(BaseModel):
    enabled: bool


class DefaultReplyIn(BaseModel):
    enabled: bool
    reply_content: Optional[str] = None
    reply_once: bool = False


class NotificationChannelIn(BaseModel):
    name: str
    type: str = "qq"
    config: str


class NotificationChannelUpdate(BaseModel):
    name: str
    config: str
    enabled: bool = True


class MessageNotificationIn(BaseModel):
    channel_id: int
    enabled: bool = True


class SystemSettingIn(BaseModel):
    value: str
    description: Optional[str] = None


NIGHT_MODE_SYSTEM_SETTING_KEYS = {
    'risk_control_night_mode_enabled',
    'risk_control_night_start_hour',
    'risk_control_night_end_hour',
}


def _validate_system_setting_value(key: str, value: str) -> str:
    if key == 'risk_control_night_mode_enabled':
        normalized = str(value).strip().lower()
        if normalized in {'true', '1', 'yes', 'on'}:
            return 'true'
        if normalized in {'false', '0', 'no', 'off'}:
            return 'false'
        raise HTTPException(status_code=400, detail='夜间降频开关只能为 true 或 false')

    if key in {'risk_control_night_start_hour', 'risk_control_night_end_hour'}:
        try:
            hour = int(str(value).strip())
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail='夜间时间必须是 0-23 的整数')
        if hour < 0 or hour > 23:
            raise HTTPException(status_code=400, detail='夜间时间必须是 0-23 的整数')
        return str(hour)

    return value


class SystemSettingCreateIn(BaseModel):
    key: str
    value: str
    description: Optional[str] = None


class ChatSendRequest(BaseModel):
    cookie_id: str
    chat_id: str
    to_user_id: str
    message: str


class SaveItemKeywordsRequest(BaseModel):
    keywords: list
    item_reply: Optional[str] = None


class CopyKeywordsRequest(BaseModel):
    source_item_id: str
    target_item_ids: List[str]


class PersonalBlacklistCreateRequest(BaseModel):
    buyer_ids: Any
    cookie_id: Optional[str] = None
    item_id: Optional[str] = None
    buyer_nick: Optional[str] = ''
    reason: Optional[str] = ''
    is_enabled: bool = True


class PersonalBlacklistBatchDeleteRequest(BaseModel):
    ids: List[int]


class PersonalBlacklistToggleRequest(BaseModel):
    is_enabled: bool


class ChatHydrationDebug(BaseModel):
    success: bool
    cookie_id: str
    chat_id: str
    stage: str
    message: str
    fetched: int = 0
    saved: int = 0
    normalized_count: int = 0
    skipped_count: int = 0
    sample_sender_id: Optional[str] = None
    sample_sender_name: Optional[str] = None
    sample_content: Optional[str] = None
    remote_history_status: Optional[str] = None
    remote_history_checked_at: Optional[str] = None
    runtime_status: Optional[Dict[str, Any]] = None


_chat_session_enrichment_cache: Dict[str, Dict[str, Any]] = {}
_CHAT_SESSION_ENRICHMENT_TTL_SECONDS = 180
_chat_history_probe_cache: Dict[str, Dict[str, Any]] = {}
_CHAT_HISTORY_PROBE_TTL_SECONDS = 6 * 60 * 60


def _build_chat_history_probe_key(cookie_id: str, chat_id: str) -> str:
    return f"{str(cookie_id or '').strip()}::{str(chat_id or '').strip()}"


def _get_cached_chat_history_probe(cookie_id: str, chat_id: str) -> Optional[Dict[str, Any]]:
    cache_key = _build_chat_history_probe_key(cookie_id, chat_id)
    cached = _chat_history_probe_cache.get(cache_key)
    if not cached:
        return None

    checked_at = float(cached.get('checked_at') or 0)
    if checked_at <= 0 or (time.time() - checked_at) > _CHAT_HISTORY_PROBE_TTL_SECONDS:
        _chat_history_probe_cache.pop(cache_key, None)
        return None

    return dict(cached)


def _apply_chat_history_probe_to_session(cookie_id: str, session: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(session or {})
    chat_id = str(normalized.get('chat_id') or '').strip()
    if not chat_id:
        return normalized

    probe = _get_cached_chat_history_probe(cookie_id, chat_id)
    if probe:
        normalized['remote_history_status'] = probe.get('status')
        normalized['remote_history_checked_at'] = probe.get('checked_at_display')
        normalized['remote_history_note'] = probe.get('note')
        normalized['remote_history_fetched'] = probe.get('fetched', 0)

    return normalized


def _compact_chat_user_ext(user_ext: Any) -> Dict[str, Any]:
    if not isinstance(user_ext, dict):
        return {}
    allowed_keys = {
        'yuxiaopuDomain', 'yuxiaopuLevelImage', 'fansTag', 'userMedal',
        'avatarPendant', 'chatBackground', 'guestChatBubble', 'ownerChatBubble'
    }
    return {key: value for key, value in user_ext.items() if key in allowed_keys and value}


def _parse_item_pre_info(raw_value: Any) -> Dict[str, Any]:
    parsed = _safe_json_loads(raw_value)
    if parsed:
        return parsed
    if not isinstance(raw_value, str) or not raw_value.strip():
        return {}
    try:
        return json.loads(raw_value.replace('\\"', '"'))
    except Exception:
        return {}


def _normalize_headinfo_buttons(buttons: Any) -> List[Dict[str, Any]]:
    normalized = []
    if not isinstance(buttons, list):
        return normalized
    for button in buttons:
        if not isinstance(button, dict):
            continue
        normalized.append({
            'name': button.get('name'),
            'style': button.get('style'),
            'trade_action': button.get('tradeAction'),
            'url': (((button.get('clickEvent') or {}).get('data') or {}).get('url')),
        })
    return normalized


def _build_chat_session_cache_key(cookie_id: str, session: Dict[str, Any]) -> str:
    return f"{cookie_id}:{session.get('chat_id') or ''}:{session.get('item_id') or ''}:{session.get('sender_id') or ''}"


def _get_cached_chat_session_enrichment(cache_key: str) -> Optional[Dict[str, Any]]:
    cached = _chat_session_enrichment_cache.get(cache_key)
    if not cached:
        return None
    if (time.time() - float(cached.get('cached_at') or 0)) > _CHAT_SESSION_ENRICHMENT_TTL_SECONDS:
        _chat_session_enrichment_cache.pop(cache_key, None)
        return None
    return dict(cached.get('value') or {})


def _set_cached_chat_session_enrichment(cache_key: str, value: Dict[str, Any]) -> None:
    _chat_session_enrichment_cache[cache_key] = {
        'cached_at': time.time(),
        'value': dict(value or {}),
    }


async def _enrich_single_chat_session(cookie_id: str, session: Dict[str, Any]) -> Dict[str, Any]:
    from XianyuAutoAsync import XianyuLive

    cache_key = _build_chat_session_cache_key(cookie_id, session)
    cached = _get_cached_chat_session_enrichment(cache_key)
    if cached is not None:
        return {**session, **cached}

    live_instance = XianyuLive.get_instance(cookie_id)
    if not live_instance:
        return session

    session_id = str(session.get('chat_id') or '').strip()
    if not session_id:
        return session

    item_id = str(session.get('item_id') or '').strip()
    sender_id = str(session.get('sender_id') or session.get('buyer_id') or '').strip()
    session_type = int(session.get('session_type') or 1)

    enriched: Dict[str, Any] = {}

    try:
        user_info_result = await live_instance.fetch_im_user_info(
            session_id=session_id,
            session_type=session_type,
            is_owner=False,
            message_id=session.get('message_id') or None,
        )
        user_info = user_info_result.get('userInfo', {}) if isinstance(user_info_result, dict) else {}
        if user_info:
            enriched.update({
                'avatar': user_info.get('logo'),
                'fish_nick': user_info.get('fishNick') or user_info.get('nick') or session.get('buyer_name') or session.get('sender_name'),
                'user_ext': _compact_chat_user_ext(user_info.get('ext')),
                'buyer_name_resolved': user_info.get('fishNick') or user_info.get('nick') or session.get('buyer_name'),
                'sender_id': sender_id or session.get('sender_id'),
            })
    except Exception as e:
        logger.debug(f"会话用户信息增强失败: cookie_id={cookie_id}, session_id={session_id}, error={mask_sensitive_text(e)}")

    if item_id:
        try:
            headinfo = await live_instance.fetch_im_head_info(session_id=session_id, item_id=item_id, session_type=session_type)
            common_data = headinfo.get('commonData', {}) if isinstance(headinfo, dict) else {}
            item_pre_info = _parse_item_pre_info(common_data.get('itemPreInfo'))
            left_data = ((headinfo.get('left') or {}).get('data') or {}) if isinstance(headinfo, dict) else {}
            middle_data = ((headinfo.get('middle') or {}).get('data') or {}) if isinstance(headinfo, dict) else {}
            right_data = ((headinfo.get('right') or {}).get('data') or {}) if isinstance(headinfo, dict) else {}
            ut_args = headinfo.get('utArgs', {}) if isinstance(headinfo, dict) else {}
            enriched.update({
                'headinfo_template': headinfo.get('template') if isinstance(headinfo, dict) else None,
                'item_title': item_pre_info.get('title') or session.get('item_title'),
                'item_price': item_pre_info.get('soldPrice') or middle_data.get('price'),
                'item_pic': left_data.get('picUrl'),
                'item_jump_url': left_data.get('jumpUrl'),
                'item_subtitle': middle_data.get('subTitle'),
                'item_tips': middle_data.get('tips'),
                'action_buttons': _normalize_headinfo_buttons(right_data.get('btnList')),
                'order_id': headinfo.get('orderId') if isinstance(headinfo, dict) else None,
                'order_detail_url': headinfo.get('orderDetailUrl') if isinstance(headinfo, dict) else None,
                'order_status_name': (ut_args.get('orderStatusName') if isinstance(ut_args, dict) else None),
            })
        except Exception as e:
            logger.debug(f"会话头信息增强失败: cookie_id={cookie_id}, session_id={session_id}, item_id={item_id}, error={mask_sensitive_text(e)}")

    try:
        blacklist_info = await live_instance.fetch_im_blacklist_status(session_id=session_id)
        if blacklist_info:
            enriched['blacklist_status'] = {
                'is_in_black': bool(blacklist_info.get('isInBlack')),
                'show_blacklist': bool(blacklist_info.get('showBlackList')),
            }
    except Exception as e:
        logger.debug(f"会话黑名单增强失败: cookie_id={cookie_id}, session_id={session_id}, error={mask_sensitive_text(e)}")

    _set_cached_chat_session_enrichment(cache_key, enriched)
    return {**session, **enriched}


async def _enrich_chat_sessions(cookie_id: str, sessions: List[Dict[str, Any]], limit: int = 30) -> List[Dict[str, Any]]:
    if not sessions:
        return []
    sessions = list(sessions)
    priority_sessions = sessions[:max(1, min(limit, len(sessions)))]
    remaining_sessions = sessions[len(priority_sessions):]
    enriched_priority = []
    for session in priority_sessions:
        enriched_priority.append(await _enrich_single_chat_session(cookie_id, session))
    return enriched_priority + remaining_sessions


def _safe_json_loads(raw_value: Any) -> Dict[str, Any]:
    if isinstance(raw_value, dict):
        return raw_value
    if not isinstance(raw_value, str) or not raw_value.strip():
        return {}
    try:
        parsed = json.loads(raw_value)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _extract_history_message_payload(message: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(message, dict):
        return {}
    try:
        message_1 = message.get('1', {}) if isinstance(message, dict) else {}
        message_6 = message_1.get('6', {}) if isinstance(message_1, dict) else {}
        message_6_3 = message_6.get('3', {}) if isinstance(message_6, dict) else {}
        return _safe_json_loads(message_6_3.get('5', '') or '{}')
    except Exception:
        return {}


def _extract_rich_message_fields(message: Dict[str, Any]) -> Dict[str, Any]:
    payload = _extract_history_message_payload(message)
    result = {
        'display_type': None,
        'content': '',
        'image_url': None,
        'media_url': None,
        'link_url': None,
        'extra_json': None,
    }

    if not payload:
        return result

    dx_card = payload.get('dxCard', {}) if isinstance(payload, dict) else {}
    dx_item = dx_card.get('item', {}) if isinstance(dx_card, dict) else {}
    main = dx_item.get('main', {}) if isinstance(dx_item, dict) else {}
    ex_content = main.get('exContent', {}) if isinstance(main, dict) else {}
    title = str(ex_content.get('title') or main.get('title') or payload.get('title') or '').strip()
    content = str(ex_content.get('content') or payload.get('text') or '').strip()
    button_text = str((ex_content.get('button') or {}).get('text') or '').strip()

    image_url = (
        ((payload.get('image') or {}).get('pics') or [{}])[0].get('url')
        if isinstance(payload.get('image'), dict) and (payload.get('image').get('pics') or [])
        else None
    )
    video_url = (
        ((payload.get('video') or {}).get('playUrl'))
        or ((payload.get('video') or {}).get('url'))
        or ((main.get('video') or {}).get('playUrl') if isinstance(main.get('video'), dict) else None)
    )
    link_url = (
        str(payload.get('targetUrl') or '').strip()
        or str(payload.get('url') or '').strip()
        or str((ex_content.get('button') or {}).get('actionUrl') or '').strip()
    ) or None

    item_id = None
    item_title = None
    item_image = None
    if isinstance(dx_item, dict):
        item_id = dx_item.get('itemId') or dx_item.get('id')
        item_title = dx_item.get('title') or title
        item_image = dx_item.get('itemMainPic') or dx_item.get('pic')

    extra = {
        'payload': payload,
        'title': title or None,
        'button_text': button_text or None,
        'item_share': {
            'item_id': item_id,
            'title': item_title,
            'image_url': item_image,
            'seller_id': dx_item.get('itemSellerId') if isinstance(dx_item, dict) else None,
        } if item_id or item_title or item_image else None,
    }

    if video_url:
        result['display_type'] = 'video'
        result['content'] = title or content or '[视频]'
        result['media_url'] = str(video_url).strip()
        result['image_url'] = image_url or item_image
        result['link_url'] = link_url
    elif image_url:
        result['display_type'] = 'image'
        result['content'] = title or content or '[图片]'
        result['image_url'] = str(image_url).strip()
        result['link_url'] = link_url
    elif item_id or item_title:
        result['display_type'] = 'item_share'
        result['content'] = item_title or title or content or '[商品分享]'
        result['image_url'] = item_image
        result['link_url'] = link_url
    elif link_url:
        result['display_type'] = 'link'
        result['content'] = title or content or button_text or '[链接]'
        result['link_url'] = link_url
    elif title or content or button_text:
        result['display_type'] = 'card'
        result['content'] = ' / '.join([part for part in [title, content, button_text] if part])

    if result['display_type']:
        result['extra_json'] = json.dumps(extra, ensure_ascii=False)

    return result


def _extract_history_message_text(message: Dict[str, Any]) -> str:
    """从闲鱼历史消息结构中尽量提取可展示文本。"""
    if not isinstance(message, dict):
        return ''

    try:
        message_1 = message.get('1', {}) if isinstance(message, dict) else {}
        message_10 = message_1.get('10', {}) if isinstance(message_1, dict) else {}
        payload = _extract_history_message_payload(message)
        candidates = [
            message_10.get('reminderContent'),
            message_10.get('detailNotice'),
            message_10.get('reminderTitle'),
            message_10.get('reminderNotice'),
            (((payload.get('dxCard') or {}).get('item') or {}).get('main') or {}).get('title'),
            ((((payload.get('dxCard') or {}).get('item') or {}).get('main') or {}).get('exContent') or {}).get('title'),
            ((((payload.get('dxCard') or {}).get('item') or {}).get('main') or {}).get('exContent') or {}).get('content'),
            ((((payload.get('dxCard') or {}).get('item') or {}).get('main') or {}).get('exContent') or {}).get('button', {}).get('text'),
            (payload.get('text') if isinstance(payload, dict) else None),
        ]
        for candidate in candidates:
            text = str(candidate or '').strip()
            if text and text not in {'{}', '[]'}:
                return text
    except Exception:
        pass

    raw_text = str(message.get('raw') or '').strip()
    return raw_text[:120] if raw_text else ''


def _format_history_created_at(raw_value: Any) -> Optional[str]:
    if raw_value in (None, '', 0, '0'):
        return None

    if isinstance(raw_value, str):
        text = raw_value.strip()
        if not text:
            return None
        if any(sep in text for sep in ('-', '/')) and ':' in text:
            normalized = text.replace('T', ' ')
            return normalized[:19]
        raw_value = text

    try:
        value = int(float(raw_value))
    except (TypeError, ValueError):
        return None

    if value <= 0:
        return None

    if value < 10**11:
        value *= 1000

    try:
        return datetime.fromtimestamp(value / 1000, tz=LOCAL_TIMEZONE).strftime('%Y-%m-%d %H:%M:%S')
    except (OverflowError, OSError, ValueError):
        return None


def _build_chat_sessions_from_recent_orders(cookie_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    """当本地 chat_messages 为空时，基于最近订单构造可点击会话入口。"""
    sessions: List[Dict[str, Any]] = []
    seen_chat_ids = set()
    orders = db_manager.get_orders_by_cookie(cookie_id, limit=max(limit * 4, 100))

    for order in orders:
        sid = str(order.get('sid') or '').strip()
        if not sid:
            continue
        chat_id = sid.split('@')[0]
        if not chat_id or chat_id in seen_chat_ids:
            continue
        seen_chat_ids.add(chat_id)
        sessions.append({
            'chat_id': chat_id,
            'sender_id': order.get('buyer_id') or '',
            'buyer_id': order.get('buyer_id') or '',
            'sender_name': order.get('buyer_nick') or order.get('buyer_id') or chat_id,
            'buyer_name': order.get('buyer_nick') or '',
            'content': '',
            'content_type': 1,
            'item_id': order.get('item_id') or '',
            'direction': 2,
            'created_at': order.get('updated_at') or order.get('created_at') or '',
        })
        if len(sessions) >= limit:
            break

    sessions.sort(key=lambda item: item.get('created_at') or '', reverse=True)
    return sessions


def _merge_chat_sessions_with_order_fallback(
    local_sessions: List[Dict[str, Any]],
    fallback_sessions: List[Dict[str, Any]],
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """合并本地会话和订单兜底会话，避免本地只有少量会话时隐藏其他历史入口。"""
    merged: List[Dict[str, Any]] = []
    seen_chat_ids = set()

    for session in local_sessions or []:
        chat_id = str(session.get('chat_id') or '').strip()
        if not chat_id or chat_id in seen_chat_ids:
            continue
        merged.append(session)
        seen_chat_ids.add(chat_id)

    for session in fallback_sessions or []:
        chat_id = str(session.get('chat_id') or '').strip()
        if not chat_id or chat_id in seen_chat_ids:
            continue
        merged.append(session)
        seen_chat_ids.add(chat_id)

    merged.sort(key=lambda item: str(item.get('created_at') or ''), reverse=True)
    return merged[:limit]


def _annotate_chat_sessions(cookie_id: str, sessions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    annotated = []
    for session in sessions or []:
        annotated.append(_apply_chat_history_probe_to_session(cookie_id, session))
    return annotated


def _get_user_cookies_map(current_user: Dict[str, Any]) -> Dict[str, str]:
    user_id = current_user['user_id']
    return db_manager.get_all_cookies(user_id)


def _ensure_cookie_access(cid: str, current_user: Dict[str, Any]) -> str:
    try:
        return AccountOwnershipPolicy(db_manager).require_owned_account(
            current_user['user_id'], cid
        )
    except MissingAccountId:
        raise HTTPException(status_code=400, detail="缺少Cookie ID")
    except AccountForbidden:
        raise HTTPException(status_code=403, detail="无权限操作该Cookie")


TASK_LOG_TYPE_LABELS = {
    'auto_comment': '自动评价',
    'item_polish': '商品擦亮',
    'login_renew': '登录续期',
    'cookie_refresh': 'Cookie刷新',
    'other_task': '其他任务',
}


def _normalize_task_log_limit(limit: int) -> int:
    try:
        return max(1, min(int(limit or 100), 500))
    except Exception:
        return 100


def _normalize_task_log_offset(offset: int) -> int:
    try:
        return max(0, int(offset or 0))
    except Exception:
        return 0


def _get_task_log_cookie_scope(current_user: Dict[str, Any], cookie_id: str = None) -> List[str]:
    if cookie_id:
        return [_ensure_cookie_access(cookie_id, current_user)]
    return list(_get_user_cookies_map(current_user).keys())


def _task_log_created_at_sort_value(log: Dict[str, Any]) -> float:
    value = log.get('created_at') or log.get('updated_at') or ''
    if isinstance(value, (int, float)):
        return float(value)
    try:
        text = str(value or '').strip()
        if not text:
            return 0.0
        normalized = text.replace('T', ' ')[:19]
        return datetime.strptime(normalized, '%Y-%m-%d %H:%M:%S').timestamp()
    except Exception:
        return 0.0


def _normalize_task_log_row(log: Dict[str, Any], task_type: str, task_label: str = None) -> Dict[str, Any]:
    normalized = dict(log or {})
    normalized['task_type'] = task_type
    normalized['task_label'] = task_label or TASK_LOG_TYPE_LABELS.get(task_type, task_type)
    normalized.setdefault('object_id', normalized.get('order_id') or normalized.get('item_id') or normalized.get('session_id') or '')
    normalized.setdefault('status', 'failed')
    normalized.setdefault('message', '')
    normalized.setdefault('created_at', normalized.get('updated_at') or '')
    return normalized


def _map_risk_log_to_task_type(log: Dict[str, Any]) -> str:
    event_type = str(log.get('event_type') or '').strip().lower()
    trigger_scene = str(log.get('trigger_scene') or '').strip().lower()
    result_code = str(log.get('result_code') or '').strip().lower()
    text = ' '.join(str(log.get(key) or '') for key in (
        'event_description', 'event_description_display', 'processing_result',
        'processing_result_display', 'error_message', 'error_message_display'
    )).lower()

    if (
        event_type in {'cookie_refresh', 'token_expired'}
        or trigger_scene in {
            'auto_cookie_refresh', 'manual_cookie_refresh', 'manual_password_refresh',
            'manual_qr_refresh', 'qr_login', 'token_refresh',
        }
        or 'cookie_refresh' in result_code
        or 'token_refresh' in result_code
        or 'cookie刷新' in text
        or 'token刷新' in text
    ):
        return 'cookie_refresh'

    if (
        trigger_scene in {'password_login', 'login_renew', 'session_keepalive'}
        or event_type in {'password_login', 'password_error', 'face_verify', 'sms_verify', 'qr_verify'}
        or 'password_login' in result_code
        or '登录' in text
        or '保活' in text
    ):
        return 'login_renew'

    return 'other_task'


def _normalize_risk_task_status(log: Dict[str, Any]) -> str:
    status = str(log.get('processing_status') or '').strip().lower()
    result_code = str(log.get('result_code') or '').strip().lower()
    combined = ' '.join(str(log.get(key) or '') for key in (
        'processing_result', 'processing_result_display', 'error_message', 'error_message_display'
    )).lower()

    if status == 'success' or 'success' in result_code or '成功' in combined:
        return 'success'
    if status == 'processing':
        return 'processing'
    if 'expired' in result_code or '过期' in combined or 'session_expired' in combined:
        return 'cookie_expired'
    if status == 'failed' or 'failed' in result_code or '失败' in combined or '异常' in combined:
        return 'failed'
    return status or 'failed'


def _risk_log_to_task_log(log: Dict[str, Any]) -> Dict[str, Any]:
    task_type = _map_risk_log_to_task_type(log)
    message_parts = [
        log.get('event_description_display') or log.get('event_description'),
        log.get('processing_result_display') or log.get('processing_result'),
        log.get('error_message_display') or log.get('error_message'),
    ]
    message = ' / '.join(str(part).strip() for part in message_parts if str(part or '').strip())
    return _normalize_task_log_row({
        'id': f"risk-{log.get('id')}",
        'batch_id': log.get('session_id') or log.get('result_code') or f"risk_{log.get('id')}",
        'cookie_id': log.get('cookie_id'),
        'object_id': log.get('session_id') or log.get('result_code') or log.get('event_type'),
        'status': _normalize_risk_task_status(log),
        'message': message or '-',
        'raw_response': log,
        'created_at': log.get('updated_at') or log.get('created_at'),
    }, task_type)


def _load_risk_task_logs(current_user: Dict[str, Any], task_type: str = 'all', cookie_id: str = None,
                         limit: int = 100) -> List[Dict[str, Any]]:
    cookie_ids = _get_task_log_cookie_scope(current_user, cookie_id)
    logs: List[Dict[str, Any]] = []
    for scoped_cookie_id in cookie_ids:
        risk_logs = db_manager.get_risk_control_logs(
            cookie_id=scoped_cookie_id,
            limit=max(20, min(limit, 500)),
            offset=0,
        )
        for risk_log in risk_logs:
            task_log = _risk_log_to_task_log(risk_log)
            if task_type == 'all' or task_log.get('task_type') == task_type:
                logs.append(task_log)
    return logs


def _normalize_runtime_timestamp(value: Any) -> Optional[float]:
    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        return None
    return timestamp if timestamp > 0 else None


def _format_runtime_timestamp(value: Any) -> Optional[str]:
    timestamp = _normalize_runtime_timestamp(value)
    if timestamp is None:
        return None

    return datetime.fromtimestamp(timestamp, tz=LOCAL_TIMEZONE).strftime('%Y-%m-%d %H:%M:%S')


def _get_runtime_age_seconds(value: Any) -> Optional[int]:
    timestamp = _normalize_runtime_timestamp(value)
    if timestamp is None:
        return None
    return max(0, int(time.time() - timestamp))


def _is_runtime_timestamp_recent(value: Any, window_seconds: Any) -> bool:
    timestamp = _normalize_runtime_timestamp(value)
    if timestamp is None:
        return False

    try:
        window = max(1, int(float(window_seconds)))
    except (TypeError, ValueError):
        return False

    return (time.time() - timestamp) <= window


def _build_runtime_monitoring_contract() -> Dict[str, Any]:
    return {
        'monitoring_safe': True,
        'monitoring_mode': 'local_snapshot',
        'monitoring_description': '仅读取本地运行态，不触发闲鱼探活、保活、刷新或历史消息拉取',
        'external_probe_performed': False,
        'auto_probe_allowed': False,
    }


def _build_runtime_risk_control_summary(
    token_refresh_status: Optional[Any],
    token_refresh_error_message: Optional[Any],
    session_keepalive_status: Optional[Any] = None,
    session_keepalive_error_message: Optional[Any] = None,
) -> Dict[str, Any]:
    status = str(token_refresh_status or session_keepalive_status or '').strip()
    token_error = str(token_refresh_error_message or '').strip()
    session_error = str(session_keepalive_error_message or '').strip()
    combined_detail = ' | '.join(part for part in [status, token_error, session_error] if part)
    status_lower = status.lower()
    detail_upper = combined_detail.upper()

    summary = None
    operator_action_required = False
    if status_lower in {
        'captcha_max_retries_exceeded',
        'manual_verification_required',
        'verification_pending_manual',
    } or 'FAIL_SYS_USER_VALIDATE' in detail_upper:
        summary = '闲鱼仍要求账号验证，自动刷新已进入受限状态；请在真实平台完成验证或重新登录后再观察。'
        operator_action_required = True
    elif status_lower == 'password_login_backoff_wait':
        summary = '账号登录/刷新处于退避等待，系统正在避免高频重试以降低风控风险。'
        operator_action_required = True
    elif status_lower in {
        'slider_failed',
        'risk_control',
        'token_expired_recovery_failed',
        'token_refresh_failed',
        'token_refresh_exception',
        'token_init_failed',
    }:
        summary = '账号刷新链路最近失败，当前监控仅展示本地失败状态，不会自动探活闲鱼。'

    return {
        'risk_control_status': status or None,
        'risk_control_summary': summary,
        'risk_control_detail': combined_detail or None,
        'operator_action_required': operator_action_required,
    }


def _build_live_runtime_status(cookie_id: str) -> Dict[str, Any]:
    cleaned_cid = str(cookie_id or '').strip()
    runtime_status = {
        **_build_runtime_monitoring_contract(),
        'instance_exists': False,
        'running': False,
        'connection_state': 'not_running',
        'ws_ready': False,
        'session_ready': False,
        'has_current_token': False,
        'message_stream_ready': False,
        'message_stream_status': 'not_running',
        'message_stream_note': None,
        'token_refresh_status': None,
        'token_refresh_error_message': None,
        'token_last_refreshed_at': None,
        'token_last_refreshed_at_display': None,
        'token_age_seconds': None,
        'token_cached': False,
        'session_keepalive_status': None,
        'session_keepalive_display_status': None,
        'session_keepalive_display_note': None,
        'session_keepalive_error_message': None,
        'session_keepalive_at': None,
        'session_keepalive_at_display': None,
        'session_keepalive_age_seconds': None,
        'session_transport_ready': False,
        'last_heartbeat_response_at': None,
        'last_heartbeat_response_at_display': None,
        'last_heartbeat_age_seconds': None,
        'last_heartbeat_sent_at': None,
        'last_heartbeat_sent_at_display': None,
        'last_heartbeat_sent_age_seconds': None,
        'ws_transport_ready': False,
        'last_business_activity_at': None,
        'last_business_activity_at_display': None,
        'last_business_activity_age_seconds': None,
        'last_sync_package_at': None,
        'last_sync_package_at_display': None,
        'last_sync_package_age_seconds': None,
        'last_user_chat_at': None,
        'last_user_chat_at_display': None,
        'last_user_chat_age_seconds': None,
        'last_stream_watchdog_reconnect_at': None,
        'last_stream_watchdog_reconnect_at_display': None,
        'last_stream_watchdog_reconnect_age_seconds': None,
        'last_message_received_at': None,
        'last_message_received_at_display': None,
        'last_message_age_seconds': None,
        'last_successful_connection_at': None,
        'last_successful_connection_at_display': None,
        'state_last_changed_at': None,
        'state_last_changed_at_display': None,
        'cookie_refresh_enabled': None,
        'manual_refresh_active': False,
        'auth_recovery_owner': None,
        'risk_control_status': None,
        'risk_control_summary': None,
        'risk_control_detail': None,
        'operator_action_required': False,
        'vnc_manual_action_available': False,
        'manual_browser_session_status': None,
        'manual_browser_reason': None,
    }
    if not cleaned_cid:
        return runtime_status

    live_instance = None
    try:
        if cookie_manager.manager:
            live_instance = getattr(cookie_manager.manager, 'live_instances', {}).get(cleaned_cid)
    except Exception:
        live_instance = None

    try:
        from XianyuAutoAsync import XianyuLive
    except Exception as e:
        if not live_instance:
            runtime_status['error'] = f"import_failed: {mask_sensitive_text(e)}"
            return runtime_status
    else:
        if not live_instance:
            live_instance = XianyuLive.get_instance(cleaned_cid)
        auth_recovery_state = XianyuLive.get_auth_recovery_lock_state(cleaned_cid)
        runtime_status['auth_recovery_owner'] = (auth_recovery_state or {}).get('owner')

    if not live_instance:
        return runtime_status

    connection_state = getattr(live_instance, 'connection_state', None)
    connection_state_value = getattr(connection_state, 'value', str(connection_state or 'unknown'))
    ws = getattr(live_instance, 'ws', None)
    session = getattr(live_instance, 'session', None)
    ws_transport_ready = bool(ws and not getattr(ws, 'closed', False))
    session_transport_ready = bool(session and not getattr(session, 'closed', True))
    token_cached = bool(getattr(live_instance, 'current_token', None))
    token_refresh_status = getattr(live_instance, 'last_token_refresh_status', None)
    session_keepalive_status = getattr(live_instance, 'last_session_keepalive_status', None)
    heartbeat_response_at = _normalize_runtime_timestamp(getattr(live_instance, 'last_heartbeat_response', 0))
    heartbeat_sent_at = _normalize_runtime_timestamp(getattr(live_instance, 'last_heartbeat_time', 0))
    token_refreshed_at = _normalize_runtime_timestamp(getattr(live_instance, 'last_token_refresh_time', 0))
    session_keepalive_at = _normalize_runtime_timestamp(getattr(live_instance, 'last_session_keepalive_time', 0))
    last_non_heartbeat_message_at = _normalize_runtime_timestamp(getattr(live_instance, 'last_non_heartbeat_message_time', 0))
    last_sync_package_at = _normalize_runtime_timestamp(getattr(live_instance, 'last_sync_package_time', 0))
    last_user_chat_at = _normalize_runtime_timestamp(getattr(live_instance, 'last_user_chat_time', 0))
    last_stream_watchdog_reconnect_at = _normalize_runtime_timestamp(getattr(live_instance, 'last_stream_watchdog_reconnect_time', 0))
    last_message_received_at = _normalize_runtime_timestamp(getattr(live_instance, 'last_message_received_time', 0))
    last_successful_connection_at = _normalize_runtime_timestamp(getattr(live_instance, 'last_successful_connection', 0))
    last_state_changed_at = _normalize_runtime_timestamp(getattr(live_instance, 'last_state_change_time', 0))

    heartbeat_interval = max(1, int(getattr(live_instance, 'heartbeat_interval', 15) or 15))
    heartbeat_timeout = max(1, int(getattr(live_instance, 'heartbeat_timeout', 30) or 30))
    token_refresh_interval = max(60, int(getattr(live_instance, 'token_refresh_interval', 72000) or 72000))
    token_retry_interval = max(30, int(getattr(live_instance, 'token_retry_interval', 180) or 180))
    session_keepalive_interval = max(60, int(getattr(live_instance, 'session_keepalive_interval', 600) or 600))
    session_keepalive_retry_interval = max(30, int(getattr(live_instance, 'session_keepalive_retry_interval', 180) or 180))
    stream_watchdog_grace_period = max(30, int(getattr(live_instance, 'stream_watchdog_grace_period', heartbeat_interval * 4) or heartbeat_interval * 4))
    message_stream_watchdog_timeout = max(60, int(getattr(live_instance, 'message_stream_watchdog_timeout', session_keepalive_interval * 3) or session_keepalive_interval * 3))

    ws_ready_window = max(heartbeat_timeout * 2, heartbeat_interval * 3, 45)
    recent_connection_window = max(heartbeat_interval + 5, 20)
    session_ready_window = max(session_keepalive_interval + session_keepalive_retry_interval + 30, 180)
    token_ready_window = max(token_refresh_interval + token_retry_interval, 300)
    now = time.time()

    recent_connection = _is_runtime_timestamp_recent(last_successful_connection_at, recent_connection_window)
    recent_heartbeat_ok = _is_runtime_timestamp_recent(heartbeat_response_at, ws_ready_window)
    recent_session_success = (
        session_keepalive_status == 'success'
        and _is_runtime_timestamp_recent(session_keepalive_at, session_ready_window)
    )
    recent_token_success = (
        token_refresh_status == 'success'
        and _is_runtime_timestamp_recent(token_refreshed_at, token_ready_window)
    )

    token_explicit_failure_statuses = {
        'captcha_max_retries_exceeded',
        'token_expired_recovery_failed',
        'token_refresh_failed',
        'token_refresh_exception',
        'token_init_failed',
    }
    session_display_status = session_keepalive_status
    session_display_note = None
    if (
        session_keepalive_status in {'auth_failed', 'api_failed', 'network_failed', 'response_parse_failed', 'exception'}
        and recent_token_success
        and session_transport_ready
    ):
        session_display_status = 'recovered'
        session_display_note = '轻保活最近一次失败，但已由后续 Token 恢复流程兜底恢复'

    ws_ready = (
        connection_state_value == 'connected'
        and ws_transport_ready
        and (recent_heartbeat_ok or recent_connection)
    )
    session_ready = (
        session_transport_ready
        and (
            recent_session_success
            or recent_token_success
        )
    )
    token_ready = (
        token_cached
        and token_refresh_status not in token_explicit_failure_statuses
        and (
            recent_token_success
            or (ws_ready and token_refresh_status in (None, 'success', 'started'))
            or (
                token_refresh_status is None
                and _is_runtime_timestamp_recent(token_refreshed_at, token_ready_window)
            )
        )
    )

    actual_business_activity_at = None
    if last_non_heartbeat_message_at is not None:
        if last_successful_connection_at is None or last_non_heartbeat_message_at > last_successful_connection_at:
            actual_business_activity_at = last_non_heartbeat_message_at

    connected_for_seconds = None
    if last_successful_connection_at is not None:
        connected_for_seconds = max(0, int(now - last_successful_connection_at))

    business_idle_reference = actual_business_activity_at or last_successful_connection_at
    business_idle_seconds = None
    if business_idle_reference is not None:
        business_idle_seconds = max(0, int(now - business_idle_reference))

    recent_watchdog_reconnect = _is_runtime_timestamp_recent(
        last_stream_watchdog_reconnect_at,
        message_stream_watchdog_timeout,
    )
    stream_stale_now = bool(
        ws_ready
        and recent_heartbeat_ok
        and connected_for_seconds is not None
        and connected_for_seconds >= stream_watchdog_grace_period
        and business_idle_seconds is not None
        and business_idle_seconds >= message_stream_watchdog_timeout
    )

    if connection_state_value in {'connecting', 'reconnecting'}:
        message_stream_status = 'recovering'
        message_stream_ready = False
    elif connection_state_value != 'connected' or not ws_transport_ready:
        message_stream_status = 'connection_unready'
        message_stream_ready = False
    elif stream_stale_now:
        message_stream_status = 'suspected_stale'
        message_stream_ready = False
    else:
        message_stream_ready = True
        if connected_for_seconds is not None and connected_for_seconds < stream_watchdog_grace_period and actual_business_activity_at is None:
            message_stream_status = 'warming_up'
        elif (
            recent_watchdog_reconnect
            and actual_business_activity_at is not None
            and last_stream_watchdog_reconnect_at is not None
            and actual_business_activity_at > last_stream_watchdog_reconnect_at
        ):
            message_stream_status = 'recovered'
        elif actual_business_activity_at is not None:
            message_stream_status = 'healthy'
        else:
            message_stream_status = 'watching'

    business_note = (
        f"最近非心跳业务包：{_format_runtime_timestamp(actual_business_activity_at)}"
        if actual_business_activity_at is not None else
        "当前连接尚未收到非心跳业务包"
    )
    sync_note = (
        f"最近同步包：{_format_runtime_timestamp(last_sync_package_at)}"
        if last_sync_package_at is not None else
        "当前连接尚未收到同步包"
    )
    user_chat_note = (
        f"最近真实买家消息：{_format_runtime_timestamp(last_user_chat_at)}"
        if last_user_chat_at is not None else
        "当前连接尚未收到真实买家消息"
    )
    message_stream_note_parts = [business_note]
    if message_stream_status == 'suspected_stale':
        message_stream_note_parts.extend([sync_note, user_chat_note])
    elif recent_watchdog_reconnect and last_stream_watchdog_reconnect_at is not None:
        message_stream_note_parts.append(
            f"最近一次假在线重连：{_format_runtime_timestamp(last_stream_watchdog_reconnect_at)}"
        )
        if actual_business_activity_at is None:
            message_stream_note_parts.append(sync_note)
    else:
        message_stream_note_parts.append(sync_note)
    message_stream_note = ' · '.join(message_stream_note_parts)

    manual_browser_status = None
    manual_browser_reason = None
    try:
        for session in password_login_sessions.values():
            if str(session.get('account_id') or '').strip() != cleaned_cid:
                continue
            if not session.get('show_browser'):
                continue
            session_status = str(session.get('status') or '').strip()
            if session_status in {'success', 'failed', 'cancelled', 'error', 'not_found', 'forbidden'}:
                continue
            if session.get('completed_at'):
                continue
            manual_browser_status = session_status or 'processing'
            manual_browser_reason = 'active_password_refresh' if session.get('refresh_mode') else 'active_password_login'
            break
    except Exception:
        manual_browser_status = None
        manual_browser_reason = None

    vnc_relevant_token_statuses = {
        'manual_refresh_active',
        'manual_refresh_browser_stabilizing',
        'verification_pending_manual',
        'manual_verification_required',
    }
    vnc_manual_action_available = bool(
        manual_browser_status
        or token_refresh_status in vnc_relevant_token_statuses
    )

    runtime_status.update({
        'instance_exists': True,
        'running': True,
        'connection_state': connection_state_value,
        'ws_ready': ws_ready,
        'session_ready': session_ready,
        'has_current_token': token_ready,
        'message_stream_ready': message_stream_ready,
        'message_stream_status': message_stream_status,
        'message_stream_note': message_stream_note,
        'token_cached': token_cached,
        'token_refresh_status': token_refresh_status,
        'token_refresh_error_message': getattr(live_instance, 'last_token_refresh_error_message', None),
        'token_last_refreshed_at': token_refreshed_at,
        'token_last_refreshed_at_display': _format_runtime_timestamp(token_refreshed_at),
        'token_age_seconds': _get_runtime_age_seconds(token_refreshed_at),
        'session_keepalive_status': session_keepalive_status,
        'session_keepalive_display_status': session_display_status,
        'session_keepalive_display_note': session_display_note,
        'session_keepalive_error_message': getattr(live_instance, 'last_session_keepalive_error_message', None),
        'session_keepalive_at': session_keepalive_at,
        'session_keepalive_at_display': _format_runtime_timestamp(session_keepalive_at),
        'session_keepalive_age_seconds': _get_runtime_age_seconds(session_keepalive_at),
        'session_transport_ready': session_transport_ready,
        'last_heartbeat_response_at': heartbeat_response_at,
        'last_heartbeat_response_at_display': _format_runtime_timestamp(heartbeat_response_at),
        'last_heartbeat_age_seconds': _get_runtime_age_seconds(heartbeat_response_at),
        'last_heartbeat_sent_at': heartbeat_sent_at,
        'last_heartbeat_sent_at_display': _format_runtime_timestamp(heartbeat_sent_at),
        'last_heartbeat_sent_age_seconds': _get_runtime_age_seconds(heartbeat_sent_at),
        'ws_transport_ready': ws_transport_ready,
        'last_business_activity_at': actual_business_activity_at,
        'last_business_activity_at_display': _format_runtime_timestamp(actual_business_activity_at),
        'last_business_activity_age_seconds': _get_runtime_age_seconds(actual_business_activity_at),
        'last_sync_package_at': last_sync_package_at,
        'last_sync_package_at_display': _format_runtime_timestamp(last_sync_package_at),
        'last_sync_package_age_seconds': _get_runtime_age_seconds(last_sync_package_at),
        'last_user_chat_at': last_user_chat_at,
        'last_user_chat_at_display': _format_runtime_timestamp(last_user_chat_at),
        'last_user_chat_age_seconds': _get_runtime_age_seconds(last_user_chat_at),
        'last_stream_watchdog_reconnect_at': last_stream_watchdog_reconnect_at,
        'last_stream_watchdog_reconnect_at_display': _format_runtime_timestamp(last_stream_watchdog_reconnect_at),
        'last_stream_watchdog_reconnect_age_seconds': _get_runtime_age_seconds(last_stream_watchdog_reconnect_at),
        'last_message_received_at': last_message_received_at,
        'last_message_received_at_display': _format_runtime_timestamp(last_message_received_at),
        'last_message_age_seconds': _get_runtime_age_seconds(last_message_received_at),
        'last_successful_connection_at': last_successful_connection_at,
        'last_successful_connection_at_display': _format_runtime_timestamp(last_successful_connection_at),
        'state_last_changed_at': last_state_changed_at,
        'state_last_changed_at_display': _format_runtime_timestamp(last_state_changed_at),
        'cookie_refresh_enabled': getattr(live_instance, 'cookie_refresh_enabled', None),
        'manual_refresh_active': bool(XianyuLive.is_manual_refresh_active(cleaned_cid, allow_handoff_recovery=True)),
        'vnc_manual_action_available': vnc_manual_action_available,
        'manual_browser_session_status': manual_browser_status,
        'manual_browser_reason': manual_browser_reason,
    })
    runtime_status.update(_build_runtime_risk_control_summary(
        token_refresh_status,
        runtime_status.get('token_refresh_error_message'),
        session_keepalive_status,
        runtime_status.get('session_keepalive_error_message'),
    ))
    return runtime_status


async def _run_live_instance_on_manager_loop(
    cookie_id: str,
    coroutine_factory: Callable[[], Awaitable[Any]],
    *,
    timeout: Optional[float] = None,
) -> Any:
    """将运行中账号实例的协程调度回 CookieManager 所属事件循环执行。"""
    manager = getattr(cookie_manager, 'manager', None)
    target_loop = getattr(manager, 'loop', None)
    if not target_loop:
        raise HTTPException(status_code=500, detail="CookieManager 未就绪")

    if hasattr(target_loop, 'is_closed') and target_loop.is_closed():
        raise HTTPException(status_code=500, detail="账号事件循环已关闭")

    try:
        current_loop = asyncio.get_running_loop()
    except RuntimeError:
        current_loop = None

    if current_loop is target_loop:
        return await coroutine_factory()

    if not target_loop.is_running():
        raise HTTPException(status_code=500, detail="账号事件循环未运行")

    thread_future = asyncio.run_coroutine_threadsafe(coroutine_factory(), target_loop)
    wrapped_future = asyncio.wrap_future(thread_future)

    try:
        if timeout and timeout > 0:
            return await asyncio.wait_for(wrapped_future, timeout=timeout)
        return await wrapped_future
    except asyncio.TimeoutError:
        thread_future.cancel()
        raise HTTPException(status_code=504, detail="账号处理超时，请稍后重试")


class CookieAccountInfo(BaseModel):
    """账号信息更新模型"""
    value: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    show_browser: Optional[bool] = None


# ========================= 代理配置相关接口 =========================

class ProxyConfig(BaseModel):
    """代理配置模型"""
    proxy_type: Optional[str] = 'none'  # none/http/https/socks5
    proxy_host: Optional[str] = ''
    proxy_port: Optional[int] = 0
    proxy_user: Optional[str] = ''
    proxy_pass: Optional[str] = ''


# ========================= 账号密码登录相关接口 =========================

def _new_risk_log_session_id(prefix: str = 'risk') -> str:
    return f"{prefix}_{secrets.token_hex(8)}"


def _build_risk_event_meta(base: Optional[Dict[str, Any]] = None, **extra_fields) -> Optional[Dict[str, Any]]:
    payload: Dict[str, Any] = {}
    if isinstance(base, dict):
        payload.update({key: value for key, value in base.items() if value is not None})
    payload.update({key: value for key, value in extra_fields.items() if value is not None})
    return payload or None


def _is_password_login_verification_timeout_message(message: str) -> bool:
    normalized = str(message or '').strip()
    if not normalized:
        return False

    if ('超时' in normalized or '失效' in normalized) and '重新发起验证' in normalized:
        return True

    timeout_markers = (
        '验证超时',
        '二维码已失效',
        '请重新扫码',
    )
    return any(marker in normalized for marker in timeout_markers)


def _derive_password_login_verification_failure_result_code(error_message: str) -> str:
    normalized = str(error_message or '').strip()
    if '二维码' in normalized:
        return 'qr_verify_timed_out' if _is_password_login_verification_timeout_message(normalized) else 'qr_verify_failed'
    if '人脸' in normalized:
        return 'face_verify_timed_out' if _is_password_login_verification_timeout_message(normalized) else 'face_verify_failed'
    if '短信' in normalized:
        return 'sms_verify_timed_out' if _is_password_login_verification_timeout_message(normalized) else 'sms_verify_failed'
    return 'verification_timed_out' if _is_password_login_verification_timeout_message(normalized) else 'verification_failed'


def _update_session_risk_log(
    session_id: str,
    status: str,
    processing_result: str = None,
    error_message: str = None,
    result_code: str = None,
    event_meta: Optional[Dict[str, Any]] = None,
):
    """更新登录会话关联的风控日志状态"""
    try:
        session = password_login_sessions.get(session_id)
        if not session:
            return
        log_id = session.get('risk_control_log_id')
        if not log_id:
            return

        risk_session_id = session.get('risk_session_id') or session_id
        duration_ms = None
        started_at = session.get('timestamp')
        if started_at:
            duration_ms = max(0, int((time.time() - float(started_at)) * 1000))

        if not result_code:
            refresh_mode = bool(session.get('refresh_mode'))
            if status == 'success':
                result_code = 'manual_cookie_refresh_success' if refresh_mode else 'password_login_success'
            elif status == 'failed':
                result_code = 'manual_cookie_refresh_failed' if refresh_mode else 'password_login_failed'

        merged_meta = _build_risk_event_meta(
            {
                'account_id': session.get('account_id'),
                'show_browser': session.get('show_browser'),
                'refresh_mode': bool(session.get('refresh_mode')),
            },
            **(event_meta or {}),
        )

        db_manager.update_risk_control_log(
            log_id=log_id,
            session_id=risk_session_id,
            processing_status=status,
            processing_result=processing_result,
            error_message=error_message,
            result_code=result_code,
            event_meta=merged_meta,
            duration_ms=duration_ms,
        )
    except Exception as e:
        logger.error(f"更新风控日志状态失败: {e}")


def _close_password_login_pending_verification_risk_logs(
    session_id: str,
    status: str,
    error_message: str = None,
    processing_result: str = None,
    result_code: str = None,
    event_meta: Optional[Dict[str, Any]] = None,
) -> int:
    """收口同一账密登录链路下遗留的 processing 验证风控日志。"""
    try:
        session = password_login_sessions.get(session_id)
        if not session:
            return 0

        risk_session_id = session.get('risk_session_id') or session_id
        if not risk_session_id:
            return 0

        with db_manager.lock:
            cursor = db_manager.conn.cursor()
            cursor.execute(
                '''
                SELECT id
                FROM risk_control_logs
                WHERE session_id = ?
                  AND processing_status = 'processing'
                  AND event_type IN ('qr_verify', 'face_verify', 'sms_verify', 'unknown')
                ORDER BY id ASC
                ''',
                (risk_session_id,)
            )
            pending_rows = cursor.fetchall() or []

        if not pending_rows:
            return 0

        duration_ms = None
        started_at = session.get('timestamp')
        if started_at:
            duration_ms = max(0, int((time.time() - float(started_at)) * 1000))

        processing_status = 'success' if str(status or '').strip().lower() == 'success' else 'failed'
        if result_code:
            resolved_result_code = result_code
        elif processing_status == 'success':
            resolved_result_code = 'manual_cookie_refresh_verification_completed' if session.get('refresh_mode') else 'password_login_verification_completed'
        else:
            resolved_result_code = _derive_password_login_verification_failure_result_code(error_message)

        if processing_result is None:
            if processing_status == 'success':
                processing_result = '人工验证已完成，登录流程已成功收尾'
            else:
                processing_result = error_message or '验证流程已结束'

        merged_meta = _build_risk_event_meta(
            {
                'account_id': session.get('account_id'),
                'show_browser': session.get('show_browser'),
                'refresh_mode': bool(session.get('refresh_mode')),
            },
            **(event_meta or {}),
        )

        updated_count = 0
        for row in pending_rows:
            log_id = row[0] if isinstance(row, (tuple, list)) else row
            if not log_id:
                continue
            updated = db_manager.update_risk_control_log(
                log_id=log_id,
                processing_result=processing_result,
                processing_status=processing_status,
                error_message=error_message,
                session_id=risk_session_id,
                trigger_scene='manual_password_refresh' if session.get('refresh_mode') else 'password_login',
                result_code=resolved_result_code,
                event_meta=merged_meta,
                duration_ms=duration_ms,
            )
            if updated:
                updated_count += 1

        return updated_count
    except Exception as e:
        logger.error(f"收口待处理验证风控日志失败: {e}")
        return 0


def _set_password_login_session_status(session_id: str, status: str, **fields):
    session = password_login_sessions.get(session_id)
    if not session:
        return False

    current_status = str(session.get('status') or '').strip().lower()
    next_status = str(status or '').strip().lower()
    if current_status in PASSWORD_LOGIN_TERMINAL_STATUSES and next_status != current_status:
        logger.info(
            f"忽略密码登录会话终态回退: session_id={session_id}, current_status={current_status}, next_status={next_status}"
        )
        return False

    session['status'] = status
    session.update(fields)

    if next_status == 'success':
        session['error'] = None
        session['verification_url'] = None
        session['screenshot_path'] = None
        session['qr_code_url'] = None
        session['verification_type'] = None

    if next_status in PASSWORD_LOGIN_TERMINAL_STATUSES:
        session['completed_at'] = time.time()
    else:
        session['completed_at'] = None

    return True


def _finalize_password_login_session_failure(
    session_id: str,
    error_message: str,
    *,
    result_code: str = None,
    event_meta: Optional[Dict[str, Any]] = None,
) -> bool:
    session = password_login_sessions.get(session_id)
    if not session:
        return False

    extra_fields: Dict[str, Any] = {}
    if _is_password_login_verification_timeout_message(error_message):
        extra_fields.update(
            verification_url=None,
            screenshot_path=None,
            qr_code_url=None,
            verification_type=None,
        )

    _set_password_login_session_status(
        session_id,
        'failed',
        error=error_message,
        **extra_fields,
    )
    _update_session_risk_log(
        session_id,
        'failed',
        error_message=(error_message or '')[:200],
        result_code=result_code,
        event_meta=event_meta,
    )
    _close_password_login_pending_verification_risk_logs(
        session_id,
        'failed',
        error_message=error_message,
        event_meta=event_meta,
    )
    return True


def _get_latest_password_login_session_for_account(
    account_id: str,
    user_id: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    target_account_id = str(account_id)
    matched_sessions = []

    for session in password_login_sessions.values():
        if str(session.get('account_id')) != target_account_id:
            continue
        if user_id is not None and session.get('user_id') != user_id:
            continue
        matched_sessions.append(session)

    if not matched_sessions:
        return None

    return max(
        matched_sessions,
        key=lambda item: (
            float(item.get('timestamp') or 0),
            float(item.get('completed_at') or 0),
        ),
    )


def _is_timed_out_verification_risk_log(log: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(log, dict):
        return False

    result_code = str(log.get('result_code') or '').strip().lower()
    if result_code == 'verification_timed_out' or result_code.endswith('_timed_out'):
        return True

    for field in ('error_message', 'processing_result', 'event_description'):
        if _is_password_login_verification_timeout_message(log.get(field)):
            return True

    return False


def _get_latest_verification_risk_log_for_account(account_id: str) -> Optional[Dict[str, Any]]:
    verification_event_types = {'qr_verify', 'face_verify', 'sms_verify', 'unknown'}
    logs = db_manager.get_risk_control_logs(cookie_id=str(account_id), limit=20)
    for log in logs:
        if str(log.get('event_type') or '').strip() in verification_event_types:
            return log
    return None


def _get_latest_risk_log_epoch_for_account(account_id: str) -> Optional[float]:
    """返回该账号最近一次风控事件（任意类型，含 slider_captcha）的时间戳(epoch秒)。

    用于判断历史验证截图是否已过期：只要有比截图更新的风控事件，
    就说明当前的问题不是那次截图对应的验证（如滑块被风控硬拒时不产生新截图），
    此时不应把旧截图当成待处理验证展示。

    注意：风控日志的 created_at/updated_at 由 SQLite CURRENT_TIMESTAMP 写入（UTC），
    必须用 parse_db_timestamp 按 UTC 解析，否则 Asia/Shanghai 部署会有 8 小时偏差。
    """
    from utils.time_utils import parse_db_timestamp
    logs = db_manager.get_risk_control_logs(cookie_id=str(account_id), limit=5)
    latest = None
    for log in logs:
        raw = log.get('updated_at') or log.get('created_at')
        parsed = parse_db_timestamp(raw)
        if parsed is None:
            continue
        ts = parsed.timestamp()  # parse_db_timestamp 返回 UTC aware datetime，timestamp() 即正确 epoch
        if latest is None or ts > latest:
            latest = ts
    return latest


# 风控事件晚于截图多少秒即判定截图过期（留 60 秒容差，避免同一验证内的时序抖动误判）
_SCREENSHOT_STALE_GAP_SECONDS = 60


def _evaluate_screenshot_freshness(latest_file: str, latest_risk_epoch: Optional[float]) -> Tuple[str, Optional[str]]:
    """判断 glob 到的历史截图是否仍应展示。抽成纯函数便于单测。

    Returns (status, message):
      - ('ok', None)           截图有效，可展示
      - ('stale', msg)         有更新的风控事件，截图已过期
      - ('unavailable', msg)   截图 mtime 读取失败（文件被并发删除等），不可用
    """
    if latest_risk_epoch is None:
        return ('ok', None)
    try:
        screenshot_mtime = os.path.getmtime(latest_file)
    except OSError:
        # 不能默认 0，否则任何近期风控都会把它误判为"过期"；明确报"不可用"
        return ('unavailable', '验证截图读取失败或已被清理，请重新发起验证')
    if latest_risk_epoch > screenshot_mtime + _SCREENSHOT_STALE_GAP_SECONDS:
        return ('stale', '当前没有待处理的验证截图（最近一次风控可能是滑块/Token刷新，已自动处理或需等待风控冷却）')
    return ('ok', None)


def _build_face_verification_screenshot_info(account_id: str, file_path: str) -> Dict[str, Any]:
    from datetime import datetime

    normalized_path = str(file_path or '').replace('\\', '/')
    filename = os.path.basename(normalized_path)
    stat = os.stat(normalized_path)
    return {
        'filename': filename,
        'account_id': account_id,
        'path': f'/static/uploads/images/{filename}',
        'size': stat.st_size,
        'created_time': stat.st_ctime,
        'created_time_str': datetime.fromtimestamp(stat.st_ctime).strftime('%Y-%m-%d %H:%M:%S')
    }


def _set_manual_cookie_import_session_status(session_id: str, status: str, **fields):
    session = manual_cookie_import_sessions.get(session_id)
    if not session:
        return

    session['status'] = status
    session.update(fields)

    if status in {'success', 'failed'}:
        session['completed_at'] = time.time()
    else:
        session['completed_at'] = None


def _empty_slider_session_stats() -> Dict[str, Any]:
    return {
        'has_data': False,
        'total_sessions': 0,
        'total_attempts': 0,
        'success_count': 0,
        'failure_count': 0,
        'processing_count': 0,
        'completed_sessions': 0,
        'success_rate': 0.0,
        'recent_success': None,
        'recent_failure': None,
        'accounts_with_sessions': 0,
        'accounts_with_failures': 0,
        'stats_mode': 'session',
        'summary_text': '暂无滑块验证记录',
        'selected_range': 'all',
        'range_label': '所有',
    }

async def _execute_password_login(session_id: str, account_id: str, account: str, password: str, show_browser: bool, user_id: int, current_user: Dict[str, Any]):
    """后台执行账号密码登录任务"""
    manual_refresh_acquired = False
    manual_refresh_owner = f"password_login:{session_id}"
    auth_recovery_owner = f"manual_password_login:{session_id}"
    auth_recovery_acquired = False
    login_thread_started = False
    manual_refresh_preflight_timeout = 45.0
    request_loop = asyncio.get_running_loop()
    try:
        log_with_user('info', f"开始执行账号密码登录任务: {session_id}, 账号: {account_id}", current_user)

        from XianyuAutoAsync import XianyuLive

        is_refresh_mode = password_login_sessions.get(session_id, {}).get('refresh_mode', False)
        auth_session_state = XianyuLive.begin_auth_recovery_session(
            account_id,
            auth_recovery_owner,
            mode='manual_cookie_refresh' if is_refresh_mode else 'manual_password_login',
            source=manual_refresh_owner,
            force_replace=False,
        )
        auth_recovery_acquired = auth_session_state.get('started', False)
        if auth_session_state.get('already_active'):
            active_owner = auth_session_state.get('active_owner', 'unknown')
            _set_password_login_session_status(
                session_id,
                'failed',
                error=f'该账号已有认证恢复流程进行中，请先完成当前验证或稍后再试（owner={active_owner}）'
            )
            _update_session_risk_log(session_id, 'failed', error_message=f'认证恢复流程进行中: {active_owner}')
            log_with_user('warning', f"账号已有认证恢复流程在执行，拒绝重复触发: {account_id}, owner={active_owner}", current_user)
            return

        if is_refresh_mode:
            manual_refresh_state = XianyuLive.begin_manual_refresh(account_id, source=manual_refresh_owner)
            manual_refresh_acquired = manual_refresh_state.get('started', False)
            if manual_refresh_state.get('already_active'):
                _set_password_login_session_status(
                    session_id,
                    'failed',
                    error='该账号正在执行手动刷新，请稍候再试'
                )
                _update_session_risk_log(session_id, 'failed', error_message='账号正在执行手动刷新')
                log_with_user('warning', f"账号已存在手动刷新任务，拒绝重复触发: {account_id}", current_user)
                return
        
        # 导入 XianyuSliderStealth (slidex)
        XianyuSliderStealth, _, _SlidexConfig, _ = _load_slider_runtime()
        import base64
        import io

        # 创建 XianyuSliderStealth 实例
        existing_cookie_info = db_manager.get_cookie_details(account_id) or {}
        proxy_config = db_manager.get_cookie_proxy_config(account_id)
        _slidex_cfg = _SlidexConfig(
            on_risk_log=lambda **kw: db_manager.add_risk_control_log(**kw),
            on_risk_log_update=lambda **kw: db_manager.update_risk_control_log(**kw),
        )
        slider_instance = _create_slider_instance(
            XianyuSliderStealth,
            user_id=account_id,
            enable_learning=True,
            headless=not show_browser,
            initial_cookies=existing_cookie_info.get('value', ''),
            proxy=proxy_config,
            slidex_config=_slidex_cfg,
        )
        slider_instance.risk_session_id = password_login_sessions.get(session_id, {}).get('risk_session_id') or session_id
        slider_instance.risk_trigger_scene = 'manual_password_refresh' if is_refresh_mode else 'password_login'
        
        # 更新会话信息
        password_login_sessions[session_id]['slider_instance'] = slider_instance
        
        # 定义通知回调函数，用于检测到验证时返回验证链接或截图（同步函数）
        def notification_callback(
            message: str,
            screenshot_path: str = None,
            verification_url: str = None,
            screenshot_path_new: str = None,
            verification_type: str = None,
        ):
            """账号验证通知回调（同步）
            
            Args:
                message: 通知消息
                screenshot_path: 旧版截图路径（兼容参数）
                verification_url: 验证链接
                screenshot_path_new: 新版截图路径（新参数，优先使用）
                verification_type: 验证类型
            """
            try:
                # 优先使用新的截图路径参数
                actual_screenshot_path = screenshot_path_new if screenshot_path_new else screenshot_path
                verification_type_label = resolve_verification_type_label(
                    verification_type,
                    message,
                    verification_url,
                )

                if _is_password_login_verification_timeout_message(message):
                    _finalize_password_login_session_failure(session_id, message)
                    log_with_user('warning', f"密码登录会话检测到失效验证页，直接标记失败: {session_id}", current_user)
                    return
                
                # 优先使用截图路径，如果没有截图则使用验证链接
                if actual_screenshot_path and os.path.exists(actual_screenshot_path):
                    # 更新会话状态，保存截图路径
                    _set_password_login_session_status(
                        session_id,
                        'verification_required',
                        screenshot_path=actual_screenshot_path,
                        verification_url=None,
                        qr_code_url=None,
                        verification_type=verification_type_label,
                    )
                    log_with_user('info', f"账号验证截图已保存: {session_id}, 路径: {actual_screenshot_path}", current_user)
                    
                    # 发送通知到用户配置的渠道
                    def send_face_verification_notification():
                        """在后台线程中发送账号验证通知"""
                        try:
                            log_with_user('info', f"开始尝试发送账号验证通知: {account_id}", current_user)
                            notification_message = build_face_verify_notification(
                                account_id=account_id,
                                time_text=time.strftime('%Y-%m-%d %H:%M:%S'),
                                verification_type=verification_type_label,
                                verification_url=verification_url or '',
                                error_message=message,
                                has_screenshot=True,
                            )
                            notification_sent = dispatch_account_notifications_sync(
                                account_id,
                                notification_message,
                                title='闲鱼账号需要验证',
                                notification_type='face_verify',
                                attachment_path=actual_screenshot_path,
                            )
                            if notification_sent:
                                log_with_user('info', f"✅ 已发送账号验证通知: {account_id}", current_user)
                            else:
                                log_with_user('warning', f"账号验证通知未发送成功: {account_id}", current_user)
                        except Exception as notify_err:
                            log_with_user('error', f"发送账号验证通知时出错: {str(notify_err)}", current_user)
                            import traceback
                            log_with_user('error', f"通知错误详情: {traceback.format_exc()}", current_user)
                    
                    # 在后台线程中发送通知，避免阻塞登录流程
                    import threading
                    notification_thread = threading.Thread(target=send_face_verification_notification)
                    notification_thread.daemon = True
                    notification_thread.start()
                    log_with_user('info', f"已启动账号验证通知发送线程: {account_id}", current_user)
                elif verification_url:
                    # 如果没有截图，使用验证链接（兼容旧版本）
                    _set_password_login_session_status(
                        session_id,
                        'verification_required',
                        verification_url=verification_url,
                        screenshot_path=None,
                        qr_code_url=None,
                        verification_type=verification_type_label,
                    )
                    log_with_user('info', f"账号验证链接已保存: {session_id}, URL: {verification_url}", current_user)
                    
                    # 发送通知到用户配置的渠道
                    def send_face_verification_notification():
                        """在后台线程中发送账号验证通知"""
                        try:
                            log_with_user('info', f"开始尝试发送账号验证通知: {account_id}", current_user)
                            notification_message = build_face_verify_notification(
                                account_id=account_id,
                                time_text=time.strftime('%Y-%m-%d %H:%M:%S'),
                                verification_type=verification_type_label,
                                verification_url=verification_url or '无',
                                error_message=message,
                                has_screenshot=False,
                            )
                            notification_sent = dispatch_account_notifications_sync(
                                account_id,
                                notification_message,
                                title='闲鱼账号需要验证',
                                notification_type='face_verify',
                            )
                            if notification_sent:
                                log_with_user('info', f"✅ 已发送账号验证通知: {account_id}", current_user)
                            else:
                                log_with_user('warning', f"账号验证通知未发送成功: {account_id}", current_user)
                        except Exception as notify_err:
                            log_with_user('error', f"发送账号验证通知时出错: {str(notify_err)}", current_user)
                            import traceback
                            log_with_user('error', f"通知错误详情: {traceback.format_exc()}", current_user)
                    
                    # 在后台线程中发送通知，避免阻塞登录流程
                    import threading
                    notification_thread = threading.Thread(target=send_face_verification_notification)
                    notification_thread.daemon = True
                    notification_thread.start()
                    log_with_user('info', f"已启动账号验证通知发送线程: {account_id}", current_user)
            except Exception as e:
                log_with_user('error', f"处理账号验证通知失败: {str(e)}", current_user)
        
        # 调用登录方法（同步方法，需要在后台线程中执行）
        import threading

        def run_login():
            import asyncio  # 在函数开头导入，避免后续局部import导致UnboundLocalError
            from db_manager import db_manager  # 在函数开头导入，避免作用域问题
            from XianyuAutoAsync import XianyuLive
            try:
                cookies_dict = slider_instance.login_with_password_playwright(
                    account=account,
                    password=password,
                    show_browser=show_browser,
                    notification_callback=notification_callback,
                    force_clean_context=is_refresh_mode
                )
                
                if cookies_dict is None:
                    failure_message = slider_instance.last_login_error or '登录失败，请检查账号密码是否正确'
                    _finalize_password_login_session_failure(session_id, failure_message)
                    log_with_user('error', f"账号密码登录失败: {account_id}, 错误: {failure_message}", current_user)
                    return
                
                log_with_user('info', f"账号密码登录成功，获取到 {len(cookies_dict)} 个Cookie字段: {account_id}", current_user)
                
                # 检查是否已存在相同账号ID的Cookie
                existing_cookies = db_manager.get_all_cookies(user_id)
                is_new_account = account_id not in existing_cookies
                existing_cookie_value = existing_cookies.get(account_id, '') if not is_new_account else ''
                existing_cookie_dict = trans_cookies(existing_cookie_value) if existing_cookie_value else {}

                merge_result = XianyuLive.protected_merge_cookie_dicts(existing_cookie_dict, cookies_dict)
                if merge_result['incoming_missing_protected_fields']:
                    log_with_user(
                        'warning',
                        f"密码登录返回的Cookie快照缺少关键字段，将进行保护性合并: {', '.join(merge_result['incoming_missing_protected_fields'])}",
                        current_user
                    )
                if merge_result['preserved_protected_fields']:
                    log_with_user(
                        'warning',
                        f"密码登录保护性保留旧关键字段: {', '.join(merge_result['preserved_protected_fields'])}",
                        current_user
                    )
                if merge_result['account_switched']:
                    log_with_user('warning', f"检测到unb变化，按账号切换处理: {account_id}", current_user)

                merged_cookies_dict = merge_result['merged_cookies_dict']
                log_with_user(
                    'info',
                    f"manual_login_protected_merge incoming_count={merge_result.get('incoming_count', len(cookies_dict))} "
                    f"existing_count={merge_result.get('existing_count', len(existing_cookie_dict))} "
                    f"merged_count={merge_result.get('merged_count', len(merged_cookies_dict))} "
                    f"protected_preserved_fields={merge_result.get('preserved_protected_fields') or []} "
                    f"would_remove_fields={merge_result.get('would_remove_fields') or []} "
                    f"account_switched={merge_result.get('account_switched', False)}",
                    current_user
                )
                cookies_str = '; '.join([f"{k}={v}" for k, v in merged_cookies_dict.items()])

                if merge_result['missing_required_fields']:
                    missing_fields_text = ', '.join(merge_result['missing_required_fields'])
                    error_message = f"登录成功但Cookie核心字段仍缺失，未覆盖旧Cookie: {missing_fields_text}"
                    log_with_user('error', f"{error_message}: {account_id}", current_user)
                    _finalize_password_login_session_failure(
                        session_id,
                        error_message,
                        result_code='password_login_cookie_incomplete',
                        event_meta={
                            'missing_required_fields': merge_result['missing_required_fields'],
                            'incoming_missing_protected_fields': merge_result['incoming_missing_protected_fields'],
                            'preserved_protected_fields': merge_result['preserved_protected_fields'],
                        },
                    )
                    return

                if is_refresh_mode:
                    try:
                        log_with_user('info', f"刷新模式开始执行Token预检，确认新实例可直接恢复: {account_id}", current_user)
                        XianyuLive.mark_manual_refresh_handoff(account_id, source=manual_refresh_owner)
                        temp_xianyu = XianyuLive(
                            cookies_str=cookies_str,
                            cookie_id=account_id,
                            user_id=user_id,
                            register_instance=False,
                        )
                        preflight_future = asyncio.run_coroutine_threadsafe(
                            temp_xianyu.preflight_token_after_manual_refresh(),
                            request_loop,
                        )
                        try:
                            preflight_future.result(timeout=manual_refresh_preflight_timeout)
                        except concurrent.futures.TimeoutError as timeout_err:
                            preflight_future.cancel()
                            raise TimeoutError(
                                f"手动刷新后的Token预检在 {manual_refresh_preflight_timeout:.0f} 秒内未完成"
                            ) from timeout_err
                        cookies_str = temp_xianyu.cookies_str
                        merged_cookies_dict = trans_cookies(cookies_str)
                        log_with_user('info', f"刷新模式Token预检通过，将使用预检后的Cookie继续交接: {account_id}", current_user)
                    except Exception as preflight_err:
                        error_message = f"刷新模式认证预检失败，任务未切换: {str(preflight_err)}"
                        log_with_user('error', f"{error_message}: {account_id}", current_user)
                        _finalize_password_login_session_failure(
                            session_id,
                            error_message,
                            result_code='manual_refresh_preflight_failed',
                            event_meta={'account_id': account_id},
                        )
                        return
                
                # 保存账号密码和Cookie到数据库
                # 使用 update_cookie_account_info 来保存，它会自动处理新账号和现有账号的情况
                # 注意：刷新模式下不更新 show_browser，避免临时调试选项被永久保存
                update_success = db_manager.update_cookie_account_info(
                    account_id,
                    cookie_value=cookies_str,
                    username=account,
                    password=password,
                    show_browser=show_browser if not is_refresh_mode else None,  # 刷新模式不更新此字段
                    user_id=user_id  # 新账号时需要提供user_id
                )
                
                if update_success:
                    if is_new_account:
                        log_with_user('info', f"新账号Cookie和账号密码已保存: {account_id}", current_user)
                    else:
                        log_with_user('info', f"现有账号Cookie和账号密码已更新: {account_id}", current_user)
                else:
                    log_with_user('error', f"保存账号信息失败: {account_id}", current_user)
                
                # 统一走 CookieManager，确保任务登记、实例切换和运行态一致
                if cookie_manager.manager:
                    if is_new_account:
                        handoff_result = cookie_manager.manager.add_cookie(account_id, cookies_str, user_id=user_id)
                        _consume_cookie_manager_handoff(handoff_result)
                        log_with_user('info', f"已将新账号加入cookie_manager并启动任务: {account_id}", current_user)
                    else:
                        handoff_result = cookie_manager.manager.update_cookie(account_id, cookies_str, save_to_db=False)
                        _consume_cookie_manager_handoff(handoff_result)
                        log_with_user('info', f"已更新cookie_manager并重启任务: {account_id}", current_user)
                
                if is_refresh_mode:
                    log_with_user('info', f"刷新模式已完成Token预检，直接切换到通过预检的新Cookie: {account_id}", current_user)
                else:
                    # 登录成功后，调用_refresh_cookies_via_browser刷新Cookie
                    try:
                        log_with_user('info', f"开始调用_refresh_cookies_via_browser刷新Cookie: {account_id}", current_user)
                        
                        # 创建临时的XianyuLive实例来刷新Cookie
                        temp_xianyu = XianyuLive(
                            cookies_str=cookies_str,
                            cookie_id=account_id,
                            user_id=user_id,
                            register_instance=False,
                        )
                        
                        # 重置扫码登录Cookie刷新标志，确保账号密码登录后能立即刷新
                        try:
                            temp_xianyu.reset_qr_cookie_refresh_flag()
                            log_with_user('info', f"已重置扫码登录Cookie刷新标志: {account_id}", current_user)
                        except Exception as reset_err:
                            log_with_user('debug', f"重置扫码登录Cookie刷新标志失败（不影响刷新）: {str(reset_err)}", current_user)
                        
                        # 在后台异步执行刷新（不阻塞主流程）
                        async def refresh_cookies_task():
                            try:
                                refresh_success = await temp_xianyu._refresh_cookies_via_browser(triggered_by_refresh_token=False)
                                if refresh_success:
                                    log_with_user('info', f"Cookie刷新成功: {account_id}", current_user)
                                    # 刷新成功后，从数据库获取更新后的Cookie
                                    updated_cookie_info = db_manager.get_cookie_details(account_id)
                                    if updated_cookie_info:
                                        refreshed_cookies = updated_cookie_info.get('value', '')
                                        if refreshed_cookies:
                                            # 更新cookie_manager中的Cookie
                                            if cookie_manager.manager:
                                                handoff_result = cookie_manager.manager.update_cookie(
                                                    account_id,
                                                    refreshed_cookies,
                                                    save_to_db=False,
                                                )
                                                _consume_cookie_manager_handoff(handoff_result)
                                            log_with_user('info', f"已更新刷新后的Cookie到cookie_manager: {account_id}", current_user)
                                else:
                                    log_with_user('warning', f"Cookie刷新失败或跳过: {account_id}", current_user)
                            except Exception as refresh_e:
                                log_with_user('error', f"刷新Cookie时出错: {account_id}, 错误: {str(refresh_e)}", current_user)
                                import traceback
                                logger.error(traceback.format_exc())
                        
                        # 在后台线程中运行异步任务
                        # 由于run_login是在线程中运行的，需要创建新的事件循环
                        def run_async_refresh():
                            try:
                                import asyncio
                                # 创建新的事件循环
                                new_loop = asyncio.new_event_loop()
                                asyncio.set_event_loop(new_loop)
                                try:
                                    new_loop.run_until_complete(refresh_cookies_task())
                                finally:
                                    new_loop.close()
                            except Exception as e:
                                log_with_user('error', f"运行异步刷新任务失败: {account_id}, 错误: {str(e)}", current_user)
                        
                        # 在后台线程中执行刷新任务
                        refresh_thread = threading.Thread(target=run_async_refresh, daemon=True)
                        refresh_thread.start()
                        
                    except Exception as refresh_err:
                        log_with_user('warning', f"调用_refresh_cookies_via_browser失败: {account_id}, 错误: {str(refresh_err)}", current_user)
                        # 刷新失败不影响登录成功
                
                # 更新会话状态
                _set_password_login_session_status(
                    session_id,
                    'success',
                    account_id=account_id,
                    is_new_account=is_new_account,
                    cookie_count=len(merged_cookies_dict)
                )
                _close_password_login_pending_verification_risk_logs(
                    session_id,
                    'success',
                    processing_result='人工验证已完成，登录流程已成功收尾',
                )
                # 更新风控日志状态
                _update_session_risk_log(
                    session_id,
                    'success',
                    processing_result='Cookie刷新成功，认证预检通过' if is_refresh_mode else 'Cookie刷新成功'
                )

                # 发送登录成功通知（使用模板系统）
                try:
                    # 根据模式选择不同模板
                    notify_refresh_mode = password_login_sessions[session_id].get('refresh_mode')
                    template_type = 'cookie_refresh_success' if notify_refresh_mode else 'password_login_success'

                    notification_message = render_notification_template(
                        template_type,
                        account_id=account_id,
                        time=time.strftime('%Y-%m-%d %H:%M:%S'),
                        cookie_count=str(len(merged_cookies_dict))
                    )

                    login_type = "刷新Cookie" if notify_refresh_mode else "密码登录"
                    notification_sent = dispatch_account_notifications_sync(
                        account_id,
                        notification_message,
                        title=f"{login_type}成功",
                        notification_type=template_type,
                    )
                    if notification_sent:
                        log_with_user('info', f"已发送{login_type}成功通知: {account_id}", current_user)
                    else:
                        log_with_user('warning', f"{login_type}成功通知未发送成功: {account_id}", current_user)
                except Exception as notify_err:
                    log_with_user('warning', f"发送登录成功通知失败: {account_id}, 错误: {str(notify_err)}", current_user)

                if is_refresh_mode and session_id in password_login_sessions:
                    screenshot_path = password_login_sessions[session_id].get('screenshot_path')
                    verification_url = password_login_sessions[session_id].get('verification_url')
                    verification_type = password_login_sessions[session_id].get('verification_type')
                    if screenshot_path or verification_url:
                        _set_password_login_session_status(
                            session_id,
                            'success',
                            screenshot_path=screenshot_path,
                            verification_url=verification_url,
                            verification_type=verification_type,
                        )
                
            except Exception as e:
                error_msg = str(e)
                _finalize_password_login_session_failure(session_id, error_msg)
                log_with_user('error', f"账号密码登录失败: {account_id}, 错误: {error_msg}", current_user)
                logger.info(f"会话 {session_id} 状态已更新为 failed，错误消息: {error_msg}")  # 添加日志确认状态更新
                import traceback
                logger.error(traceback.format_exc())
            finally:
                # 清理实例（释放并发槽位）
                try:
                    _, _, _, concurrency_manager = _load_slider_runtime()
                    if concurrency_manager.unregister_instance(account_id, slider_instance):
                        log_with_user('debug', f"已释放并发槽位: {account_id}", current_user)
                except Exception as cleanup_e:
                    log_with_user('warning', f"清理实例时出错: {str(cleanup_e)}", current_user)

                if manual_refresh_acquired:
                    try:
                        from XianyuAutoAsync import XianyuLive
                        XianyuLive.end_manual_refresh(account_id, source=manual_refresh_owner)
                        log_with_user('info', f"已结束手动刷新保护: {account_id}", current_user)
                    except Exception as manual_cleanup_e:
                        log_with_user('warning', f"结束手动刷新保护失败: {account_id}, 错误: {str(manual_cleanup_e)}", current_user)

                if auth_recovery_acquired:
                    try:
                        from XianyuAutoAsync import XianyuLive
                        XianyuLive.end_auth_recovery_session(account_id, auth_recovery_owner)
                        log_with_user('info', f"已结束认证恢复单飞锁: {account_id}", current_user)
                    except Exception as auth_cleanup_e:
                        log_with_user('warning', f"结束认证恢复单飞锁失败: {account_id}, 错误: {str(auth_cleanup_e)}", current_user)
        
        # 在后台线程中执行登录
        login_thread = threading.Thread(target=run_login, daemon=True)
        login_thread.start()
        login_thread_started = True
        
    except Exception as e:
        _finalize_password_login_session_failure(session_id, str(e))
        log_with_user('error', f"执行账号密码登录任务异常: {str(e)}", current_user)
        if manual_refresh_acquired and not login_thread_started:
            try:
                from XianyuAutoAsync import XianyuLive
                XianyuLive.end_manual_refresh(account_id, source=manual_refresh_owner)
            except Exception:
                pass
        if auth_recovery_acquired and not login_thread_started:
            try:
                from XianyuAutoAsync import XianyuLive
                XianyuLive.end_auth_recovery_session(account_id, auth_recovery_owner)
            except Exception:
                pass
        import traceback
        logger.error(traceback.format_exc())


async def _execute_manual_cookie_import(
    session_id: str,
    account_id: str,
    cookie_value: str,
    show_browser: bool,
    user_id: int,
    current_user: Dict[str, Any],
):
    try:
        XianyuSliderStealth, probe_cookie_verification_from_cookie, _SlidexConfig, _ = _load_slider_runtime()
        from XianyuAutoAsync import XianyuLive

        existing_cookie_info = db_manager.get_cookie_details(account_id) or {}
        proxy_config = {
            'proxy_type': existing_cookie_info.get('proxy_type', 'none'),
            'proxy_host': existing_cookie_info.get('proxy_host', ''),
            'proxy_port': existing_cookie_info.get('proxy_port', 0),
            'proxy_user': existing_cookie_info.get('proxy_user', ''),
            'proxy_pass': existing_cookie_info.get('proxy_pass', ''),
        }
        _slidex_cfg = _SlidexConfig(
            on_risk_log=lambda **kw: db_manager.add_risk_control_log(**kw),
            on_risk_log_update=lambda **kw: db_manager.update_risk_control_log(**kw),
        )
        slider_instance = _create_slider_instance(
            XianyuSliderStealth,
            user_id=account_id,
            enable_learning=True,
            headless=not show_browser,
            initial_cookies=cookie_value,
            proxy=proxy_config,
            slidex_config=_slidex_cfg,
        )
        manual_cookie_import_sessions[session_id]['slider_instance'] = slider_instance

        def merge_cookie_dicts_for_import(incoming_cookie_dict: Optional[Dict[str, Any]], source_label: str) -> Dict[str, Any]:
            existing_cookie_dict = trans_cookies(cookie_value)
            merge_result = XianyuLive.protected_merge_cookie_dicts(
                existing_cookie_dict,
                incoming_cookie_dict or {},
            )
            if merge_result['incoming_missing_protected_fields']:
                log_with_user(
                    'warning',
                    (
                        f"导入 Cookie {source_label}快照缺少关键字段，执行保护性合并: "
                        f"{', '.join(merge_result['incoming_missing_protected_fields'])}"
                    ),
                    current_user,
                )
            if merge_result['preserved_protected_fields']:
                log_with_user(
                    'warning',
                    f"导入 Cookie 保护性保留旧字段: {', '.join(merge_result['preserved_protected_fields'])}",
                    current_user,
                )
            return merge_result['merged_cookies_dict']

        def persist_manual_cookie_import_success(merged_cookies_dict: Dict[str, Any], source_label: str):
            if not merged_cookies_dict:
                raise ValueError(f"手动导入 Cookie {source_label}后未获取到有效 Cookie")

            cookies_str = '; '.join([f"{k}={v}" for k, v in merged_cookies_dict.items()])
            existing_same_user_cookie = db_manager.get_all_cookies(user_id)
            is_new_account = account_id not in existing_same_user_cookie
            if is_new_account:
                db_manager.save_cookie(account_id, cookies_str, user_id)
                if cookie_manager.manager:
                    handoff_result = cookie_manager.manager.add_cookie(account_id, cookies_str, user_id=user_id)
                    _consume_cookie_manager_handoff(handoff_result)
            else:
                db_manager.update_cookie_account_info(account_id, cookie_value=cookies_str)
                if cookie_manager.manager:
                    if account_id in getattr(cookie_manager.manager, 'cookies', {}):
                        handoff_result = cookie_manager.manager.update_cookie(account_id, cookies_str, save_to_db=False)
                    else:
                        handoff_result = cookie_manager.manager.add_cookie(account_id, cookies_str, user_id=user_id)
                    _consume_cookie_manager_handoff(handoff_result)

            _set_manual_cookie_import_session_status(
                session_id,
                'success',
                account_id=account_id,
                is_new_account=is_new_account,
                cookie_count=len(merged_cookies_dict),
            )
            log_with_user(
                'info',
                (
                    f"手动导入 Cookie {source_label}成功并已保存: "
                    f"{account_id}, cookie_count={len(merged_cookies_dict)}"
                ),
                current_user,
            )

        def notification_callback(
            message: str,
            screenshot_path: str = None,
            verification_url: str = None,
            screenshot_path_new: str = None,
            verification_type: str = None,
        ):
            """手动导入 Cookie 的验证通知回调。"""
            try:
                import threading

                actual_screenshot_path = screenshot_path_new if screenshot_path_new else screenshot_path
                if actual_screenshot_path and not os.path.exists(actual_screenshot_path):
                    actual_screenshot_path = None

                verification_type_label = resolve_verification_type_label(
                    verification_type,
                    message,
                    verification_url,
                )
                _set_manual_cookie_import_session_status(
                    session_id,
                    'verification_required',
                    verification_url=verification_url or None,
                    screenshot_path=actual_screenshot_path,
                    verification_type=verification_type_label,
                )

                if actual_screenshot_path:
                    log_with_user(
                        'info',
                        f"手动导入 Cookie 验证截图已保存: {session_id}, 路径: {actual_screenshot_path}",
                        current_user,
                    )
                elif verification_url:
                    log_with_user(
                        'info',
                        f"手动导入 Cookie 验证链接已保存: {session_id}, URL: {verification_url}",
                        current_user,
                    )
                else:
                    log_with_user(
                        'warning',
                        f"手动导入 Cookie 检测到{verification_type_label}，但未获取到可用的截图或验证链接: {session_id}",
                        current_user,
                    )

                def send_verification_notification():
                    try:
                        notification_message = build_face_verify_notification(
                            account_id=account_id,
                            time_text=time.strftime('%Y-%m-%d %H:%M:%S'),
                            verification_type=verification_type_label,
                            verification_url=verification_url or '',
                            error_message=message,
                            has_screenshot=bool(actual_screenshot_path),
                        )
                        notification_sent = dispatch_account_notifications_sync(
                            account_id,
                            notification_message,
                            title='闲鱼账号需要验证',
                            notification_type='face_verification',
                            attachment_path=actual_screenshot_path,
                        )
                        if notification_sent:
                            log_with_user('info', f"已发送手动导入 Cookie 验证通知: {account_id}", current_user)
                        else:
                            log_with_user('warning', f"手动导入 Cookie 验证通知未发送成功: {account_id}", current_user)
                    except Exception as notify_err:
                        log_with_user(
                            'warning',
                            f"发送手动导入 Cookie 验证通知失败: {account_id}, 错误: {str(notify_err)}",
                            current_user,
                        )

                notification_thread = threading.Thread(target=send_verification_notification, daemon=True)
                notification_thread.start()
            except Exception as callback_err:
                log_with_user(
                    'warning',
                    f"处理手动导入 Cookie 验证回调失败: {account_id}, 错误: {str(callback_err)}",
                    current_user,
                )

        def run_import():
            try:
                probe_result = probe_cookie_verification_from_cookie(cookie_value, proxy_config)
                if probe_result.get('status') == 'cookie_valid':
                    merged_cookies_dict = merge_cookie_dicts_for_import(
                        probe_result.get('session_cookies'),
                        '预检直通',
                    )
                    log_with_user(
                        'info',
                        f"手动导入 Cookie 预检已确认当前 Cookie 直接有效，跳过浏览器验证: {account_id}",
                        current_user,
                    )
                    persist_manual_cookie_import_success(merged_cookies_dict, '预检直通')
                    return

                target_url = probe_result.get('verification_url')
                if not target_url:
                    raise RuntimeError(
                        f"未拿到最新 verification_url: {probe_result.get('payload') or probe_result}"
                    )
                log_with_user('info', f"手动导入 Cookie 已解析 verification_url: {account_id}", current_user)

                success, cookies_dict = slider_instance.run(
                    target_url,
                    notification_callback=notification_callback,
                    notification_scene='手动导入 Cookie',
                )
                if not success or not cookies_dict:
                    failure_message = slider_instance._get_slider_failure_message('滑块验证失败，请稍后重试')
                    _set_manual_cookie_import_session_status(session_id, 'failed', error=failure_message)
                    log_with_user('error', f"手动导入 Cookie 验证失败: {account_id}, 错误: {failure_message}", current_user)
                    return

                merged_cookies_dict = merge_cookie_dicts_for_import(cookies_dict, '浏览器验证')
                persist_manual_cookie_import_success(merged_cookies_dict, '浏览器验证')
            except Exception as exc:
                error_message = str(exc)
                _set_manual_cookie_import_session_status(session_id, 'failed', error=error_message)
                log_with_user('error', f"手动导入 Cookie 执行异常: {account_id}, 错误: {error_message}", current_user)
                import traceback
                logger.error(traceback.format_exc())
            finally:
                try:
                    _, _, _, concurrency_manager = _load_slider_runtime()
                    concurrency_manager.unregister_instance(account_id, slider_instance)
                except Exception:
                    pass

        import threading
        login_thread = threading.Thread(target=run_import, daemon=True)
        login_thread.start()
    except Exception as exc:
        _set_manual_cookie_import_session_status(session_id, 'failed', error=str(exc))
        log_with_user('error', f"执行手动导入 Cookie 任务异常: {str(exc)}", current_user)
        import traceback
        logger.error(traceback.format_exc())


# ========================= 人脸验证截图相关接口 =========================


# ========================= 扫码登录相关接口 =========================


async def _finish_qr_login_after_external_success(
    session_id: str,
    apply_result: Dict[str, Any],
    current_user: Dict[str, Any],
    source_label: str,
) -> Dict[str, Any]:
    """会话已标记 success 后，触发/复用 process_qr_login_cookies 落地账号。

    调用方必须已持有 qr_check_locks[session_id]。
    """
    already = qr_check_processed.get(session_id) or {}
    if already.get('processed') and already.get('account_info') and not already.get('error'):
        return {
            'success': True,
            'status': 'success',
            'message': f'已使用{source_label}完成登录，账号已就绪',
            'unb': apply_result.get('unb'),
            'account_info': already.get('account_info'),
            'already_processed': True,
            **{k: apply_result[k] for k in ('via',) if k in apply_result},
        }

    if already.get('processing') and not already.get('processed'):
        return {
            'success': True,
            'status': 'confirmed',
            'message': f'{source_label}已接收，正在写入账号...',
            'unb': apply_result.get('unb'),
            **{k: apply_result[k] for k in ('via',) if k in apply_result},
        }

    cookies_info = qr_login_manager.get_session_cookies(session_id)
    if not cookies_info:
        return {
            'success': True,
            'status': 'success',
            'message': apply_result.get('message') or '会话已标记成功，请继续轮询',
            'unb': apply_result.get('unb'),
            **{k: apply_result[k] for k in ('via',) if k in apply_result},
        }

    qr_check_processed[session_id] = {
        'processed': False,
        'processing': True,
        'timestamp': time.time(),
    }

    async def _process_external_success_background():
        try:
            account_info = await process_qr_login_cookies(
                cookies_info['cookies'],
                cookies_info['unb'],
                current_user,
            )
            log_with_user(
                'info',
                f"{source_label}账号落地完成: {session_id}, 账号: {account_info.get('account_id', 'unknown')}",
                current_user,
            )
            qr_check_processed[session_id] = {
                'processed': True,
                'processing': False,
                'timestamp': time.time(),
                'account_info': account_info,
            }
        except Exception as bg_e:
            log_with_user('error', f"{source_label}后台落地失败: {bg_e}", current_user)
            qr_check_processed[session_id] = {
                'processed': True,
                'processing': False,
                'timestamp': time.time(),
                'error': str(bg_e),
            }

    asyncio.create_task(_process_external_success_background())
    return {
        'success': True,
        'status': 'confirmed',
        'message': f'已使用{source_label}，正在写入账号...',
        'unb': apply_result.get('unb'),
        **{k: apply_result[k] for k in ('via',) if k in apply_result},
    }


# ========================= 轻量扫码登录(qr_login_lite) =========================

def _cleanup_qr_lite_sessions():
    now = time.time()
    stale = [
        sid for sid, st in qr_lite_sessions.items()
        if st.get('finished') and now - st.get('finished_at', st.get('started_at', now)) > QR_LITE_SESSION_TTL
    ]
    for sid in stale:
        qr_lite_sessions.pop(sid, None)


def _render_qr_data_url(qr_url: str) -> str:
    """把 cv-cat 返回的二维码内容渲染成 data:image/png;base64,..."""
    import qrcode as _qrlib
    img = _qrlib.make(qr_url)
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return 'data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode('ascii')


async def _run_qr_login_lite(session_id: str, current_user: Dict[str, Any]):
    state = qr_lite_sessions.get(session_id)
    if state is None:
        return

    def _on_qr_url(qr_url: str):
        try:
            state['qr_data_url'] = _render_qr_data_url(qr_url)
            state['state'] = 'waiting'
        except Exception as render_e:
            state['error_message'] = f'二维码渲染失败: {render_e}'
            state['state'] = 'error'

    def _on_status(raw: str):
        # cv-cat 内部 qrCodeStatus → 前端可识别的 state
        normalized = (raw or '').strip().upper()
        state['raw_qr_status'] = normalized
        if normalized == 'SCANNED':
            state['state'] = 'scanned'
        elif normalized == 'CONFIRMED':
            state['state'] = 'confirmed'
        elif normalized == 'NEW':
            # NEW 与初始 waiting 同义，避免回退覆盖更靠后的 confirmed
            if state.get('state') in (None, 'pending', 'waiting'):
                state['state'] = 'waiting'
        # EXPIRED / 异常字符串：让 qrcode_login_lite 抛 TimeoutError，由 finally 收口

    try:
        cookies, acct = await asyncio.to_thread(
            qrcode_login_lite,
            poll_interval=3.0,
            timeout=180.0,
            show_qrcode_in_terminal=False,
            on_qr_url=_on_qr_url,
            on_status=_on_status,
        )
        cookie_str = '; '.join(f"{k}={v}" for k, v in cookies.items())
        info = await process_qr_login_cookies(cookie_str, acct.get('unb', ''), current_user)
        merged = {**acct, **(info or {})}
        # process_qr_login_cookies 通常会回填 account_id/cookie_length 等
        state['account_info'] = merged
        handoff_error = _qr_runtime_handoff_error(merged)
        if handoff_error:
            state['state'] = 'error'
            state['error_message'] = handoff_error
        else:
            state['state'] = 'success'
    except TimeoutError as exc:
        state['state'] = 'expired'
        state['error_message'] = str(exc) or '二维码已过期或扫码超时'
        log_with_user('warning', f"轻量扫码登录超时: {exc}", current_user)
    except Exception as exc:
        state['state'] = 'error'
        state['error_message'] = str(exc) or '轻量扫码登录失败'
        log_with_user('error', f"轻量扫码登录异常: {exc}", current_user)
    finally:
        state['finished'] = True
        state['finished_at'] = time.time()


async def process_qr_login_cookies(cookies: str, unb: str, current_user: Dict[str, Any]) -> Dict[str, Any]:
    """处理扫码登录获取的Cookie - 先获取真实cookie再保存到数据库"""
    try:
        user_id = current_user['user_id']

        # 检查是否已存在相同unb的账号
        existing_cookies = db_manager.get_all_cookies(user_id)
        existing_account_id = None
        previous_cookie_value = None

        for account_id, cookie_value in existing_cookies.items():
            try:
                # 解析现有Cookie中的unb
                existing_cookie_dict = trans_cookies(cookie_value)
                if existing_cookie_dict.get('unb') == unb:
                    existing_account_id = account_id
                    previous_cookie_value = cookie_value
                    break
            except:
                continue

        # 确定账号ID
        if existing_account_id:
            account_id = existing_account_id
            is_new_account = False
            log_with_user('info', f"扫码登录找到现有账号: {account_id}, UNB: {unb}", current_user)
        else:
            # 创建新账号，使用unb作为账号ID
            account_id = unb

            # 确保账号ID唯一
            counter = 1
            original_account_id = account_id
            while account_id in existing_cookies:
                account_id = f"{original_account_id}_{counter}"
                counter += 1

            is_new_account = True
            log_with_user('info', f"扫码登录准备创建新账号: {account_id}, UNB: {unb}", current_user)

        # 第一步：使用扫码cookie获取真实cookie
        log_with_user('info', f"开始使用扫码cookie获取真实cookie: {account_id}", current_user)

        # 记录扫码登录到风控日志
        risk_log_id = None
        risk_session_id = _new_risk_log_session_id('qr')
        risk_log_started_at = time.time()
        try:
            risk_log_id = db_manager.add_risk_control_log(
                cookie_id=account_id,
                event_type='cookie_refresh',
                session_id=risk_session_id,
                trigger_scene='qr_login',
                result_code='qr_cookie_refresh_started',
                event_description='扫码登录获取真实Cookie',
                processing_status='processing',
                event_meta=_build_risk_event_meta({
                    'account_id': account_id,
                    'is_new_account': is_new_account,
                })
            )
        except Exception as log_e:
            logger.error(f"记录风控日志失败: {log_e}")

        try:
            # 创建一个临时的XianyuLive实例来执行cookie刷新
            from XianyuAutoAsync import XianyuLive

            # 使用扫码登录的cookie创建临时实例
            temp_instance = XianyuLive(
                cookies_str=cookies,
                cookie_id=account_id,
                user_id=user_id,
                register_instance=False,
            )

            # 执行cookie刷新获取真实cookie
            refresh_success = await temp_instance.refresh_cookies_from_qr_login(
                qr_cookies_str=cookies,
                cookie_id=account_id,
                user_id=user_id
            )

            if refresh_success:
                log_with_user('info', f"扫码登录真实cookie获取成功: {account_id}", current_user)

                # 从数据库获取刚刚保存的真实cookie
                updated_cookie_info = db_manager.get_cookie_by_id(account_id)
                if updated_cookie_info:
                    real_cookies = updated_cookie_info['cookies_str']
                    log_with_user('info', f"已获取真实cookie，长度: {len(real_cookies)}", current_user)

                    qr_login_grace_minutes = max(5, int(RISK_CONTROL.get('qr_login_grace_minutes', 15) or 15))
                    qr_login_grace_until = int(time.time() + (qr_login_grace_minutes * 60))
                    task_restarted = False
                    warning_message = None
                    final_cookies = temp_instance.cookies_str or real_cookies

                    try:
                        if cookie_manager.manager:
                            if is_new_account:
                                handoff_result = cookie_manager.manager.add_cookie(account_id, final_cookies, user_id=user_id)
                                await _await_cookie_manager_handoff(handoff_result)
                                log_with_user('info', f"已将真实cookie添加到cookie_manager: {account_id}", current_user)
                            else:
                                # refresh_cookies_from_qr_login 已经保存到数据库了，这里不需要再保存
                                handoff_result = cookie_manager.manager.update_cookie(account_id, final_cookies, save_to_db=False)
                                await _await_cookie_manager_handoff(handoff_result)
                                log_with_user('info', f"已更新cookie_manager中的真实cookie: {account_id}", current_user)
                            task_restarted = True
                            db_manager.set_cookie_qr_login_grace_until(account_id, qr_login_grace_until)
                            XianyuLive.mark_qr_login_grace(account_id, stage='real_cookie_ready', grace_until=qr_login_grace_until)
                            # 扫码刚拿到全新可信 cookie，立即清掉旧的密码登录失败退避，
                            # 否则 init() 会被旧的 slider_failed/credentials 退避 skip，
                            # 表现为"扫码完成但 WS 起不来"（详见 22:43 / 22:08 那两次链路）。
                            XianyuLive.clear_password_login_failure_backoff(account_id)
                            log_with_user('info', f"扫码成功后已清除密码登录失败退避: {account_id}", current_user)
                            warning_message = f"真实Cookie已获取，账号任务已切换；为降低再次触发风控的概率，将进入 {qr_login_grace_minutes} 分钟稳定期，稳定期内不自动预热Token"
                            log_with_user('warning', f"{warning_message}: {account_id}", current_user)
                        else:
                            warning_message = "真实Cookie已获取，但任务管理器未初始化，未启动账号任务"
                            log_with_user('warning', f"{warning_message}: {account_id}", current_user)
                    except Exception as task_switch_e:
                        db_manager.set_cookie_qr_login_grace_until(account_id, 0)
                        XianyuLive.clear_qr_login_grace(account_id)
                        warning_message = f"真实Cookie已获取，但切换账号任务失败: {str(task_switch_e)}"
                        log_with_user('warning', f"{warning_message}: {account_id}", current_user)

                    if not task_restarted:
                        db_manager.set_cookie_qr_login_grace_until(account_id, 0)
                        XianyuLive.clear_qr_login_grace(account_id)
                        if not warning_message:
                            warning_message = "真实Cookie已获取，但任务管理器未初始化，未启动账号任务"
                            log_with_user('warning', f"{warning_message}: {account_id}", current_user)
                        if is_new_account:
                            db_manager.delete_cookie(account_id)
                            log_with_user('warning', f"扫码登录未完成切换，已删除临时创建的新账号记录: {account_id}", current_user)
                        elif previous_cookie_value:
                            db_manager.update_cookie_account_info(account_id, cookie_value=previous_cookie_value)
                            log_with_user('warning', f"扫码登录未完成切换，已回滚现有账号Cookie: {account_id}", current_user)
                        else:
                            log_with_user('warning', f"扫码登录未完成切换，但未找到可回滚的旧Cookie: {account_id}", current_user)

                    # 更新风控日志状态
                    if risk_log_id:
                        try:
                            if task_restarted:
                                processing_result = '扫码登录真实Cookie获取成功，账号任务已启动'
                                processing_result += f'；已进入 {qr_login_grace_minutes} 分钟稳定期，稳定期内不自动预热Token'
                                db_manager.update_risk_control_log(
                                    log_id=risk_log_id,
                                    processing_status='success',
                                    processing_result=processing_result,
                                    session_id=risk_session_id,
                                    trigger_scene='qr_login',
                                    result_code='qr_cookie_refresh_success',
                                    duration_ms=max(0, int((time.time() - risk_log_started_at) * 1000)),
                                    event_meta=_build_risk_event_meta({
                                        'account_id': account_id,
                                        'is_new_account': is_new_account,
                                        'task_restarted': task_restarted,
                                        'token_prewarmed': False,
                                    })
                                )
                            else:
                                db_manager.update_risk_control_log(
                                    log_id=risk_log_id,
                                    processing_status='failed',
                                    error_message=(warning_message or '账号任务未启动')[:200],
                                    processing_result='扫码登录真实Cookie获取成功，但未切换到新任务',
                                    session_id=risk_session_id,
                                    trigger_scene='qr_login',
                                    result_code='qr_cookie_task_not_started',
                                    duration_ms=max(0, int((time.time() - risk_log_started_at) * 1000)),
                                    event_meta=_build_risk_event_meta({
                                        'account_id': account_id,
                                        'is_new_account': is_new_account,
                                        'task_restarted': task_restarted,
                                        'token_prewarmed': False,
                                    })
                                )
                        except Exception:
                            pass

                    return {
                        'account_id': account_id,
                        'is_new_account': is_new_account,
                        'real_cookie_refreshed': task_restarted,  # 回滚时为 False，成功切换时为 True
                        'cookie_length': len(final_cookies),
                        'token_prewarmed': False,
                        'task_restarted': task_restarted,
                        'warning_message': warning_message
                    }
                else:
                    log_with_user('error', f"无法从数据库获取真实cookie: {account_id}", current_user)
                    if risk_log_id:
                        try:
                            db_manager.update_risk_control_log(
                                log_id=risk_log_id,
                                processing_status='failed',
                                error_message='无法从数据库获取真实cookie',
                                session_id=risk_session_id,
                                trigger_scene='qr_login',
                                result_code='qr_cookie_missing_after_refresh',
                                duration_ms=max(0, int((time.time() - risk_log_started_at) * 1000)),
                                event_meta=_build_risk_event_meta({'account_id': account_id, 'is_new_account': is_new_account})
                            )
                        except Exception:
                            pass
                    # 降级处理：使用原始扫码cookie
                    return await _fallback_save_qr_cookie(account_id, cookies, user_id, is_new_account, current_user, "无法从数据库获取真实cookie")
            else:
                log_with_user('warning', f"扫码登录真实cookie获取失败: {account_id}", current_user)
                if risk_log_id:
                    try:
                        db_manager.update_risk_control_log(
                            log_id=risk_log_id,
                            processing_status='failed',
                            error_message='真实cookie获取失败',
                            session_id=risk_session_id,
                            trigger_scene='qr_login',
                            result_code='qr_cookie_refresh_failed',
                            duration_ms=max(0, int((time.time() - risk_log_started_at) * 1000)),
                            event_meta=_build_risk_event_meta({'account_id': account_id, 'is_new_account': is_new_account})
                        )
                    except Exception:
                        pass
                # 降级处理：使用原始扫码cookie
                return await _fallback_save_qr_cookie(account_id, cookies, user_id, is_new_account, current_user, "真实cookie获取失败")

        except Exception as refresh_e:
            log_with_user('error', f"扫码登录真实cookie获取异常: {str(refresh_e)}", current_user)
            if risk_log_id:
                try:
                    db_manager.update_risk_control_log(
                        log_id=risk_log_id,
                        processing_status='failed',
                        error_message=str(refresh_e)[:200],
                        session_id=risk_session_id,
                        trigger_scene='qr_login',
                        result_code='qr_cookie_refresh_exception',
                        duration_ms=max(0, int((time.time() - risk_log_started_at) * 1000)),
                        event_meta=_build_risk_event_meta({'account_id': account_id, 'is_new_account': is_new_account})
                    )
                except Exception:
                    pass
            # 降级处理：使用原始扫码cookie
            return await _fallback_save_qr_cookie(account_id, cookies, user_id, is_new_account, current_user, f"获取真实cookie异常: {str(refresh_e)}")

    except Exception as e:
        log_with_user('error', f"处理扫码登录Cookie失败: {str(e)}", current_user)
        raise e


async def _fallback_save_qr_cookie(account_id: str, cookies: str, user_id: int, is_new_account: bool, current_user: Dict[str, Any], error_reason: str) -> Dict[str, Any]:
    """降级处理：当无法获取真实cookie时，保存原始扫码cookie"""
    try:
        log_with_user('warning', f"降级处理 - 保存原始扫码cookie: {account_id}, 原因: {error_reason}", current_user)

        # 保存原始扫码cookie到数据库
        if is_new_account:
            db_manager.save_cookie(account_id, cookies, user_id)
            log_with_user('info', f"降级处理 - 新账号原始cookie已保存: {account_id}", current_user)
        else:
            # 现有账号使用 update_cookie_account_info 避免覆盖其他字段
            db_manager.update_cookie_account_info(account_id, cookie_value=cookies)
            log_with_user('info', f"降级处理 - 现有账号原始cookie已更新: {account_id}", current_user)

        # 添加到或更新cookie_manager
        if cookie_manager.manager:
            if is_new_account:
                handoff_result = cookie_manager.manager.add_cookie(account_id, cookies, user_id=user_id)
                _consume_cookie_manager_handoff(handoff_result)
                log_with_user('info', f"降级处理 - 已将原始cookie添加到cookie_manager: {account_id}", current_user)
            else:
                # update_cookie_account_info 已经保存到数据库了，这里不需要再保存
                handoff_result = cookie_manager.manager.update_cookie(account_id, cookies, save_to_db=False)
                _consume_cookie_manager_handoff(handoff_result)
                log_with_user('info', f"降级处理 - 已更新cookie_manager中的原始cookie: {account_id}", current_user)

        return {
            'account_id': account_id,
            'is_new_account': is_new_account,
            'real_cookie_refreshed': False,
            'fallback_reason': error_reason,
            'cookie_length': len(cookies)
        }

    except Exception as fallback_e:
        log_with_user('error', f"降级处理失败: {str(fallback_e)}", current_user)
        raise fallback_e


# ------------------------- 默认回复管理接口 -------------------------


# ------------------------- 通知渠道管理接口 -------------------------


# ------------------------- 消息通知配置接口 -------------------------


# ------------------------- 通知模板接口 -------------------------


class TestNotificationIn(BaseModel):
    template_type: str
    template: str


class NotificationTemplateIn(BaseModel):
    template: str


# ------------------------- 系统设置接口 -------------------------


# ------------------------- 注册设置接口 -------------------------


class RegistrationSettingUpdate(BaseModel):
    enabled: bool


class LoginInfoSettingUpdate(BaseModel):
    enabled: bool


# 公开接口：获取登录验证码是否启用（供登录页面使用）


class AutoConfirmUpdate(BaseModel):
    auto_confirm: bool


class AutoCommentUpdate(BaseModel):
    auto_comment: bool


class AutoCommentBatchRateRequest(BaseModel):
    cookie_ids: Optional[List[str]] = None
    account_ids: Optional[List[str]] = None
    page_size: Optional[int] = 100


class CommentTemplateCreate(BaseModel):
    name: str
    content: str
    is_active: Optional[bool] = False


class CommentTemplateUpdate(BaseModel):
    name: Optional[str] = None
    content: Optional[str] = None
    is_active: Optional[bool] = None


class RemarkUpdate(BaseModel):
    remark: str


class PauseDurationUpdate(BaseModel):
    pause_duration: int


# ==================== 自动好评相关API ====================


class KeywordIn(BaseModel):
    keywords: Dict[str, str]  # key -> reply

class KeywordWithItemIdIn(BaseModel):
    keywords: List[Dict[str, Any]]  # [{"keyword": str, "reply": str, "item_id": str}]


# 卡券管理API


# 自动发货规则API


# ==================== 备份和恢复 API ====================


# ==================== 商品管理 API ====================


# ==================== 商品搜索 API ====================

class ItemSearchRequest(BaseModel):
    keyword: str
    page: int = 1
    page_size: int = 20

class ItemSearchMultipleRequest(BaseModel):
    keyword: str
    total_pages: int = 1


def _parse_optional_non_negative_float(value: Any, field_label: str) -> Optional[float]:
    raw_value = str(value or "").strip()
    if not raw_value:
        return None

    try:
        parsed = float(raw_value)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail=f"{field_label}必须是数字")

    if parsed < 0:
        raise HTTPException(status_code=400, detail=f"{field_label}必须大于等于 0")

    return parsed


def _parse_form_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value

    normalized = str(value or "").strip().lower()
    return normalized in {"1", "true", "yes", "on", "y"}


def _persist_cookie_value_for_account(
    cookie_id: str,
    current_user: Dict[str, Any],
    original_cookie_value: str,
    latest_cookie_value: str,
):
    cleaned_latest = str(latest_cookie_value or "").strip()
    if not cleaned_latest or cleaned_latest == str(original_cookie_value or "").strip():
        return

    db_manager.update_cookie_account_info(
        cookie_id,
        cookie_value=cleaned_latest,
        user_id=current_user["user_id"],
    )
    if cookie_manager.manager is not None:
        handoff_result = cookie_manager.manager.update_cookie(cookie_id, cleaned_latest, save_to_db=False)
        _consume_cookie_manager_handoff(handoff_result)


async def _sync_items_after_publish(
    cookie_id: str,
    cookies_str: str,
    published_item_id: Optional[str] = None,
) -> Dict[str, Any]:
    from XianyuAutoAsync import XianyuLive

    xianyu_instance = XianyuLive(cookies_str, cookie_id, register_instance=False)
    fallback_result = None
    page_sync_result = None
    item_synced = None

    try:
        page_sync_result = await xianyu_instance.get_item_list_info(
            page_number=1,
            page_size=100,
            sync_item_details=True,
        )

        if published_item_id:
            item_synced = bool(db_manager.get_item_info(cookie_id, published_item_id))

        if published_item_id and not item_synced:
            fallback_result = await xianyu_instance.get_all_items(
                page_size=100,
                max_pages=3,
                sync_item_details=True,
            )
            item_synced = bool(db_manager.get_item_info(cookie_id, published_item_id))

        sync_success = bool(page_sync_result and page_sync_result.get("success"))
        fallback_success = bool(fallback_result and fallback_result.get("success"))

        summary_message = "已同步最新商品列表"
        if published_item_id:
            if item_synced:
                summary_message = f"已同步发布商品 {published_item_id}"
            else:
                summary_message = f"已执行同步，但暂未在本地列表确认商品 {published_item_id}"
        elif not sync_success and not fallback_success:
            summary_message = "发布成功，但同步最新商品列表失败"

        return {
            "success": sync_success or fallback_success,
            "message": summary_message,
            "published_item_id": published_item_id,
            "item_synced": item_synced,
            "page_sync": {
                "success": bool(page_sync_result and page_sync_result.get("success")),
                "current_count": int((page_sync_result or {}).get("current_count", 0) or 0),
                "saved_count": int((page_sync_result or {}).get("saved_count", 0) or 0),
                "error": (page_sync_result or {}).get("error"),
            },
            "full_sync": {
                "used": fallback_result is not None,
                "success": bool(fallback_result and fallback_result.get("success")),
                "total_count": int((fallback_result or {}).get("total_count", 0) or 0),
                "total_saved": int((fallback_result or {}).get("total_saved", 0) or 0),
                "error": (fallback_result or {}).get("error"),
            },
        }
    finally:
        await xianyu_instance.close_session()


class ProductMaterialRequest(BaseModel):
    title: str
    description: str
    price: Optional[float] = None
    original_price: Optional[float] = None
    category: Optional[str] = None
    images: List[Any] = []
    delivery_method: str = "包邮"
    postage: Optional[float] = 0
    can_self_pickup: bool = False
    brand: Optional[str] = None
    condition: Optional[str] = "全新"
    remark: Optional[str] = None


class ProductMaterialUpdateRequest(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    price: Optional[float] = None
    original_price: Optional[float] = None
    category: Optional[str] = None
    images: Optional[List[Any]] = None
    delivery_method: Optional[str] = None
    postage: Optional[float] = None
    can_self_pickup: Optional[bool] = None
    brand: Optional[str] = None
    condition: Optional[str] = None
    remark: Optional[str] = None


class ProductBatchPublishRequest(BaseModel):
    account_ids: List[str]
    material_ids: List[int]


class ProductSinglePublishRequest(BaseModel):
    account_id: str
    title: str
    description: str
    price: Optional[float] = None
    original_price: Optional[float] = None
    images: List[Any]
    delivery_method: str = "包邮"
    postage: Optional[float] = 0
    can_self_pickup: bool = False
    category: Optional[str] = None
    brand: Optional[str] = None
    condition: Optional[str] = "全新"
    material_id: Optional[int] = None

PRODUCT_PUBLISH_DELIVERY_CHOICES = {"包邮", "按距离计费", "一口价", "无需邮寄"}
PRODUCT_PUBLISH_MAX_IMAGES = 9
PRODUCT_PUBLISH_MAX_IMAGE_BYTES = 8 * 1024 * 1024  # 单图解码后上限 8MB
PRODUCT_PUBLISH_MAX_BASE64_CHARS = 12 * 1024 * 1024  # Base64 文本上限约 12MB


def _model_to_dict(model: BaseModel, *, exclude_unset: bool = False) -> Dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump(exclude_unset=exclude_unset)
    return model.dict(exclude_unset=exclude_unset)


def _dedupe_str_list(values: List[Any], field_label: str) -> List[str]:
    result: List[str] = []
    for value in values or []:
        text = str(value or '').strip()
        if not text:
            continue
        if text not in result:
            result.append(text)
    if not result:
        raise HTTPException(status_code=400, detail=f"{field_label}不能为空")
    return result


def _dedupe_int_list(values: List[Any], field_label: str) -> List[int]:
    result: List[int] = []
    for value in values or []:
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        if number > 0 and number not in result:
            result.append(number)
    if not result:
        raise HTTPException(status_code=400, detail=f"{field_label}不能为空")
    return result


def _normalize_product_publish_data(data: Dict[str, Any], *, partial: bool = False) -> Dict[str, Any]:
    normalized: Dict[str, Any] = {}

    for field in ('title', 'description', 'category', 'brand', 'condition', 'remark'):
        if field in data or not partial:
            value = data.get(field)
            if value is None:
                normalized[field] = None
            else:
                normalized[field] = str(value).strip()

    if not partial:
        if not normalized.get('title'):
            raise HTTPException(status_code=400, detail="商品标题不能为空")
        if not normalized.get('description'):
            raise HTTPException(status_code=400, detail="商品描述不能为空")
    else:
        if 'title' in normalized and not normalized.get('title'):
            raise HTTPException(status_code=400, detail="商品标题不能为空")
        if 'description' in normalized and not normalized.get('description'):
            raise HTTPException(status_code=400, detail="商品描述不能为空")

    if 'price' in data or not partial:
        normalized['price'] = _parse_optional_non_negative_float(data.get('price'), "现价")
    if 'original_price' in data or not partial:
        normalized['original_price'] = _parse_optional_non_negative_float(data.get('original_price'), "原价")
    if 'postage' in data or not partial:
        normalized['postage'] = _parse_optional_non_negative_float(data.get('postage'), "邮费")

    current_price = normalized.get('price') if 'price' in normalized else data.get('price')
    original_price = normalized.get('original_price') if 'original_price' in normalized else data.get('original_price')
    if original_price is not None and current_price is None:
        raise HTTPException(status_code=400, detail="填写原价时必须同时填写现价")

    if 'delivery_method' in data or not partial:
        delivery_method = str(data.get('delivery_method') or '包邮').strip() or '包邮'
        if delivery_method not in PRODUCT_PUBLISH_DELIVERY_CHOICES:
            raise HTTPException(status_code=400, detail="不支持的运费方式")
        normalized['delivery_method'] = delivery_method
        if delivery_method == '一口价' and normalized.get('postage') is None:
            raise HTTPException(status_code=400, detail="运费方式为一口价时必须填写邮费")

    if 'can_self_pickup' in data or not partial:
        normalized['can_self_pickup'] = _parse_form_bool(data.get('can_self_pickup'))

    if 'images' in data or not partial:
        images = data.get('images') or []
        if not isinstance(images, list):
            raise HTTPException(status_code=400, detail="商品图片必须是数组")
        normalized['images'] = images

    return normalized


def _estimate_base64_bytes(value: str) -> int:
    text = str(value or '').strip()
    if not text:
        return 0
    if ',' in text and text.lower().startswith('data:'):
        text = text.split(',', 1)[1]
    text = re.sub(r'\s+', '', text)
    padding = text.count('=')
    return max(0, (len(text) * 3) // 4 - padding)


def _sanitize_material_images(images: List[Any], *, require_images: bool = True) -> List[Dict[str, Any]]:
    """素材落库前规范化图片：优先保留 URL，限制数量与 Base64 体积。"""
    if not isinstance(images, list):
        raise HTTPException(status_code=400, detail="商品图片必须是数组")
    if require_images and not images:
        raise HTTPException(status_code=400, detail="请至少提供 1 张商品图片")
    if len(images) > PRODUCT_PUBLISH_MAX_IMAGES:
        raise HTTPException(status_code=400, detail=f"单次最多支持 {PRODUCT_PUBLISH_MAX_IMAGES} 张商品图片")

    sanitized: List[Dict[str, Any]] = []
    for index, image in enumerate(images, start=1):
        if not isinstance(image, dict):
            raise HTTPException(status_code=400, detail=f"第 {index} 张图片格式无效")

        url = str(image.get('url') or image.get('image_url') or image.get('src') or '').strip()
        item: Dict[str, Any] = {}
        if url:
            item['url'] = url
            for key in ('width', 'height', 'widthSize', 'heightSize', 'filename', 'name'):
                if image.get(key) is not None:
                    item[key] = image.get(key)
            sanitized.append(item)
            continue

        raw = image.get('content') or image.get('data') or image.get('base64')
        if raw is None:
            raise HTTPException(status_code=400, detail=f"第 {index} 张图片缺少 URL 或 Base64 内容")

        if isinstance(raw, (bytes, bytearray)):
            if len(raw) > PRODUCT_PUBLISH_MAX_IMAGE_BYTES:
                raise HTTPException(status_code=400, detail=f"第 {index} 张图片超过 {PRODUCT_PUBLISH_MAX_IMAGE_BYTES // (1024 * 1024)}MB 限制")
            # 素材库不直接存二进制，要求前端转 data URL / 先上传拿 URL
            raise HTTPException(status_code=400, detail=f"第 {index} 张图片请使用 URL 或 Base64 文本保存到素材")

        raw_text = str(raw).strip()
        if not raw_text:
            raise HTTPException(status_code=400, detail=f"第 {index} 张图片内容为空")
        if len(raw_text) > PRODUCT_PUBLISH_MAX_BASE64_CHARS:
            raise HTTPException(status_code=400, detail=f"第 {index} 张图片 Base64 过大，请先压缩或改用已上传 URL")
        estimated = _estimate_base64_bytes(raw_text)
        if estimated > PRODUCT_PUBLISH_MAX_IMAGE_BYTES:
            raise HTTPException(status_code=400, detail=f"第 {index} 张图片超过 {PRODUCT_PUBLISH_MAX_IMAGE_BYTES // (1024 * 1024)}MB 限制")

        item['data'] = raw_text
        for key in ('filename', 'name', 'type', 'size', 'width', 'height'):
            if image.get(key) is not None:
                item[key] = image.get(key)
        sanitized.append(item)
    return sanitized


def _validate_publish_images(images: List[Any]) -> List[Dict[str, Any]]:
    if not images:
        raise HTTPException(status_code=400, detail="请至少提供 1 张商品图片")
    if len(images) > PRODUCT_PUBLISH_MAX_IMAGES:
        raise HTTPException(status_code=400, detail=f"单次最多支持 {PRODUCT_PUBLISH_MAX_IMAGES} 张商品图片")

    normalized_images = []
    for index, image in enumerate(images, start=1):
        if not isinstance(image, dict):
            raise HTTPException(status_code=400, detail=f"第 {index} 张图片格式无效")
        if not any(image.get(key) for key in ('url', 'image_url', 'src', 'content', 'data', 'base64')):
            raise HTTPException(status_code=400, detail=f"第 {index} 张图片缺少 URL 或 Base64 内容")

        raw = image.get('content') or image.get('data') or image.get('base64')
        if isinstance(raw, str) and raw.strip():
            if len(raw) > PRODUCT_PUBLISH_MAX_BASE64_CHARS:
                raise HTTPException(status_code=400, detail=f"第 {index} 张图片 Base64 过大")
            if _estimate_base64_bytes(raw) > PRODUCT_PUBLISH_MAX_IMAGE_BYTES:
                raise HTTPException(status_code=400, detail=f"第 {index} 张图片超过 {PRODUCT_PUBLISH_MAX_IMAGE_BYTES // (1024 * 1024)}MB 限制")
        elif isinstance(raw, (bytes, bytearray)) and len(raw) > PRODUCT_PUBLISH_MAX_IMAGE_BYTES:
            raise HTTPException(status_code=400, detail=f"第 {index} 张图片超过 {PRODUCT_PUBLISH_MAX_IMAGE_BYTES // (1024 * 1024)}MB 限制")

        normalized_images.append(image)
    return normalized_images


def _summarize_publish_result_for_client(publish_result: Any) -> Dict[str, Any]:
    """发布接口出站摘要，避免把完整上游响应回传前端。"""
    if not isinstance(publish_result, dict):
        return {'preview': str(publish_result)[:500]}
    summary: Dict[str, Any] = {}
    for key in ('ret', 'api', 'v', 'traceId'):
        if key in publish_result:
            summary[key] = publish_result.get(key)
    data = publish_result.get('data')
    if isinstance(data, dict):
        data_summary = {}
        for key in ('itemId', 'item_id', 'id', 'url', 'failMsg', 'errorMsg', 'errorCode'):
            if key in data and data.get(key) is not None:
                data_summary[key] = data.get(key)
        if data_summary:
            summary['data'] = data_summary
    uploaded = publish_result.get('_uploaded_images')
    if isinstance(uploaded, list):
        summary['uploaded_image_count'] = len(uploaded)
    return summary


def _build_published_item_url(item_id: Optional[str]) -> Optional[str]:
    clean_item_id = str(item_id or '').strip()
    if not clean_item_id:
        return None
    return f"https://www.goofish.com/item?id={clean_item_id}"


def _summarize_publish_sync(sync_result: Dict[str, Any]) -> Tuple[str, str, int, int]:
    sync_success = bool(sync_result.get('success'))
    sync_status = 'success' if sync_success else 'failed'
    sync_message = sync_result.get('message') or ('同步成功' if sync_success else '同步失败')

    page_sync = sync_result.get('page_sync') or {}
    full_sync = sync_result.get('full_sync') or {}
    sync_total_count = int(page_sync.get('current_count') or 0)
    sync_saved_count = int(page_sync.get('saved_count') or 0)
    if full_sync.get('used'):
        sync_total_count += int(full_sync.get('total_count') or 0)
        sync_saved_count += int(full_sync.get('total_saved') or 0)

    return sync_status, sync_message, sync_total_count, sync_saved_count


async def _publish_product_to_account(
    *,
    current_user: Dict[str, Any],
    account_id: str,
    title: str,
    description: str,
    images: List[Dict[str, Any]],
    current_price: Optional[float],
    original_price: Optional[float],
    delivery_choice: str,
    post_price: Optional[float],
    can_self_pickup: bool,
    material_id: Optional[int] = None,
    batch_id: Optional[str] = None,
    log_id: Optional[int] = None,
) -> Dict[str, Any]:
    from utils.item_publisher import ItemPublisher

    user_prefix = get_user_log_prefix(current_user)
    cleaned_account_id = _ensure_cookie_access(account_id, current_user)
    cookies_map = _get_user_cookies_map(current_user)
    cookies_str = str(cookies_map.get(cleaned_account_id) or '').strip()
    if not cookies_str:
        raise HTTPException(status_code=400, detail="账号 Cookie 为空，无法发布商品")

    cleaned_title = str(title or '').strip()
    cleaned_description = str(description or '').strip()
    if not cleaned_title:
        raise HTTPException(status_code=400, detail="商品标题不能为空")
    if not cleaned_description:
        raise HTTPException(status_code=400, detail="商品描述不能为空")

    image_payloads = _validate_publish_images(images)
    current_price_value = _parse_optional_non_negative_float(current_price, "现价")
    original_price_value = _parse_optional_non_negative_float(original_price, "原价")
    post_price_value = _parse_optional_non_negative_float(post_price, "邮费")

    if original_price_value is not None and current_price_value is None:
        raise HTTPException(status_code=400, detail="填写原价时必须同时填写现价")
    if delivery_choice not in PRODUCT_PUBLISH_DELIVERY_CHOICES:
        raise HTTPException(status_code=400, detail="不支持的运费方式")
    if delivery_choice == "一口价" and post_price_value is None:
        raise HTTPException(status_code=400, detail="运费方式为一口价时必须填写邮费")

    created_log_id = log_id
    if not created_log_id:
        created_log_id = db_manager.add_publish_log(
            current_user['user_id'],
            cleaned_account_id,
            cleaned_title,
            description=cleaned_description,
            price=str(current_price_value) if current_price_value is not None else None,
            material_id=material_id,
            batch_id=batch_id,
            status='publishing',
        )
    else:
        db_manager.update_publish_log(created_log_id, status='publishing', user_id=current_user['user_id'])

    try:
        logger.info(
            f"{user_prefix} 开始发布商品: cookie_id={cleaned_account_id}, "
            f"title={cleaned_title}, images={len(image_payloads)}, delivery_choice={delivery_choice}"
        )

        proxy_config = db_manager.get_cookie_proxy_config(cleaned_account_id)
        async with ItemPublisher(cookies_str, cleaned_account_id, proxy_config=proxy_config) as publisher:
            publish_result = await publisher.publish_item(
                title=cleaned_title,
                description=cleaned_description,
                images=image_payloads,
                current_price=current_price_value,
                original_price=original_price_value,
                delivery_choice=delivery_choice,
                post_price=post_price_value,
                can_self_pickup=bool(can_self_pickup),
            )
            latest_cookies_str = publisher.cookies_str
            published_item_id = publisher.extract_published_item_id(publish_result)

            if not publisher.is_success_response(publish_result):
                error_message = publisher.extract_error_message(publish_result)
                if created_log_id:
                    db_manager.update_publish_log(
                        created_log_id,
                        status='failed',
                        error_message=error_message,
                        raw_response=publish_result,
                        user_id=current_user['user_id'],
                    )
                raise HTTPException(status_code=400, detail=f"商品发布失败: {error_message}")

        _persist_cookie_value_for_account(
            cleaned_account_id,
            current_user,
            cookies_str,
            latest_cookies_str,
        )

        try:
            sync_result = await _sync_items_after_publish(
                cleaned_account_id,
                latest_cookies_str or cookies_str,
                published_item_id=published_item_id,
            )
        except Exception as sync_exc:
            logger.warning(
                f"{user_prefix} 商品发布成功但同步商品列表失败: "
                f"cookie_id={cleaned_account_id}, error={mask_sensitive_text(sync_exc)}"
            )
            sync_result = {
                "success": False,
                "message": f"发布成功，但同步最新商品列表失败: {str(sync_exc)}",
                "published_item_id": published_item_id,
                "item_synced": False,
                "page_sync": {"success": False, "current_count": 0, "saved_count": 0, "error": str(sync_exc)},
                "full_sync": {"used": False, "success": False, "total_count": 0, "total_saved": 0, "error": None},
            }

        sync_status, sync_message, sync_total_count, sync_saved_count = _summarize_publish_sync(sync_result)
        item_url = _build_published_item_url(published_item_id)

        if created_log_id:
            db_manager.update_publish_log(
                created_log_id,
                status='success',
                item_url=item_url,
                item_id=published_item_id,
                sync_status=sync_status,
                sync_message=sync_message,
                sync_total_count=sync_total_count,
                sync_saved_count=sync_saved_count,
                raw_response=publish_result,
                user_id=current_user['user_id'],
            )

        sync_success = bool(sync_result.get('success'))
        success_message = "商品发布成功"
        if sync_success:
            success_message = "商品发布成功，已同步到商品管理"
        elif sync_result.get('message'):
            success_message = f"商品发布成功，{sync_result['message']}"

        logger.info(
            f"{user_prefix} 商品发布完成: cookie_id={cleaned_account_id}, "
            f"published_item_id={published_item_id or 'unknown'}, sync_success={sync_success}"
        )

        return {
            "success": True,
            "message": success_message,
            "published_item_id": published_item_id,
            "item_url": item_url,
            "log_id": created_log_id,
            "batch_id": batch_id,
            "material_id": material_id,
            "publish_result": _summarize_publish_result_for_client(publish_result),
            "sync_result": sync_result,
        }

    except HTTPException as exc:
        if created_log_id and exc.status_code >= 400:
            db_manager.update_publish_log(created_log_id, status='failed', error_message=str(exc.detail), user_id=current_user['user_id'])
        raise
    except ValueError as exc:
        if created_log_id:
            db_manager.update_publish_log(created_log_id, status='failed', error_message=str(exc), user_id=current_user['user_id'])
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        if created_log_id:
            db_manager.update_publish_log(created_log_id, status='failed', error_message=str(exc), user_id=current_user['user_id'])
        logger.error(f"{user_prefix} 商品发布运行失败: {mask_sensitive_text(exc)}")
        raise HTTPException(status_code=500, detail=str(exc))
    except Exception as exc:
        if created_log_id:
            db_manager.update_publish_log(created_log_id, status='failed', error_message=str(exc), user_id=current_user['user_id'])
        logger.error(f"{user_prefix} 商品发布异常: {mask_sensitive_text(exc)}")
        raise HTTPException(status_code=500, detail=f"商品发布异常: {str(exc)}")


async def _run_product_batch_publish(batch_id: str, jobs: List[Dict[str, Any]], current_user: Dict[str, Any]):
    logger.info(f"{get_user_log_prefix(current_user)} 商品批量发布任务开始: batch_id={batch_id}, total={len(jobs)}")
    for job in jobs:
        material = job.get('material') or {}
        log_id = job.get('log_id')
        account_id = job.get('account_id')
        try:
            await _publish_product_to_account(
                current_user=current_user,
                account_id=account_id,
                title=material.get('title'),
                description=material.get('description'),
                images=material.get('images') or [],
                current_price=material.get('price'),
                original_price=material.get('original_price'),
                delivery_choice=material.get('delivery_method') or '包邮',
                post_price=material.get('postage'),
                can_self_pickup=bool(material.get('can_self_pickup')),
                material_id=material.get('id'),
                batch_id=batch_id,
                log_id=log_id,
            )
        except HTTPException as exc:
            logger.warning(
                f"{get_user_log_prefix(current_user)} 商品批量发布失败: batch_id={batch_id}, "
                f"account_id={account_id}, material_id={material.get('id')}, error={exc.detail}"
            )
        except Exception as exc:
            if log_id:
                db_manager.update_publish_log(log_id, status='failed', error_message=str(exc), user_id=current_user['user_id'])
            logger.error(
                f"{get_user_log_prefix(current_user)} 商品批量发布异常: batch_id={batch_id}, "
                f"account_id={account_id}, material_id={material.get('id')}, error={mask_sensitive_text(exc)}"
            )
    logger.info(f"{get_user_log_prefix(current_user)} 商品批量发布任务结束: batch_id={batch_id}")


class ItemDetailUpdate(BaseModel):
    item_detail: str


class BatchDeleteRequest(BaseModel):
    items: List[dict]  # [{"cookie_id": "xxx", "item_id": "yyy"}, ...]


class AIReplySettings(BaseModel):
    ai_enabled: bool
    model_name: str = "qwen-plus"
    api_key: str = ""
    base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    api_type: str = ""
    max_discount_percent: int = 10
    max_discount_amount: int = 100
    max_bargain_rounds: int = 3
    custom_prompts: str = ""


class AIConfigPreset(BaseModel):
    preset_name: str
    model_name: str
    api_key: str = ""
    base_url: str = ""
    api_type: str = ""


# ==================== AI回复管理API ====================


# ==================== 日志管理API ====================


def _find_first_nested_value(payload: Any, keys: List[str]) -> Any:
    """从闲鱼待评价列表项中尽量提取字段。"""
    if isinstance(payload, dict):
        for key in keys:
            if key in payload and payload[key] not in (None, ''):
                return payload[key]
        for value in payload.values():
            found = _find_first_nested_value(value, keys)
            if found not in (None, ''):
                return found
    elif isinstance(payload, list):
        for value in payload:
            found = _find_first_nested_value(value, keys)
            if found not in (None, ''):
                return found
    return None


def _extract_merchant_rate_order_id(item: Dict[str, Any]) -> str:
    return str(_find_first_nested_value(item, [
        'orderId', 'tradeId', 'bizOrderId', 'biz_order_id', 'order_id', 'trade_id'
    ]) or '').strip()


def _extract_merchant_rate_item_meta(item: Dict[str, Any]) -> Dict[str, str]:
    return {
        'item_id': str(_find_first_nested_value(item, ['itemId', 'item_id', 'auctionId', 'auction_id']) or '').strip(),
        'buyer_id': str(_find_first_nested_value(item, ['buyerId', 'buyer_id', 'buyerUserId', 'userId']) or '').strip(),
        'buyer_nick': str(_find_first_nested_value(item, ['buyerNick', 'buyer_nick', 'buyerName', 'nick', 'userNick']) or '').strip(),
    }


# ==================== 商品管理API ====================


# ------------------------- 用户设置接口 -------------------------


# ------------------------- 管理员专用接口 -------------------------


# ------------------------- 指定商品回复接口 -------------------------


class ItemToDelete(BaseModel):
    cookie_id: str
    item_id: str

class BatchDeleteRequest(BaseModel):
    items: List[ItemToDelete]


# ------------------------- 数据库备份和恢复接口 -------------------------


# ------------------------- 数据管理接口 -------------------------

SENSITIVE_ADMIN_DATA_FIELDS = {
    'password',
    'password_hash',
    'proxy_pass',
    'smtp_password',
    'api_key',
    'secret',
    'token',
    'value',
    'config',
}


def _is_sensitive_admin_data_field(table_name: str, column_name: str) -> bool:
    normalized = str(column_name or '').lower()
    if normalized in SENSITIVE_ADMIN_DATA_FIELDS:
        return True
    if any(part in normalized for part in ('password', 'secret', 'token', 'api_key', 'cookie', 'proxy_pass')):
        return True
    return table_name in {'cookies', 'system_settings', 'ai_reply_settings', 'notification_channels'} and normalized in {'value', 'config'}


def _redact_admin_table_data(table_name: str, data: List[Dict[str, Any]], columns: List[str]) -> List[Dict[str, Any]]:
    redacted_rows = []
    for row in data:
        redacted = {}
        for column in columns:
            value = row.get(column)
            redacted[column] = '***REDACTED***' if value not in (None, '') and _is_sensitive_admin_data_field(table_name, column) else value
        redacted_rows.append(redacted)
    return redacted_rows


# 商品多规格管理API


# 商品多数量发货管理API


# ==================== 订单管理接口 ====================

class OrderHistorySyncRequest(BaseModel):
    cookie_id: Optional[str] = None
    start_date: str
    end_date: str
    max_orders: int = 120
    fetch_details: bool = True


class OrderRecoverRequest(BaseModel):
    cookie_id: str
    order_id: str
    item_id: Optional[str] = None
    buyer_id: Optional[str] = None
    buyer_nick: Optional[str] = None
    sid: Optional[str] = None
    auto_deliver: bool = True


def _normalize_history_optional_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def _normalize_history_amount_text(value: Any) -> Optional[str]:
    text = _normalize_history_optional_text(value)
    if not text:
        return None
    return text if parse_order_amount_value(text) is not None else None


def _create_order_history_sync_job_snapshot(job: Dict[str, Any]) -> Dict[str, Any]:
    return {
        'job_id': job.get('job_id'),
        'status': job.get('status'),
        'message': job.get('message'),
        'error': job.get('error'),
        'created_at': job.get('created_at'),
        'started_at': job.get('started_at'),
        'finished_at': job.get('finished_at'),
        'request': job.get('request'),
        'current_account': job.get('current_account'),
        'current_order_id': job.get('current_order_id'),
        'accounts_total': job.get('accounts_total', 0),
        'accounts_completed': job.get('accounts_completed', 0),
        'orders_discovered': job.get('orders_discovered', 0),
        'orders_processed': job.get('orders_processed', 0),
        'orders_saved': job.get('orders_saved', 0),
        'orders_skipped': job.get('orders_skipped', 0),
        'orders_failed': job.get('orders_failed', 0),
        'matched_orders': job.get('matched_orders', 0),
        'warnings': list(job.get('warnings') or []),
    }


def _append_order_history_sync_warning(job: Dict[str, Any], message: str) -> None:
    warnings = job.setdefault('warnings', [])
    if len(warnings) >= 20:
        return
    warnings.append(str(message))


def _cleanup_order_history_sync_jobs() -> None:
    now_ts = time.time()
    expired_job_ids = []
    for job_id, job in order_history_sync_jobs.items():
        status_value = str(job.get('status') or '')
        finished_ts = job.get('finished_ts') or 0
        if status_value in {'completed', 'failed', 'cancelled'} and finished_ts and (now_ts - finished_ts) > ORDER_HISTORY_SYNC_JOB_RETENTION_SECONDS:
            expired_job_ids.append(job_id)

    for job_id in expired_job_ids:
        order_history_sync_jobs.pop(job_id, None)
        order_history_sync_tasks.pop(job_id, None)


def _save_history_order_candidate(cookie_id: str, candidate: Dict[str, Any]) -> bool:
    order_status = _normalize_history_optional_text(candidate.get('order_status'))
    normalized_status = normalize_order_status_value(order_status) if order_status else None

    return db_manager.insert_or_update_order(
        order_id=str(candidate.get('order_id') or '').strip(),
        item_id=_normalize_history_optional_text(candidate.get('item_id')),
        buyer_id=_normalize_history_optional_text(candidate.get('buyer_id')),
        buyer_nick=_normalize_history_optional_text(candidate.get('buyer_nick')),
        sid=_normalize_history_optional_text(candidate.get('sid')),
        amount=_normalize_history_amount_text(candidate.get('amount')),
        order_status=normalized_status,
        cookie_id=cookie_id,
        platform_created_at=_normalize_history_optional_text(candidate.get('platform_created_at')),
        platform_paid_at=_normalize_history_optional_text(candidate.get('platform_paid_at')),
        platform_completed_at=_normalize_history_optional_text(candidate.get('platform_completed_at')),
    )


def _save_history_order_detail_result(cookie_id: str, candidate: Dict[str, Any], result: Dict[str, Any]) -> bool:
    order_id = _normalize_history_optional_text(result.get('order_id')) or _normalize_history_optional_text(candidate.get('order_id'))
    if not order_id:
        return False

    raw_status = _normalize_history_optional_text(result.get('order_status'))
    normalized_status = normalize_order_status_value(raw_status) if raw_status and raw_status.lower() != 'unknown' else None

    return db_manager.insert_or_update_order(
        order_id=order_id,
        item_id=_normalize_history_optional_text(result.get('item_id')) or _normalize_history_optional_text(candidate.get('item_id')),
        buyer_id=_normalize_history_optional_text(candidate.get('buyer_id')),
        buyer_nick=_normalize_history_optional_text(candidate.get('buyer_nick')),
        sid=_normalize_history_optional_text(candidate.get('sid')),
        spec_name=_normalize_history_optional_text(result.get('spec_name')),
        spec_value=_normalize_history_optional_text(result.get('spec_value')),
        spec_name_2=_normalize_history_optional_text(result.get('spec_name_2')),
        spec_value_2=_normalize_history_optional_text(result.get('spec_value_2')),
        quantity=_normalize_history_optional_text(result.get('quantity')),
        amount=_normalize_history_amount_text(result.get('amount')) or _normalize_history_amount_text(candidate.get('amount')),
        order_status=normalized_status,
        cookie_id=cookie_id,
        platform_created_at=_normalize_history_optional_text(result.get('platform_created_at')) or _normalize_history_optional_text(candidate.get('platform_created_at')),
        platform_paid_at=_normalize_history_optional_text(result.get('platform_paid_at')) or _normalize_history_optional_text(candidate.get('platform_paid_at')),
        platform_completed_at=_normalize_history_optional_text(result.get('platform_completed_at')) or _normalize_history_optional_text(candidate.get('platform_completed_at')),
    )


async def _run_order_history_sync_job(job_id: str) -> None:
    job = order_history_sync_jobs.get(job_id)
    if not job:
        return

    request_data = dict(job.get('request') or {})
    user_info = dict(job.get('user_info') or {})
    current_user_id = user_info.get('user_id')

    from utils.order_history_sync import OrderHistoryPageFetcher, OrderHistorySyncError

    try:
        utc_start = local_date_to_utc_start(request_data.get('start_date'))
        utc_end_exclusive = local_date_to_utc_end_exclusive(request_data.get('end_date'))
        if not utc_start or not utc_end_exclusive:
            raise ValueError('日期格式错误，应为 YYYY-MM-DD')
        if utc_start >= utc_end_exclusive:
            raise ValueError('开始日期必须早于结束日期')

        max_orders = int(request_data.get('max_orders') or 120)
        max_orders = min(max(max_orders, 1), 500)
        fetch_details = bool(request_data.get('fetch_details', True))

        user_cookies = db_manager.get_all_cookies(current_user_id)
        selected_cookie_id = _normalize_history_optional_text(request_data.get('cookie_id'))
        if selected_cookie_id:
            if selected_cookie_id not in user_cookies:
                raise ValueError('指定账号不存在或无权限访问')
            target_cookie_ids = [selected_cookie_id]
        else:
            target_cookie_ids = list(user_cookies.keys())

        if not target_cookie_ids:
            raise ValueError('当前没有可同步的账号')

        _cleanup_order_history_sync_jobs()

        job.update({
            'status': 'running',
            'message': '开始同步历史订单',
            'error': None,
            'started_at': get_local_now().strftime('%Y-%m-%d %H:%M:%S'),
            'accounts_total': len(target_cookie_ids),
            'accounts_completed': 0,
            'orders_discovered': 0,
            'orders_processed': 0,
            'orders_saved': 0,
            'orders_skipped': 0,
            'orders_failed': 0,
            'matched_orders': 0,
            'warnings': [],
        })

        for account_index, cookie_id in enumerate(target_cookie_ids, start=1):
            if job.get('status') == 'cancelled':
                return

            remaining_limit = max_orders - int(job.get('matched_orders') or 0)
            if remaining_limit <= 0:
                break

            cookie_string = user_cookies.get(cookie_id)
            if not cookie_string:
                _append_order_history_sync_warning(job, f'账号 {cookie_id} 缺少 Cookie，已跳过')
                job['accounts_completed'] = account_index
                continue

            job['current_account'] = cookie_id
            job['current_order_id'] = None
            job['message'] = f'正在抓取账号 {cookie_id} 的历史订单列表'

            history_fetcher = OrderHistoryPageFetcher(cookie_string, cookie_id_for_log=cookie_id, headless=True)
            live_instance = cookie_manager.manager.get_xianyu_instance(cookie_id) if cookie_manager.manager else None

            try:
                try:
                    fetch_result = await history_fetcher.fetch_recent_orders(
                        max_orders=remaining_limit,
                        utc_start=utc_start,
                        utc_end_exclusive=utc_end_exclusive,
                    )
                except OrderHistorySyncError as history_exc:
                    logger.warning(
                        f"历史订单列表同步跳过账号: cookie_id={cookie_id}, "
                        f"kind={history_exc.kind}, error={history_exc}"
                    )
                    warning_message = str(history_exc)
                    if history_exc.guidance:
                        warning_message = f'{warning_message}；处理建议：{history_exc.guidance}'
                    _append_order_history_sync_warning(job, warning_message)
                    job['orders_failed'] += 1
                    job['accounts_completed'] = account_index
                    continue

                candidates = list(fetch_result.get('orders') or [])
                scanned_count = int(fetch_result.get('scanned_count') or 0)
                matched_count = int(fetch_result.get('matched_count') or 0)
                out_of_range_count = int(fetch_result.get('out_of_range_count') or 0)

                job['orders_discovered'] += scanned_count
                job['matched_orders'] += matched_count
                job['orders_skipped'] += out_of_range_count

                if live_instance is not None:
                    await history_fetcher.close()

                if job.get('status') == 'cancelled':
                    return

                if not candidates:
                    if scanned_count > 0 and out_of_range_count > 0:
                        _append_order_history_sync_warning(job, f'账号 {cookie_id} 未命中时间范围内的历史订单')
                    else:
                        _append_order_history_sync_warning(job, f'账号 {cookie_id} 未抓到历史订单候选')
                    job['accounts_completed'] = account_index
                    continue

                for candidate in candidates:
                    if job.get('status') == 'cancelled':
                        return

                    order_id = _normalize_history_optional_text(candidate.get('order_id'))
                    if not order_id:
                        continue

                    job['current_order_id'] = order_id
                    job['orders_processed'] += 1
                    job['message'] = f'正在同步账号 {cookie_id} 的订单 {order_id}'

                    detail_saved = False
                    detail_result = None

                    if fetch_details:
                        try:
                            if live_instance is not None:
                                detail_result = await live_instance.fetch_order_detail_info(
                                    order_id=order_id,
                                    item_id=_normalize_history_optional_text(candidate.get('item_id')),
                                    buyer_id=_normalize_history_optional_text(candidate.get('buyer_id')),
                                    sid=_normalize_history_optional_text(candidate.get('sid')),
                                    force_refresh=True,
                                    buyer_nick=_normalize_history_optional_text(candidate.get('buyer_nick')),
                                    buyer_id_source='history_sync',
                                )
                                detail_saved = bool(detail_result)
                            else:
                                detail_result = await history_fetcher.fetch_order_detail(order_id, force_refresh=True)
                                if detail_result:
                                    detail_saved = _save_history_order_detail_result(cookie_id, candidate, detail_result)
                        except Exception as sync_exc:
                            logger.warning(f"历史订单详情同步失败: cookie_id={cookie_id}, order_id={order_id}, error={sync_exc}")
                            _append_order_history_sync_warning(job, f'订单 {order_id} 详情刷新失败: {sync_exc}')

                    if not fetch_details or not detail_saved:
                        if _save_history_order_candidate(cookie_id, candidate):
                            detail_saved = True
                        else:
                            _append_order_history_sync_warning(job, f'订单 {order_id} 基础信息写库失败')

                    if detail_saved:
                        job['orders_saved'] += 1
                    else:
                        job['orders_skipped'] += 1
                        job['orders_failed'] += 1

                job['accounts_completed'] = account_index
            finally:
                await history_fetcher.close()

        job['status'] = 'completed'
        job['message'] = (
            f"历史订单同步完成，共扫描 {job.get('orders_discovered', 0)} 单，"
            f"命中时间范围 {job.get('matched_orders', 0)} 单，入库/更新 {job.get('orders_saved', 0)} 单"
        )
    except asyncio.CancelledError:
        logger.info(f"历史订单同步任务已取消: {job_id}")
        job['status'] = 'cancelled'
        job['error'] = None
        job['message'] = job.get('message') or '历史订单同步已取消'
    except Exception as exc:
        logger.error(f"历史订单同步任务失败: {exc}")
        job['status'] = 'failed'
        job['error'] = str(exc)
        job['message'] = f'历史订单同步失败: {exc}'
    finally:
        job['current_order_id'] = None
        job['current_account'] = None
        job['finished_at'] = get_local_now().strftime('%Y-%m-%d %H:%M:%S')
        job['finished_ts'] = time.time()


# ------------------------- 黑名单接口 -------------------------


# ==================== 自动更新接口 ====================

from auto_updater import get_updater, UpdateStatus, init_updater
from pydantic import BaseModel as PydanticBaseModel

class UpdateCheckResponse(PydanticBaseModel):
    """更新检查响应"""
    has_update: bool
    current_version: str
    new_version: str = ""
    description: str = ""
    changelog: list = []
    files_count: int = 0
    total_size: int = 0
    release_date: str = ""


class UpdateProgressResponse(PydanticBaseModel):
    """更新进度响应"""
    status: str
    current_file: str = ""
    current_index: int = 0
    total_files: int = 0
    downloaded_bytes: int = 0
    total_bytes: int = 0
    message: str = ""
    error: str = ""


class UpdateResultResponse(PydanticBaseModel):
    """更新结果响应"""
    success: bool
    message: str
    updated_files: list = []
    deleted_files: list = []
    needs_restart: bool = False
    new_version: str = ""


def _ensure_update_admin(current_user: Dict[str, Any]) -> None:
    """Update-management APIs accept both current and legacy admin markers."""
    if not (current_user.get('is_admin', False) or current_user.get('username') == ADMIN_USERNAME):
        raise HTTPException(status_code=403, detail="只有管理员可以执行更新管理操作")


# ==================== 一键擦亮API ====================


# ==================== 定时任务管理API ====================

def _parse_enabled_flag(value):
    """将不同类型的 enabled 入参统一转换为 0/1"""
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, (int, float)):
        return 1 if int(value) else 0
    if isinstance(value, str):
        return 1 if value.strip().lower() in {'1', 'true', 'yes', 'on'} else 0
    return 1 if value else 0


def _parse_run_hour(value, default=8):
    run_hour = default if value is None else int(value)
    if run_hour < 0 or run_hour > 23:
        raise ValueError("运行时间必须在 0-23 之间")
    return run_hour


def _parse_random_delay(value, default=10):
    random_delay_max = default if value is None else int(value)
    if random_delay_max < 0:
        raise ValueError("随机分钟不能小于 0")
    return random_delay_max


# ==================== 定时任务调度器 ====================

async def scheduled_task_checker():
    """每60秒检查并执行到期的定时任务"""
    while True:
        try:
            due_tasks = db_manager.get_due_tasks()
            for task in due_tasks:
                try:
                    account_id = task['account_id']
                    task_id = task['id']
                    task_type = task['task_type']

                    logger.info(f"执行定时任务: {task['name']} (ID: {task_id}, 账号: {account_id})")

                    if task_type == 'item_polish':
                        cookie_info = db_manager.get_cookie_by_id(account_id)
                        if not cookie_info:
                            logger.warning(f"定时任务 {task_id} 账号 {account_id} 不存在，跳过")
                            result = {"success": False, "message": "账号不存在"}
                        else:
                            cookies_str = cookie_info.get('cookies_str', '')
                            if not cookies_str:
                                result = {"success": False, "message": "账号cookie为空"}
                            else:
                                from XianyuAutoAsync import XianyuLive
                                xianyu_instance = XianyuLive(cookies_str, account_id, register_instance=False)
                                result = await xianyu_instance.polish_all_items()
                                await xianyu_instance.close_session()
                    else:
                        result = {"success": False, "message": f"未知任务类型: {task_type}"}

                    run_hour = task.get('delay_minutes', 8)  # delay_minutes 复用为每日运行小时
                    random_max = task.get('random_delay_max', 10)
                    next_run_str = db_manager.calculate_next_daily_run(
                        run_hour,
                        random_max,
                        include_today=False
                    )

                    db_manager.update_task_run_result(task_id, result, next_run_str)
                    logger.info(f"定时任务 {task_id} 执行完毕，下次运行: {next_run_str}")

                except Exception as e:
                    logger.error(f"执行定时任务 {task.get('id')} 异常: {str(e)}")
        except Exception as e:
            logger.error(f"定时任务检查异常: {str(e)}")
        await asyncio.sleep(60)


# ==================== 文件下载服务 API ====================


# ==================== ?????? ====================

class ClientErrorRequest(BaseModel):
    message: str = ""
    source: str = ""
    lineno: int = 0
    colno: int = 0
    error: str = ""
    url: str = ""
    userAgent: str = ""


# ==================== 下载 Token 机制 ====================


# ==================== 用户组管理 API ====================

class CreateGroupRequest(BaseModel):
    group_name: str
    description: Optional[str] = ""
    user_count: int = 5

class AddMembersRequest(BaseModel):
    count: int = 5


# 移除自动启动，由Start.py或手动启动
# if __name__ == "__main__":
#     uvicorn.run(app, host="0.0.0.0", port=8080)

# ========================= Strangler Fig P1: 已拆分路由域注册（app/api/routers/）=========================
# 置于模块末尾：factory 装饰期需解析全部模块级符号（如 CookieIn）。
app.include_router(
    create_login_router(
        ctx=ctx,
        session_service=session_service,
        security=security,
        verify_dependency=verify_token,
        admin_username=ADMIN_USERNAME,
    )
)
app.include_router(create_cookies_router(ctx=ctx))
app.include_router(create_orders_chat_router(ctx=ctx))
app.include_router(create_admin_ops_router(ctx=ctx))
app.include_router(create_trading_router(ctx=ctx))
app.include_router(create_account_login_router(ctx=ctx))
app.include_router(create_keywords_router(ctx=ctx))
app.include_router(create_notifications_router(ctx=ctx))
app.include_router(create_settings_router(ctx=ctx))
