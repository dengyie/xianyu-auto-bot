"""Shared API-layer state (P1 closeout: real home, was the ApiContext proxy).

reply_server imports these names back at module scope, so runtime rebinds on
either module attribute surface stay visible to routers that read `state.X`
at call time.
"""

import asyncio
import os
from collections import defaultdict
from typing import Any, Dict

from app.application.auth.sessions import SessionService


SESSION_TOKENS = {}  # 存储会话token: {token: {'user_id': int, 'username': str, 'timestamp': float}}

DOWNLOAD_TOKENS = {}  # 下载一次性token: {token_str: {user_id, file_id, exp}}

TOKEN_EXPIRE_TIME = 24 * 60 * 60  # token过期时间：24小时

session_service = SessionService(SESSION_TOKENS, TOKEN_EXPIRE_TIME)

# 扫码登录检查锁 - 防止并发处理同一个session
qr_check_locks = defaultdict(lambda: asyncio.Lock())

qr_check_processed = {}  # 记录已处理的session: {session_id: {'processed': bool, 'timestamp': float}}

login_ip_tracker = {}

login_user_tracker = {}

ip_blacklist = set()

username_rate_tracker: dict = {}

captcha_storage = {}

order_history_sync_jobs: Dict[str, Dict[str, Any]] = {}

order_history_sync_tasks: Dict[str, asyncio.Task] = {}

password_login_sessions = {}  # {session_id: {'account_id','account','show_browser','status','verification_url','qr_code_url','slider_instance','task','timestamp'}}

manual_cookie_import_sessions = {}  # {session_id: {'account_id','status','verification_url','screenshot_path','slider_instance','task','timestamp'}}

# 轻量扫码登录会话表: state ∈ pending|waiting|success|error|expired
qr_lite_sessions: Dict[str, Dict[str, Any]] = {}

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

STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'static')
