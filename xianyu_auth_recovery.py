"""XianyuLive 认证恢复状态机（自 XianyuAutoAsync.py 拆出，P2-x 步骤③）。

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





class ConnectionState(Enum):
    """WebSocket连接状态枚举"""
    DISCONNECTED = "disconnected"  # 未连接
    CONNECTING = "connecting"  # 连接中
    CONNECTED = "connected"  # 已连接
    RECONNECTING = "reconnecting"  # 重连中
    FAILED = "failed"  # 连接失败
    CLOSED = "closed"  # 已关闭


MANUAL_VERIFICATION_CONTEXTS = {
    'manual_password_login',
    'manual_cookie_refresh',
    'manual_refresh',
}



class XianyuAuthRecoveryMixin:
    """认证恢复 / 风控暂停 / 夜间模式行为集（状态容器在宿主 XianyuLive 上）。"""

    @classmethod
    def _cleanup_auth_prewarmed_tokens(cls):
        """清理过期的通用预热 token 缓存。"""
        now = time.time()
        expired_cookie_ids = [
            cookie_id
            for cookie_id, token_info in cls._auth_prewarmed_tokens.items()
            if now - token_info.get('timestamp', 0) > cls._auth_prewarmed_token_ttl
        ]
        for cookie_id in expired_cookie_ids:
            cls._auth_prewarmed_tokens.pop(cookie_id, None)


    @classmethod
    def cache_auth_prewarmed_token(cls, cookie_id: str, token: str, source: str = 'generic_auth'):
        """缓存预检成功后的 token，供新实例首轮初始化复用。"""
        if not cookie_id or not token:
            return
        cls._cleanup_auth_prewarmed_tokens()
        cls._auth_prewarmed_tokens[cookie_id] = {
            'token': token,
            'timestamp': time.time(),
            'source': source,
        }


    @classmethod
    def pop_auth_prewarmed_token(cls, cookie_id: str) -> Optional[Dict[str, Any]]:
        """弹出通用预热 token，过期则忽略。"""
        if not cookie_id:
            return None
        cls._cleanup_auth_prewarmed_tokens()
        token_info = cls._auth_prewarmed_tokens.pop(cookie_id, None)
        if not token_info:
            return None
        if time.time() - token_info.get('timestamp', 0) > cls._auth_prewarmed_token_ttl:
            return None
        return token_info


    @classmethod
    def clear_auth_prewarmed_token(cls, cookie_id: str):
        if not cookie_id:
            return
        cls._auth_prewarmed_tokens.pop(cookie_id, None)


    @classmethod
    def _cleanup_manual_refresh_state(cls):
        """清理过期的刷新交接恢复状态。"""
        now = time.time()
        expired_cookie_ids = []
        with cls._manual_refresh_lock:
            for cookie_id, state in cls._manual_refresh_state.items():
                if state.get('phase') != 'handoff_recovery':
                    continue
                expires_at = state.get('expires_at', 0)
                if expires_at and now > expires_at:
                    expired_cookie_ids.append(cookie_id)

            for cookie_id in expired_cookie_ids:
                cls._manual_refresh_state.pop(cookie_id, None)

        for cookie_id in expired_cookie_ids:
            logger.warning(f"【{cookie_id}】刷新交接恢复状态已过期，自动清理")


    @classmethod
    def get_manual_refresh_state(cls, cookie_id: str) -> Optional[Dict[str, Any]]:
        if not cookie_id:
            return None
        cls._cleanup_manual_refresh_state()
        with cls._manual_refresh_lock:
            state = cls._manual_refresh_state.get(cookie_id)
            return dict(state) if state else None


    @classmethod
    def mark_manual_refresh_handoff(cls, cookie_id: str, source: str = 'manual_refresh_handoff', ttl: int = None) -> Dict[str, Any]:
        """将手动刷新状态切换为交接恢复窗口，允许新实例做初始化恢复。"""
        if not cookie_id:
            return {'updated': False, 'reason': 'empty_cookie_id'}

        live_instance = cls.get_instance(cookie_id)
        previous_cookie_refresh_enabled = None
        if live_instance is not None:
            previous_cookie_refresh_enabled = live_instance.cookie_refresh_enabled

        now = time.time()
        expires_at = now + (ttl or cls._manual_refresh_handoff_ttl)
        with cls._manual_refresh_lock:
            state = cls._manual_refresh_state.get(cookie_id) or {}
            state.update({
                'source': source,
                'phase': 'handoff_recovery',
                'started_at': state.get('started_at', now),
                'updated_at': now,
                'handoff_started_at': now,
                'expires_at': expires_at,
                'slider_failed_bypass_used': state.get('slider_failed_bypass_used', False),
                'previous_cookie_refresh_enabled': state.get('previous_cookie_refresh_enabled', previous_cookie_refresh_enabled),
            })
            cls._manual_refresh_state[cookie_id] = state

        logger.warning(
            f"【{cookie_id}】已进入刷新交接恢复窗口，允许新实例执行初始化恢复 (有效期 {int(expires_at - now)} 秒)"
        )
        return {'updated': True, 'phase': 'handoff_recovery', 'expires_at': expires_at}


    @classmethod
    def consume_manual_refresh_slider_failed_bypass(cls, cookie_id: str) -> bool:
        if not cookie_id:
            return False
        cls._cleanup_manual_refresh_state()
        with cls._manual_refresh_lock:
            state = cls._manual_refresh_state.get(cookie_id)
            if not state or state.get('phase') != 'handoff_recovery':
                return False
            if state.get('slider_failed_bypass_used'):
                return False
            state['slider_failed_bypass_used'] = True
            state['updated_at'] = time.time()
            return True


    @classmethod
    def _cleanup_auth_recovery_locks(cls):
        now = time.time()
        expired_cookie_ids = []
        with cls._auth_recovery_lock:
            for cookie_id, state in cls._auth_recovery_locks.items():
                if now > state.get('expires_at', 0):
                    expired_cookie_ids.append(cookie_id)
            for cookie_id in expired_cookie_ids:
                cls._auth_recovery_locks.pop(cookie_id, None)


    @classmethod
    def acquire_auth_recovery_lock(cls, cookie_id: str, owner: str, ttl: int = None) -> Tuple[bool, Optional[Dict[str, Any]]]:
        if not cookie_id or not owner:
            return False, None
        cls._cleanup_auth_recovery_locks()
        now = time.time()
        expires_at = now + (ttl or cls._auth_recovery_lock_ttl)
        with cls._auth_recovery_lock:
            existing = cls._auth_recovery_locks.get(cookie_id)
            if existing and existing.get('owner') != owner and now <= existing.get('expires_at', 0):
                return False, dict(existing)
            cls._auth_recovery_locks[cookie_id] = {
                'owner': owner,
                'acquired_at': now,
                'expires_at': expires_at,
            }
        return True, None


    @classmethod
    def get_auth_recovery_lock_state(cls, cookie_id: str) -> Optional[Dict[str, Any]]:
        if not cookie_id:
            return None
        cls._cleanup_auth_recovery_locks()
        with cls._auth_recovery_lock:
            state = cls._auth_recovery_locks.get(cookie_id)
            return dict(state) if state else None


    @classmethod
    def release_auth_recovery_lock(cls, cookie_id: str, owner: str = None):
        if not cookie_id:
            return
        with cls._auth_recovery_lock:
            existing = cls._auth_recovery_locks.get(cookie_id)
            if not existing:
                return
            if owner and existing.get('owner') != owner:
                return
            cls._auth_recovery_locks.pop(cookie_id, None)


    @classmethod
    def get_init_auth_failure_state(cls, cookie_id: str) -> Optional[Dict[str, Any]]:
        if not cookie_id:
            return None
        with cls._init_auth_failure_lock:
            state = cls._init_auth_failure_state.get(cookie_id)
            if not state:
                return None
            if state.get('circuit_until') and time.time() > state.get('circuit_until', 0):
                state = {
                    'count': 0,
                    'window_started_at': 0,
                    'last_failure_at': state.get('last_failure_at', 0),
                    'last_reason': state.get('last_reason'),
                    'circuit_until': 0,
                }
                cls._init_auth_failure_state[cookie_id] = state
            return dict(state)


    @classmethod
    def record_init_auth_failure(cls, cookie_id: str, reason: str) -> Dict[str, Any]:
        now = time.time()
        with cls._init_auth_failure_lock:
            state = cls._init_auth_failure_state.get(cookie_id) or {
                'count': 0,
                'window_started_at': now,
                'last_failure_at': 0,
                'last_reason': '',
                'circuit_until': 0,
            }
            window_started_at = state.get('window_started_at', 0)
            if not window_started_at or (now - window_started_at) > cls._init_auth_failure_window:
                state['count'] = 0
                state['window_started_at'] = now
                state['circuit_until'] = 0

            state['count'] = int(state.get('count', 0)) + 1
            state['last_failure_at'] = now
            state['last_reason'] = str(reason or '')
            if state['count'] >= cls._init_auth_failure_threshold:
                state['circuit_until'] = now + cls._init_auth_cooldown

            cls._init_auth_failure_state[cookie_id] = state
            return dict(state)


    @classmethod
    def clear_init_auth_failure_state(cls, cookie_id: str):
        if not cookie_id:
            return
        with cls._init_auth_failure_lock:
            cls._init_auth_failure_state.pop(cookie_id, None)


    @classmethod
    def _cleanup_qr_login_grace_state(cls):
        """清理过期的扫码登录缓冲状态"""
        now = time.time()
        expired_cookie_ids = [
            cookie_id
            for cookie_id, state in cls._qr_login_grace_state.items()
            if now - state.get('timestamp', 0) > cls._qr_login_grace_ttl
        ]
        for cookie_id in expired_cookie_ids:
            cls._qr_login_grace_state.pop(cookie_id, None)


    @classmethod
    def mark_qr_login_grace(cls, cookie_id: str, **extra_state):
        """标记账号刚完成扫码登录，后续首轮 token 刷新可走更保守的缓冲分支"""
        if not cookie_id:
            return
        cls._cleanup_qr_login_grace_state()
        state = {
            'timestamp': time.time(),
            'captcha_buffer_used': False,
            'browser_stabilized': False,
        }
        state.update(extra_state)
        cls._qr_login_grace_state[cookie_id] = state


    @classmethod
    def get_qr_login_grace_ttl_seconds(cls) -> int:
        return max(300, int(RISK_CONTROL.get('qr_login_grace_minutes', 15) or 15) * 60)


    @classmethod
    def get_qr_login_grace(cls, cookie_id: str) -> Optional[Dict[str, Any]]:
        """获取扫码登录缓冲状态，过期则自动忽略"""
        if not cookie_id:
            return None
        cls._cleanup_qr_login_grace_state()
        state = cls._qr_login_grace_state.get(cookie_id)
        if not state:
            return None
        if time.time() - state.get('timestamp', 0) > cls._qr_login_grace_ttl:
            cls._qr_login_grace_state.pop(cookie_id, None)
            return None
        return state


    @classmethod
    def update_qr_login_grace(cls, cookie_id: str, **updates):
        """更新扫码登录缓冲状态"""
        state = cls.get_qr_login_grace(cookie_id)
        if not state:
            return None
        state.update(updates)
        cls._qr_login_grace_state[cookie_id] = state
        return state


    @classmethod
    def clear_qr_login_grace(cls, cookie_id: str):
        """清理指定账号的扫码登录缓冲状态"""
        if not cookie_id:
            return
        cls._qr_login_grace_state.pop(cookie_id, None)


    def _get_qr_login_grace_until(self) -> int:
        try:
            account_info = db_manager.get_cookie_details(self.cookie_id) or {}
            return int(account_info.get('qr_login_grace_until') or 0)
        except Exception as e:
            logger.warning(f"【{self.cookie_id}】读取扫码稳定期截止时间失败: {self._safe_str(e)}")
            return 0


    def _get_qr_login_grace_remaining_seconds(self, current_time: Optional[float] = None) -> int:
        current_time = current_time or time.time()
        grace_until = self._get_qr_login_grace_until()
        return max(0, int(grace_until - current_time))


    def _is_in_qr_login_grace_period(self, current_time: Optional[float] = None) -> bool:
        return self._get_qr_login_grace_remaining_seconds(current_time) > 0


    def _set_qr_login_grace_until(self, grace_until: int) -> None:
        db_manager.set_cookie_qr_login_grace_until(self.cookie_id, int(grace_until or 0))


    def _clear_qr_login_grace_period(self) -> None:
        self.clear_qr_login_grace(self.cookie_id)
        self._set_qr_login_grace_until(0)


    def _enter_qr_login_grace_period(self, *, stage: str = 'qr_login_success') -> int:
        now = time.time()
        grace_until = int(now + self.get_qr_login_grace_ttl_seconds())
        self.mark_qr_login_grace(self.cookie_id, stage=stage, entered_at=now)
        self._set_qr_login_grace_until(grace_until)
        return grace_until


    def _consume_qr_login_grace_period_if_expired(self, current_time: Optional[float] = None) -> bool:
        current_time = current_time or time.time()
        grace_until = self._get_qr_login_grace_until()
        if not grace_until:
            return False
        if current_time < grace_until:
            return False
        self._clear_qr_login_grace_period()
        logger.info(f"【{self.cookie_id}】扫码登录稳定期已结束，恢复自动认证链路")
        return True


    def _should_defer_auth_recovery_for_qr_grace(self, current_time: Optional[float] = None) -> bool:
        current_time = current_time or time.time()
        self._consume_qr_login_grace_period_if_expired(current_time)
        remaining = self._get_qr_login_grace_remaining_seconds(current_time)
        if remaining <= 0:
            return False
        self.last_token_refresh_status = "qr_login_grace_wait"
        self.last_token_refresh_error_message = f"扫码登录稳定期中，剩余{remaining}秒"
        logger.warning(f"【{self.cookie_id}】扫码登录稳定期中，暂缓自动认证恢复，还需等待 {remaining} 秒")
        return True


    @classmethod
    def _cleanup_password_login_failure_backoff(cls):
        """清理已过期的密码登录失败退避状态"""
        now = time.time()
        expired_cookie_ids = [
            cookie_id
            for cookie_id, state in cls._password_login_failure_backoff.items()
            if now >= state.get('until', 0)
        ]
        for cookie_id in expired_cookie_ids:
            cls._password_login_failure_backoff.pop(cookie_id, None)


    @classmethod
    def get_password_login_failure_backoff(cls, cookie_id: str) -> Optional[Dict[str, Any]]:
        """获取当前账号的密码登录失败退避状态"""
        if not cookie_id:
            return None
        cls._cleanup_password_login_failure_backoff()
        return cls._password_login_failure_backoff.get(cookie_id)


    @classmethod
    def clear_password_login_failure_backoff(cls, cookie_id: str):
        """清理指定账号的密码登录失败退避状态"""
        if not cookie_id:
            return
        cls._password_login_failure_backoff.pop(cookie_id, None)


    @classmethod
    def set_password_login_failure_backoff(cls, cookie_id: str, reason: str, seconds: int):
        """设置密码登录失败后的退避时间"""
        if not cookie_id or seconds <= 0:
            return
        previous_state = cls._password_login_failure_backoff.get(cookie_id) or {}
        previous_reason = previous_state.get('reason')
        previous_count = int(previous_state.get('consecutive_count', 0) or 0)
        consecutive_count = previous_count + 1 if previous_reason == reason else 1
        escalation_factor = float(RISK_CONTROL.get('backoff_escalation_factor', 1.5) or 1.5)
        max_cap = max(seconds, int(RISK_CONTROL.get('backoff_max_cap_seconds', 3600) or 3600))
        actual_seconds = int(round(min(seconds * (escalation_factor ** max(0, consecutive_count - 1)), max_cap)))
        actual_seconds = max(seconds, actual_seconds)
        now = time.time()
        cls._password_login_failure_backoff[cookie_id] = {
            'until': now + actual_seconds,
            'reason': reason,
            'seconds': actual_seconds,
            'base_seconds': seconds,
            'consecutive_count': consecutive_count,
            'created_at': now,
        }


    @staticmethod
    def _is_counted_password_login_failure_reason(reason: str) -> bool:
        return str(reason or '').strip() in {'slider_failed', 'risk_control'}


    def _get_night_mode_settings(self) -> Dict[str, Any]:
        from config import config

        def _setting_value(system_key: str, config_key: str, default: Any) -> Any:
            raw_value = db_manager.get_system_setting(system_key)
            if raw_value is None:
                return RISK_CONTROL.get(config_key, config.get(f'RISK_CONTROL.{config_key}', default))
            return raw_value

        enabled_raw = _setting_value('risk_control_night_mode_enabled', 'night_mode_enabled', False)
        start_raw = _setting_value('risk_control_night_start_hour', 'night_start_hour', 1)
        end_raw = _setting_value('risk_control_night_end_hour', 'night_end_hour', 6)

        def _to_bool(value: Any, default: bool = False) -> bool:
            if isinstance(value, bool):
                return value
            if value is None:
                return default
            return str(value).strip().lower() in {'1', 'true', 'yes', 'on'}

        def _to_hour(value: Any, default: int) -> int:
            try:
                return max(0, min(23, int(value)))
            except (TypeError, ValueError):
                return default

        return {
            'enabled': _to_bool(enabled_raw, False),
            'start_hour': _to_hour(start_raw, 1),
            'end_hour': _to_hour(end_raw, 6),
        }


    def _is_in_night_mode_window(self, local_hour: Optional[int] = None) -> bool:
        settings = self._get_night_mode_settings()
        if not settings.get('enabled'):
            return False

        current_hour = datetime.now().hour if local_hour is None else int(local_hour)
        start_hour = int(settings.get('start_hour', 1))
        end_hour = int(settings.get('end_hour', 6))
        if start_hour == end_hour:
            return True
        if start_hour < end_hour:
            return start_hour <= current_hour < end_hour
        return current_hour >= start_hour or current_hour < end_hour


    def _get_effective_keepalive_interval(self) -> int:
        base_interval = max(60, int(self.session_keepalive_interval or 600))
        if not self._is_in_night_mode_window():
            return base_interval
        multiplier = max(1, int(RISK_CONTROL.get('night_keepalive_multiplier', 3) or 3))
        return base_interval * multiplier


    def _get_effective_cookie_refresh_interval(self) -> int:
        base_interval = max(60, int(self.cookie_refresh_interval or 10800))
        if not self._is_in_night_mode_window():
            return base_interval
        multiplier = max(1, int(RISK_CONTROL.get('night_cookie_refresh_multiplier', 2) or 2))
        return base_interval * multiplier


    def _compute_token_retry_wait_seconds(self, current_time: Optional[float] = None) -> int:
        current_time = current_time or time.time()
        min_wait = max(60, int(RISK_CONTROL.get('token_retry_min_wait_seconds', 180) or 180))
        backoff = self._get_active_password_login_failure_backoff(current_time)
        if backoff:
            remaining = max(0, int(backoff.get('remaining_time', 0) or 0))
            return max(min_wait, remaining + 60)
        return max(min_wait, int(self.token_retry_interval or min_wait))


    async def _protect_account_for_consecutive_failures(self, backoff_state: Optional[Dict[str, Any]] = None) -> bool:
        state = backoff_state or self._get_active_password_login_failure_backoff()
        if not state:
            return False

        reason = str(state.get('reason') or '').strip()
        if not self._is_counted_password_login_failure_reason(reason):
            return False

        threshold = max(1, int(RISK_CONTROL.get('consecutive_failure_protection_threshold', 5) or 5))
        consecutive_count = int(state.get('consecutive_count', 0) or 0)
        if consecutive_count < threshold:
            return False

        pause_reason = f"连续{consecutive_count}次{reason}"
        await self._apply_account_pause_state(
            refresh_status="consecutive_failure_protected",
            status_note="连续风控保护中",
            error_message=f"检测到{pause_reason}，已暂停账号等待人工介入",
            connection_message="连续风控失败，已自动暂停账号",
            note_error_prefix="写入连续失败保护状态文案失败",
            status_error_prefix="持久化连续失败保护状态失败",
            memory_error_prefix="更新连续失败内存状态失败",
        )
        await self.send_account_paused_notification(
            status_note="连续风控保护中",
            pause_reason=pause_reason,
            error_message=f"账号在自动恢复过程中已连续触发 {consecutive_count} 次 {reason}，系统已暂停自动恢复以避免继续放大风控。",
            verification_url='',
        )
        await self._request_stop_after_account_pause("连续风控失败触发账号保护")
        return True


    def _get_active_password_login_failure_backoff(self, current_time: Optional[float] = None) -> Optional[Dict[str, Any]]:
        """获取仍在生效的密码登录失败退避状态，并处理可忽略的旧滑块退避。"""
        current_time = current_time or time.time()
        failure_backoff = self.get_password_login_failure_backoff(self.cookie_id)
        if not failure_backoff:
            return None

        remaining_time = failure_backoff.get('until', 0) - current_time
        if remaining_time <= 0:
            return None

        backoff_reason = failure_backoff.get('reason', 'unknown')
        if backoff_reason == 'slider_failed' and (
            self._has_recent_slider_success() or self.consume_manual_refresh_slider_failed_bypass(self.cookie_id)
        ):
            logger.warning(
                f"【{self.cookie_id}】检测到最近刚通过滑块或处于刷新交接恢复窗口，忽略一次旧的 slider_failed 退避并继续尝试恢复"
            )
            self.clear_password_login_failure_backoff(self.cookie_id)
            return None

        state = dict(failure_backoff)
        state['reason'] = backoff_reason
        state['remaining_time'] = remaining_time
        return state


    def _should_skip_token_refresh_for_login_backoff(self, current_time: Optional[float] = None) -> bool:
        """在需要人工介入或明确退避期间，直接跳过 token 预检，避免重复打到平台。"""
        current_time = current_time or time.time()
        failure_backoff = self._get_active_password_login_failure_backoff(current_time)
        if not failure_backoff:
            return False

        backoff_reason = failure_backoff.get('reason', 'unknown')
        if backoff_reason not in {'slider_failed', 'verification_required', 'credentials', 'risk_control'}:
            return False

        remaining_time = failure_backoff.get('remaining_time', 0.0)
        should_log = (
            self.last_token_refresh_status != "password_login_backoff_wait" or
            (current_time - getattr(self, 'last_password_login_backoff_log_time', 0.0)) >= 30
        )
        if should_log:
            logger.warning(
                f"【{self.cookie_id}】密码登录失败退避中（原因: {backoff_reason}），"
                f"直接跳过本次token刷新，还需等待 {remaining_time:.1f} 秒"
            )
            self.last_password_login_backoff_log_time = current_time

        self.last_token_refresh_status = "password_login_backoff_wait"
        self.last_token_refresh_error_message = f"密码登录失败退避中，剩余{remaining_time:.1f}秒"
        return True


    @staticmethod
    def classify_password_login_failure(error_message: str) -> Tuple[str, int]:
        """按失败类型返回(原因标签, 退避秒数)"""
        message = (error_message or "").lower()
        if any(keyword in message for keyword in ["账号密码错误", "账密错误", "用户名或密码错误", "密码错误"]):
            return "credentials", 1800
        if any(
            keyword in message for keyword in [
                "短信验证",
                "二维码验证",
                "人脸验证",
                "身份验证",
                "等待短信验证超时",
                "等待二维码验证超时",
                "等待人脸验证超时",
                "等待身份验证超时",
            ]
        ):
            return "verification_required", 900
        if any(keyword in message for keyword in ["前置滑块", "风控", "拦截", "框体错误", "点击框体重试", "账号存在风险", "闲鱼客户端登录"]):
            return "risk_control", 900
        if any(keyword in message for keyword in ["滑块验证失败", "未找到滑块容器"]):
            return "slider_failed", 600
        if any(
            keyword in message for keyword in [
                "未找到登录表单",
                "未找到登录iframe",
                "session过期且清理会话状态后未找到登录表单",
                "session验证异常且清理会话状态后未找到登录表单",
            ]
        ):
            return "login_form_missing", 90
        if any(keyword in message for keyword in ["页面会话已失效", "target page, context or browser has been closed"]):
            return "unknown", 180
        if any(keyword in message for keyword in ["网络", "timeout", "cannot connect", "连接", "dns", "ssl"]):
            return "network", 180
        return "unknown", 300


    @staticmethod
    def _is_account_risk_login_error(error_message: str) -> bool:
        """识别需要立即停账号保护的高风险登录提示。"""
        message = str(error_message or "").strip()
        if not message:
            return False
        return "账号存在风险" in message and ("闲鱼客户端登录" in message or "按提示操作" in message)


    @staticmethod
    def _is_account_pause_status(status: str) -> bool:
        return status in {"account_risk_protected", "manual_verification_required"}


    @staticmethod
    def _should_pause_for_manual_verification(verification_type: str, verification_context: str) -> bool:
        """判断人工介入提示是否应禁用账号。

        普通扫码登录页（login_page / mini_login）只是登录态丢失后的正常登录入口，
        不能当作风控/身份校验来暂停账号；真正的人脸/短信/二维码身份验证仍按自动流程保护。
        """
        if verification_context in MANUAL_VERIFICATION_CONTEXTS:
            return False
        if verification_type == 'login_page':
            return False
        return True


    async def _apply_account_pause_state(
        self,
        *,
        refresh_status: str,
        status_note: str,
        error_message: str,
        connection_message: str,
        note_error_prefix: str,
        status_error_prefix: str,
        memory_error_prefix: str,
    ) -> None:
        self.current_token = None
        self.last_token_refresh_status = refresh_status
        self.last_token_refresh_error_message = str(error_message or "").strip()
        self.clear_password_login_failure_backoff(self.cookie_id)

        try:
            db_manager.update_cookie_status_note(self.cookie_id, status_note)
        except Exception as note_e:
            logger.error(f"【{self.cookie_id}】{note_error_prefix}: {self._safe_str(note_e)}")

        try:
            db_manager.save_cookie_status(self.cookie_id, False)
        except Exception as status_e:
            logger.error(f"【{self.cookie_id}】{status_error_prefix}: {self._safe_str(status_e)}")

        _mgr = self._cookie_mgr
        if _mgr:
            _mgr.cookie_status[self.cookie_id] = False

        self._set_connection_state(ConnectionState.FAILED, connection_message)


    async def _clear_account_pause_state(self, reason: str = "认证恢复成功") -> None:
        self.last_token_refresh_error_message = ""
        self._clear_qr_login_grace_period()

        try:
            db_manager.update_cookie_status_note(self.cookie_id, '')
        except Exception as note_e:
            logger.error(f"【{self.cookie_id}】清理账号状态文案失败: {self._safe_str(note_e)}")

        try:
            db_manager.save_cookie_status(self.cookie_id, True)
        except Exception as status_e:
            logger.error(f"【{self.cookie_id}】恢复账号启用状态失败: {self._safe_str(status_e)}")

        _mgr = self._cookie_mgr
        if _mgr:
            _mgr.cookie_status[self.cookie_id] = True

        logger.info(f"【{self.cookie_id}】账号暂停状态已清理: {reason}")


    async def _request_stop_after_account_pause(self, reason: str) -> None:
        try:
            _mgr = self._cookie_mgr
            if not _mgr:
                return

            current_task = asyncio.current_task()
            tracked_task = _mgr.tasks.get(self.cookie_id)

            if tracked_task is current_task:
                _mgr.tasks.pop(self.cookie_id, None)
                loop = asyncio.get_running_loop()

                def _cancel_current_task() -> None:
                    if current_task and not current_task.done():
                        current_task.cancel()

                loop.call_soon(_cancel_current_task)
                logger.info(f"【{self.cookie_id}】账号已暂停，当前任务将在本轮流程结束后停止: {reason}")
                return

            if tracked_task and not tracked_task.done():
                tracked_task.cancel()
                logger.info(f"【{self.cookie_id}】账号已暂停，已取消运行中的账号任务: {reason}")

            if tracked_task is not None:
                _mgr.tasks.pop(self.cookie_id, None)
        except Exception as stop_e:
            logger.warning(f"【{self.cookie_id}】请求停止暂停账号任务失败: {self._safe_str(stop_e)}")


    async def _protect_account_from_risk_login_retry(self, error_message: str, status_note: str = "风控保护中") -> bool:
        """命中高风险登录提示后自动禁用账号，避免持续触发更强风控。"""
        message = str(error_message or "").strip()
        if not self._is_account_risk_login_error(message):
            return False

        await self._apply_account_pause_state(
            refresh_status="account_risk_protected",
            status_note=status_note,
            error_message=message,
            connection_message="检测到账号风控，已自动禁用",
            note_error_prefix="写入账号状态文案失败",
            status_error_prefix="持久化账号禁用状态失败",
            memory_error_prefix="更新内存账号状态失败",
        )
        logger.error(
            f"【{self.cookie_id}】检测到账号高风险登录提示，已自动禁用账号并标记为“{status_note}”，停止后续自动登录重试"
        )
        try:
            await self._force_websocket_reconnect("检测到账号风控，账号已自动禁用")
        except Exception as reconnect_e:
            logger.warning(f"【{self.cookie_id}】风控保护触发后关闭WebSocket失败: {self._safe_str(reconnect_e)}")
        return True


    async def _pause_account_for_manual_verification(
        self,
        verification_type: str = None,
        error_message: str = "",
        pause_account: bool = True,
        verification_context: str = 'auto_refresh',
        verification_url: str = '',
    ) -> bool:
        """检测到需要人工验证时，按上下文决定是否暂停账号。"""
        verification_type_names = {
            'face_verify': '人脸验证',
            'sms_verify': '短信验证',
            'qr_verify': '二维码验证',
            'login_page': '扫码登录',
            'unknown': '身份验证',
        }
        type_name = verification_type_names.get(verification_type, '身份验证')
        status_note = f"待{type_name}"
        message = str(error_message or f"检测到需要人工完成的{type_name}").strip()

        if not pause_account:
            if verification_type == 'login_page':
                logger.warning(
                    f"【{self.cookie_id}】检测到普通扫码登录入口({verification_context})，仅通知用户完成登录，不自动暂停账号"
                )
            else:
                logger.warning(
                    f"【{self.cookie_id}】检测到需要人工完成的{type_name}，但当前属于手动流程({verification_context})，不自动暂停账号"
                )
            return False

        await self._apply_account_pause_state(
            refresh_status="manual_verification_required",
            status_note=status_note,
            error_message=message,
            connection_message=f"检测到{type_name}，已自动暂停账号",
            note_error_prefix="写入人工验证状态文案失败",
            status_error_prefix="持久化人工验证暂停状态失败",
            memory_error_prefix="更新人工验证内存状态失败",
        )
        logger.warning(
            f"【{self.cookie_id}】检测到需要人工完成的{type_name}，已自动暂停账号并标记为“{status_note}”"
        )
        await self.send_account_paused_notification(
            status_note=status_note,
            pause_reason=type_name,
            error_message=message,
            verification_url=verification_url,
            action_hint='请先完成验证，再在账号管理中恢复或重新启动该账号。',
        )
        return True


    async def send_account_paused_notification(
        self,
        *,
        status_note: str,
        pause_reason: str,
        error_message: str,
        verification_url: str = '',
        action_hint: str = '',
    ) -> bool:
        message = render_notification_template(
            'account_paused',
            account_id=self.cookie_id,
            status_note=status_note or '已暂停',
            pause_reason=pause_reason or '未知原因',
            time=time.strftime('%Y-%m-%d %H:%M:%S'),
            error_message=error_message or '系统检测到账号需要人工处理',
            verification_url=verification_url or '无',
            action_hint=action_hint or '请尽快处理账号状态，避免自动任务长时间不可用。',
        )

        logger.info(f"【{self.cookie_id}】准备发送账号暂停通知")
        sent = await dispatch_account_notifications(
            self.cookie_id,
            message,
            title='闲鱼账号已暂停',
            notification_type='account_paused',
        )
        if sent:
            logger.info(f"【{self.cookie_id}】账号暂停通知发送成功")
        else:
            logger.warning(f"【{self.cookie_id}】账号暂停通知未发送成功")
        return sent


    def _safe_str(self, e):
        """安全地将异常转换为字符串"""
        try:
            return str(e)
        except Exception:
            try:
                return repr(e)
            except Exception:
                return "未知错误"


    def _mask_secret_value(self, value: str, head: int = 6, tail: int = 4) -> str:
        text = str(value or '')
        if not text:
            return ''
        if len(text) <= head + tail:
            return '***'
        return f"{text[:head]}***{text[-tail:]}"


    def _summarize_cookie_string(self, cookie_string: str) -> str:
        cookie_string = str(cookie_string or '').strip()
        if not cookie_string:
            return 'empty-cookie'

        segments = []
        for part in cookie_string.split(';'):
            part = part.strip()
            if not part:
                continue
            if '=' in part:
                key, value = part.split('=', 1)
                segments.append(f"{key.strip()}={self._mask_secret_value(value.strip(), head=4, tail=2)}")
            else:
                segments.append(self._mask_secret_value(part, head=4, tail=2))

        preview = '; '.join(segments[:6])
        if len(segments) > 6:
            preview += f"; ...(+{len(segments) - 6} fields)"
        return preview


    @staticmethod
    def _new_risk_session_id(prefix: str = 'risk') -> str:
        return f"{prefix}_{secrets.token_hex(8)}"


    def _normalize_risk_trigger_scene(self, trigger_reason: str = None, default: str = 'unknown') -> str:
        text = str(trigger_reason or '').strip()
        if not text:
            return default
        lower_text = text.lower()
        if 'token' in lower_text or 'session' in lower_text or '令牌' in text:
            return 'token_refresh'
        if 'password' in lower_text or '账密' in text or '登录' in text:
            return 'password_login'
        if 'cookie' in lower_text or '连接' in text or '失败' in text:
            return 'auto_cookie_refresh'
        return default


    def _build_risk_event_meta(self, trigger_scene: str = None, verification_url: str = None, extra: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        payload: Dict[str, Any] = {}
        if trigger_scene:
            payload['trigger_scene'] = trigger_scene
        payload.update(self._sanitize_verification_meta(verification_url))
        if isinstance(extra, dict):
            payload.update({key: value for key, value in extra.items() if value is not None})
        return payload or None


    def _create_risk_log(
        self,
        event_type: str,
        event_description: str,
        processing_status: str = 'processing',
        processing_result: str = None,
        error_message: str = None,
        session_id: str = None,
        trigger_scene: str = None,
        result_code: str = None,
        event_meta: Optional[Dict[str, Any]] = None,
        duration_ms: Optional[int] = None,
    ) -> Optional[int]:
        try:
            return db_manager.add_risk_control_log(
                cookie_id=self.cookie_id,
                event_type=event_type,
                session_id=session_id,
                trigger_scene=trigger_scene,
                result_code=result_code,
                event_description=event_description,
                event_meta=event_meta,
                processing_result=processing_result,
                processing_status=processing_status,
                error_message=error_message,
                duration_ms=duration_ms,
            )
        except Exception as e:
            logger.error(f"【{self.cookie_id}】记录风控日志失败: {self._safe_str(e)}")
            return None


    @classmethod
    def begin_manual_refresh(cls, cookie_id: str, source: str = "manual_refresh") -> Dict[str, Any]:
        """标记账号进入手动刷新保护期，并暂停自动Cookie刷新"""
        if not cookie_id:
            return {"started": False, "already_active": False, "reason": "empty_cookie_id"}

        live_instance = cls.get_instance(cookie_id)
        previous_cookie_refresh_enabled = None
        if live_instance is not None:
            previous_cookie_refresh_enabled = live_instance.cookie_refresh_enabled

        cls._cleanup_manual_refresh_state()
        with cls._manual_refresh_lock:
            existing = cls._manual_refresh_state.get(cookie_id)
            if existing:
                existing["source"] = source
                existing["phase"] = 'manual_refresh'
                existing["updated_at"] = time.time()
                existing["expires_at"] = None
                return {
                    "started": False,
                    "already_active": True,
                    "previous_cookie_refresh_enabled": existing.get("previous_cookie_refresh_enabled")
                }

            cls._manual_refresh_state[cookie_id] = {
                "source": source,
                "phase": 'manual_refresh',
                "started_at": time.time(),
                "updated_at": time.time(),
                "expires_at": None,
                "previous_cookie_refresh_enabled": previous_cookie_refresh_enabled,
            }

        if live_instance is not None and previous_cookie_refresh_enabled is not None:
            live_instance.enable_cookie_refresh(False)
            logger.warning(f"【{cookie_id}】已进入手动刷新保护期，暂停自动Cookie刷新")
        else:
            logger.warning(f"【{cookie_id}】已进入手动刷新保护期，当前无运行中的账号实例")

        return {
            "started": True,
            "already_active": False,
            "previous_cookie_refresh_enabled": previous_cookie_refresh_enabled
        }


    @classmethod
    def end_manual_refresh(cls, cookie_id: str, source: str = "manual_refresh") -> bool:
        """结束手动刷新保护期，并按原状态恢复自动Cookie刷新"""
        if not cookie_id:
            return False

        cls._cleanup_manual_refresh_state()
        with cls._manual_refresh_lock:
            state = cls._manual_refresh_state.pop(cookie_id, None)

        if state is None:
            return False

        live_instance = cls.get_instance(cookie_id)
        previous_cookie_refresh_enabled = state.get("previous_cookie_refresh_enabled")
        if live_instance is not None and previous_cookie_refresh_enabled is not None:
            live_instance.enable_cookie_refresh(previous_cookie_refresh_enabled)
            if previous_cookie_refresh_enabled:
                # 手动刷新刚结束时，避免新实例立刻再触发一轮自动Cookie刷新。
                live_instance.last_cookie_refresh_time = time.time()
            logger.warning(
                f"【{cookie_id}】手动刷新保护期已结束，恢复自动Cookie刷新: {previous_cookie_refresh_enabled}"
            )
        else:
            logger.warning(f"【{cookie_id}】手动刷新保护期已结束，当前无运行中的账号实例可恢复")

        logger.info(f"【{cookie_id}】结束手动刷新保护期，来源: {source}")
        return True


    async def _try_password_login_refresh(
        self,
        trigger_reason: str = "令牌/Session过期",
        risk_session_id: Optional[str] = None,
        trigger_scene: Optional[str] = None,
        ignore_slider_failed_backoff: bool = False,
    ):
        """尝试通过密码登录刷新Cookie并重启实例
        
        Args:
            trigger_reason: 触发原因，用于日志记录
            
        Returns:
            bool: 是否成功刷新Cookie
        """
        logger.warning(f"【{self.cookie_id}】检测到{trigger_reason}，准备刷新Cookie并重启实例...")
        trigger_scene = trigger_scene or self._normalize_risk_trigger_scene(trigger_reason, default='auto_cookie_refresh')
        risk_session_id = risk_session_id or self._new_risk_session_id('cookie')
        risk_log_started_at = time.time()
        base_event_meta = {'cookie_id': self.cookie_id, 'trigger_reason': trigger_reason}

        # 记录到风控日志
        refresh_risk_log_id = None
        try:
            stale_count = db_manager.mark_stale_risk_control_logs_failed(timeout_minutes=15, cookie_id=self.cookie_id)
            if stale_count > 0:
                logger.warning(f"【{self.cookie_id}】检测到{stale_count}条超时processing风控日志，已自动标记failed")
            refresh_risk_log_id = self._create_risk_log(
                event_type='cookie_refresh',
                session_id=risk_session_id,
                trigger_scene=trigger_scene,
                result_code='cookie_refresh_started',
                event_description=f"{trigger_reason}触发Cookie刷新",
                processing_status='processing',
                event_meta=self._build_risk_event_meta(trigger_scene=trigger_scene, extra=base_event_meta),
            )
        except Exception as log_e:
            logger.error(f"【{self.cookie_id}】记录风控日志失败: {log_e}")

        if self.is_manual_refresh_active(self.cookie_id, allow_handoff_recovery=True):
            logger.warning(f"【{self.cookie_id}】手动刷新进行中，跳过自动密码登录刷新")
            if refresh_risk_log_id:
                self._update_risk_log(
                    refresh_risk_log_id,
                    session_id=risk_session_id,
                    trigger_scene=trigger_scene,
                    result_code='manual_refresh_active',
                    processing_status='failed',
                    error_message='手动刷新进行中，自动密码登录刷新已跳过',
                    duration_ms=max(0, int((time.time() - risk_log_started_at) * 1000)),
                    event_meta=self._build_risk_event_meta(trigger_scene=trigger_scene, extra=base_event_meta),
                )
            return False

        if self._is_account_pause_status(getattr(self, 'last_token_refresh_status', None)):
            logger.warning(f"【{self.cookie_id}】账号处于人工验证/风控暂停状态，跳过自动密码登录刷新")
            if refresh_risk_log_id:
                self._update_risk_log(
                    refresh_risk_log_id,
                    session_id=risk_session_id,
                    trigger_scene=trigger_scene,
                    result_code='account_pause_active',
                    processing_status='failed',
                    error_message='账号处于人工验证/风控暂停状态，自动密码登录刷新已跳过',
                    duration_ms=max(0, int((time.time() - risk_log_started_at) * 1000)),
                    event_meta=self._build_risk_event_meta(trigger_scene=trigger_scene, extra=base_event_meta),
                )
            return False

        if self._should_defer_auth_recovery_for_qr_grace():
            logger.warning(f"【{self.cookie_id}】扫码登录稳定期内，跳过自动密码登录刷新")
            if refresh_risk_log_id:
                self._update_risk_log(
                    refresh_risk_log_id,
                    session_id=risk_session_id,
                    trigger_scene=trigger_scene,
                    result_code='qr_login_grace_active',
                    processing_status='failed',
                    error_message=self.last_token_refresh_error_message or '扫码登录稳定期内，自动密码登录刷新已跳过',
                    duration_ms=max(0, int((time.time() - risk_log_started_at) * 1000)),
                    event_meta=self._build_risk_event_meta(trigger_scene=trigger_scene, extra=base_event_meta),
                )
            return False

        recovery_lock_owner = f"{self.cookie_id}:{trigger_scene or 'auto_cookie_refresh'}:{int(time.time() * 1000)}"
        recovery_lock_acquired = False

        # 检查是否在密码登录冷却期内，避免重复登录
        current_time = time.time()
        failure_backoff = self._get_active_password_login_failure_backoff(current_time)
        if failure_backoff:
            backoff_reason = failure_backoff.get('reason', 'unknown')
            remaining_time = failure_backoff.get('remaining_time', 0.0)
            if backoff_reason == 'slider_failed' and ignore_slider_failed_backoff:
                logger.warning(
                    f"【{self.cookie_id}】检测到最近刚通过滑块，忽略一次旧的 slider_failed 退避并继续尝试密码登录刷新"
                )
                self.clear_password_login_failure_backoff(self.cookie_id)
                failure_backoff = None
            else:
                logger.warning(
                    f"【{self.cookie_id}】密码登录失败退避中（原因: {backoff_reason}），还需等待 {remaining_time:.1f} 秒"
                )
                if refresh_risk_log_id:
                    self._update_risk_log(
                        refresh_risk_log_id,
                        session_id=risk_session_id,
                        trigger_scene=trigger_scene,
                        result_code='password_login_backoff',
                        processing_status='failed',
                        error_message=f"密码登录失败退避中，剩余{remaining_time:.1f}秒",
                        duration_ms=max(0, int((time.time() - risk_log_started_at) * 1000)),
                        event_meta=self._build_risk_event_meta(
                            trigger_scene=trigger_scene,
                            extra={**base_event_meta, 'backoff_reason': backoff_reason, 'backoff_seconds': failure_backoff.get('seconds')},
                        ),
                    )
                return False

        last_password_login = self._last_password_login_time.get(self.cookie_id, 0)
        time_since_last_login = current_time - last_password_login
        
        if last_password_login > 0 and time_since_last_login < self._password_login_cooldown:
            remaining_time = self._password_login_cooldown - time_since_last_login
            logger.warning(f"【{self.cookie_id}】距离上次密码登录仅 {time_since_last_login:.1f} 秒，仍在冷却期内（还需等待 {remaining_time:.1f} 秒），跳过密码登录")
            logger.warning(f"【{self.cookie_id}】提示：如果新Cookie仍然无效，请检查账号状态或手动更新Cookie")
            if refresh_risk_log_id:
                self._update_risk_log(
                    refresh_risk_log_id,
                    session_id=risk_session_id,
                    trigger_scene=trigger_scene,
                    result_code='password_login_cooldown',
                    processing_status='failed',
                    error_message=f"密码登录冷却期内，剩余{remaining_time:.1f}秒",
                    duration_ms=max(0, int((time.time() - risk_log_started_at) * 1000)),
                    event_meta=self._build_risk_event_meta(trigger_scene=trigger_scene, extra=base_event_meta),
                )
            return False

        recovery_lock_acquired, existing_lock = self.acquire_auth_recovery_lock(
            self.cookie_id,
            recovery_lock_owner,
        )
        if not recovery_lock_acquired:
            existing_owner = (existing_lock or {}).get('owner', 'unknown')
            logger.warning(f"【{self.cookie_id}】认证恢复流程已在执行中，跳过本次重复触发: owner={existing_owner}")
            if refresh_risk_log_id:
                self._update_risk_log(
                    refresh_risk_log_id,
                    session_id=risk_session_id,
                    trigger_scene=trigger_scene,
                    result_code='auth_recovery_in_progress',
                    processing_status='failed',
                    error_message='已有认证恢复流程执行中',
                    duration_ms=max(0, int((time.time() - risk_log_started_at) * 1000)),
                    event_meta=self._build_risk_event_meta(
                        trigger_scene=trigger_scene,
                        extra={**base_event_meta, 'active_owner': existing_owner},
                    ),
                )
            return False

        # 记录到日志文件
        log_captcha_event(self.cookie_id, f"{trigger_reason}触发Cookie刷新和实例重启", None,
            f"检测到{trigger_reason}，准备刷新Cookie并重启实例")

        try:
            # 从数据库获取账号登录信息
            account_info = db_manager.get_cookie_details(self.cookie_id)

            if not account_info:
                logger.error(f"【{self.cookie_id}】无法获取账号信息")
                self.last_token_refresh_error_message = "无法获取账号信息"
                if refresh_risk_log_id:
                    self._update_risk_log(
                        refresh_risk_log_id,
                        session_id=risk_session_id,
                        trigger_scene=trigger_scene,
                        result_code='account_info_missing',
                        processing_status='failed',
                        error_message='无法获取账号信息',
                        duration_ms=max(0, int((time.time() - risk_log_started_at) * 1000)),
                        event_meta=self._build_risk_event_meta(trigger_scene=trigger_scene, extra=base_event_meta),
                    )
                return False

            # 【重要】先检查数据库中的cookie是否已经更新
            # 如果用户已经手动更新了cookie，就不需要触发密码登录刷新
            db_cookie_value = account_info.get('cookie_value', '')
            if db_cookie_value and db_cookie_value != self.cookies_str:
                logger.info(f"【{self.cookie_id}】检测到数据库中的cookie已更新，重新加载cookie")
                self._set_runtime_cookie_state(cookies_str=db_cookie_value, source="db_cookie_reload_before_password_login")
                logger.info(f"【{self.cookie_id}】Cookie已从数据库重新加载，跳过密码登录刷新")
                if refresh_risk_log_id:
                    self._update_risk_log(
                        refresh_risk_log_id,
                        session_id=risk_session_id,
                        trigger_scene=trigger_scene,
                        result_code='cookie_already_updated',
                        processing_status='success',
                        processing_result='检测到数据库Cookie已更新，自动刷新流程跳过',
                        duration_ms=max(0, int((time.time() - risk_log_started_at) * 1000)),
                        event_meta=self._build_risk_event_meta(trigger_scene=trigger_scene, extra=base_event_meta),
                    )
                return True
            
            username = account_info.get('username', '')
            password = account_info.get('password', '')
            show_browser = account_info.get('show_browser', False)
            
            # 检查是否配置了用户名和密码
            if not username or not password:
                logger.warning(f"【{self.cookie_id}】未配置用户名或密码，跳过密码登录刷新")
                self.last_token_refresh_error_message = "未配置用户名或密码，无法自动刷新Cookie"
                await self.send_token_refresh_notification(
                    f"检测到{trigger_reason}，但未配置用户名或密码，无法自动刷新Cookie",
                    "no_credentials"
                )
                if refresh_risk_log_id:
                    self._update_risk_log(
                        refresh_risk_log_id,
                        session_id=risk_session_id,
                        trigger_scene=trigger_scene,
                        result_code='missing_credentials',
                        processing_status='failed',
                        error_message='未配置用户名或密码，无法自动刷新Cookie',
                        duration_ms=max(0, int((time.time() - risk_log_started_at) * 1000)),
                        event_meta=self._build_risk_event_meta(trigger_scene=trigger_scene, extra=base_event_meta),
                    )
                return False
            
            # 使用集成的 Playwright 登录方法（slidex）
            from slidex.stealth import XianyuSliderStealth
            from slidex import SlidexConfig as _SlidexConfig
            browser_mode = "有头" if show_browser else "无头"
            logger.info(f"【{self.cookie_id}】开始使用{browser_mode}浏览器进行密码登录刷新Cookie...")
            logger.info(f"【{self.cookie_id}】使用账号: {username}")
            
            # 创建一个通知回调包装函数，支持接收截图路径和验证链接
            async def notification_callback_wrapper(
                message: str,
                screenshot_path: str = None,
                verification_url: str = None,
                verification_type: str = None,
            ):
                """通知回调包装函数，支持接收截图路径和验证链接"""
                verification_context = 'manual_cookie_refresh' if self.is_manual_refresh_active(self.cookie_id, allow_handoff_recovery=True) else 'auto_refresh'
                should_pause_account = self._should_pause_for_manual_verification(verification_type, verification_context)
                self.last_token_refresh_status = 'verification_pending_manual' if not should_pause_account else 'manual_verification_required'
                self.last_token_refresh_error_message = str(message or '').strip()
                pause_target_loop = None
                _mgr = self._cookie_mgr
                pause_target_loop = getattr(_mgr, 'loop', None)

                current_loop = None
                try:
                    current_loop = asyncio.get_running_loop()
                except RuntimeError:
                    current_loop = None

                if pause_target_loop and pause_target_loop.is_running() and pause_target_loop is not current_loop:
                    pause_future = asyncio.run_coroutine_threadsafe(
                        self._pause_account_for_manual_verification(
                            verification_type=verification_type,
                            error_message=message,
                            pause_account=should_pause_account,
                            verification_context=verification_context,
                            verification_url=verification_url or '',
                        ),
                        pause_target_loop,
                    )
                    try:
                        pause_future.result(timeout=10)
                    except Exception as pause_e:
                        logger.warning(f"【{self.cookie_id}】跨线程暂停人工验证账号失败: {self._safe_str(pause_e)}")
                else:
                    await self._pause_account_for_manual_verification(
                        verification_type=verification_type,
                        error_message=message,
                        pause_account=should_pause_account,
                        verification_context=verification_context,
                        verification_url=verification_url or '',
                    )

                await self.send_token_refresh_notification(
                    error_message=message,
                    notification_type="token_refresh",
                    chat_id=None,
                    attachment_path=screenshot_path,
                    verification_url=verification_url,
                    verification_type=verification_type,
                )
                if should_pause_account:
                    await self._request_stop_after_account_pause(
                        f"检测到需要人工完成的{verification_type or 'manual_verification'}"
                    )
            
            # 在单独的线程中运行同步的登录方法
            import asyncio
            _slidex_cfg = _SlidexConfig(
                on_risk_log=lambda **kw: db_manager.add_risk_control_log(**kw),
                on_risk_log_update=lambda **kw: db_manager.update_risk_control_log(**kw),
            )
            slider = XianyuSliderStealth(
                user_id=self.cookie_id, enable_learning=True, headless=not show_browser,
                slidex_config=_slidex_cfg,
            )
            slider.risk_session_id = risk_session_id
            slider.risk_trigger_scene = trigger_scene
            result = await slider._run_sync_method_on_fresh_thread(
                slider.login_with_password_playwright,
                account=username,
                password=password,
                show_browser=show_browser,
                notification_callback=notification_callback_wrapper,
                force_clean_context=True,
            )
            
            if result:
                logger.info(f"【{self.cookie_id}】密码登录成功，获取到Cookie")
                result_keys = list(result.keys()) if isinstance(result, dict) else []
                has_unb = any(str(k).lower() == 'unb' for k in result_keys)
                logger.info(
                    f"【{self.cookie_id}】密码登录Cookie摘要: "
                    f"count={len(result_keys)} keys={result_keys} has_unb={has_unb}"
                )
                self.clear_password_login_failure_backoff(self.cookie_id)
                
                # 仅打印字段名与长度，禁止值
                logger.info(f"【{self.cookie_id}】========== 密码登录Cookie字段摘要 ==========")
                logger.info(f"【{self.cookie_id}】Cookie字段数: {len(result_keys)}")
                for i, key in enumerate(result_keys, 1):
                    val = result.get(key) if isinstance(result, dict) else None
                    logger.info(
                        f"【{self.cookie_id}】  {i:2d}. {key}: len={len(str(val or ''))}"
                    )
                
                # 检查关键字段
                important_keys = ['unb', '_m_h5_tk', '_m_h5_tk_enc', 'cookie2', 't', 'sgcookie', 'cna']
                logger.info(f"【{self.cookie_id}】关键字段检查:")
                for key in important_keys:
                    if key in result:
                        val = result[key]
                        logger.info(f"【{self.cookie_id}】  ✅ {key}: {'存在' if val else '为空'} (长度: {len(str(val)) if val else 0})")
                    else:
                        logger.info(f"【{self.cookie_id}】  ❌ {key}: 缺失")
                logger.info(f"【{self.cookie_id}】==========================================")
                
                # 将cookie字典转换为字符串格式
                new_cookies_str = '; '.join([f"{k}={v}" for k, v in result.items()])
                logger.info(f"【{self.cookie_id}】Cookie字符串摘要: {self._summarize_cookie_string(new_cookies_str)}")
                
                # 记录密码登录时间，防止重复登录
                self._last_password_login_time[self.cookie_id] = time.time()
                logger.warning(f"【{self.cookie_id}】已记录密码登录时间，冷却期 {self._password_login_cooldown} 秒")
                await self._clear_account_pause_state("密码登录刷新成功")
                self.last_token_refresh_status = 'cookie_refresh_success'
                self.last_token_refresh_error_message = ''
                
                # ⚠️ 先发送通知，再更新cookies并重启任务
                # 因为重启后当前任务会被取消，不能在重启后发送通知
                try:
                    await self.send_token_refresh_notification(
                        f"账号密码登录成功，Cookie已获取，准备更新并重启",
                        "cookie_refresh_success"
                    )
                except Exception as notify_e:
                    logger.warning(f"【{self.cookie_id}】发送通知失败: {self._safe_str(notify_e)}")
                
                # 更新cookies并重启任务
                update_success = await self._update_cookies_and_restart(new_cookies_str)
                
                if update_success:
                    logger.info(f"【{self.cookie_id}】Cookie更新并重启任务成功")
                    # 更新风控日志状态为成功
                    if refresh_risk_log_id:
                        self._update_risk_log(
                            refresh_risk_log_id,
                            session_id=risk_session_id,
                            trigger_scene=trigger_scene,
                            result_code='cookie_refresh_success',
                            processing_status='success',
                            processing_result='密码登录刷新Cookie成功，实例已重启',
                            duration_ms=max(0, int((time.time() - risk_log_started_at) * 1000)),
                            event_meta=self._build_risk_event_meta(trigger_scene=trigger_scene, extra=base_event_meta),
                        )
                    return True
                else:
                    logger.error(f"【{self.cookie_id}】Cookie更新失败")
                    if refresh_risk_log_id:
                        self._update_risk_log(
                            refresh_risk_log_id,
                            session_id=risk_session_id,
                            trigger_scene=trigger_scene,
                            result_code='cookie_save_failed',
                            processing_status='failed',
                            error_message='Cookie获取成功但更新到数据库失败',
                            duration_ms=max(0, int((time.time() - risk_log_started_at) * 1000)),
                            event_meta=self._build_risk_event_meta(trigger_scene=trigger_scene, extra=base_event_meta),
                        )
                    return False
                    
            else:
                login_error = getattr(slider, 'last_login_error', '') or "密码登录失败，未获取到Cookie"
                self.last_token_refresh_error_message = login_error
                if await self._protect_account_from_risk_login_retry(login_error):
                    if refresh_risk_log_id:
                        self._update_risk_log(
                            refresh_risk_log_id,
                            session_id=risk_session_id,
                            trigger_scene=trigger_scene,
                            result_code='account_risk_protected',
                            processing_status='failed',
                            error_message=login_error[:200],
                            duration_ms=max(0, int((time.time() - risk_log_started_at) * 1000)),
                            event_meta=self._build_risk_event_meta(
                                trigger_scene=trigger_scene,
                                extra={**base_event_meta, 'status_note': '风控保护中'},
                            ),
                        )
                    await self._request_stop_after_account_pause("检测到账号高风险登录提示")
                    return False
                backoff_reason, backoff_seconds = self.classify_password_login_failure(login_error)
                self.set_password_login_failure_backoff(self.cookie_id, backoff_reason, backoff_seconds)
                protected = await self._protect_account_for_consecutive_failures(
                    self.get_password_login_failure_backoff(self.cookie_id)
                )
                logger.warning(f"【{self.cookie_id}】密码登录失败，未获取到Cookie: {login_error}")
                logger.warning(f"【{self.cookie_id}】已进入失败退避期: {backoff_reason}, {backoff_seconds}秒")
                if protected:
                    return False
                if refresh_risk_log_id:
                    self._update_risk_log(
                        refresh_risk_log_id,
                        session_id=risk_session_id,
                        trigger_scene=trigger_scene,
                        result_code=f'password_login_{backoff_reason}',
                        processing_status='failed',
                        error_message=login_error[:200],
                        duration_ms=max(0, int((time.time() - risk_log_started_at) * 1000)),
                        event_meta=self._build_risk_event_meta(
                            trigger_scene=trigger_scene,
                            extra={**base_event_meta, 'backoff_reason': backoff_reason, 'backoff_seconds': backoff_seconds},
                        ),
                    )
                return False

        except Exception as refresh_e:
            if await self._protect_account_from_risk_login_retry(str(refresh_e)):
                if refresh_risk_log_id:
                    self._update_risk_log(
                        refresh_risk_log_id,
                        session_id=risk_session_id,
                        trigger_scene=trigger_scene,
                        result_code='account_risk_protected',
                        processing_status='failed',
                        error_message=str(refresh_e)[:200],
                        duration_ms=max(0, int((time.time() - risk_log_started_at) * 1000)),
                        event_meta=self._build_risk_event_meta(
                            trigger_scene=trigger_scene,
                            extra={**base_event_meta, 'status_note': '风控保护中'},
                        ),
                    )
                await self._request_stop_after_account_pause("检测到账号高风险登录异常")
                return False
            self.last_token_refresh_error_message = self._safe_str(refresh_e)
            backoff_reason, backoff_seconds = self.classify_password_login_failure(str(refresh_e))
            self.set_password_login_failure_backoff(self.cookie_id, backoff_reason, backoff_seconds)
            protected = await self._protect_account_for_consecutive_failures(
                self.get_password_login_failure_backoff(self.cookie_id)
            )
            logger.error(f"【{self.cookie_id}】Cookie刷新或实例重启失败: {self._safe_str(refresh_e)}")
            import traceback
            logger.error(f"【{self.cookie_id}】详细堆栈:\n{traceback.format_exc()}")
            if protected:
                return False
            if refresh_risk_log_id:
                self._update_risk_log(
                    refresh_risk_log_id,
                    session_id=risk_session_id,
                    trigger_scene=trigger_scene,
                    result_code='cookie_refresh_exception',
                    processing_status='failed',
                    error_message=str(refresh_e)[:200],
                    duration_ms=max(0, int((time.time() - risk_log_started_at) * 1000)),
                    event_meta=self._build_risk_event_meta(trigger_scene=trigger_scene, extra=base_event_meta),
                )
            return False
        finally:
            if recovery_lock_acquired:
                self.release_auth_recovery_lock(self.cookie_id, recovery_lock_owner)

