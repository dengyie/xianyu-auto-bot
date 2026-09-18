"""密码登录必须具备整体时限。

`_run_sync_method_on_fresh_thread` 把同步登录丢进 daemon 线程，线程无法取消：
Chromium 被 OOM 或 CDP 假死时那个 future 永不完成。若调用方无限等待，
token_refresh_lock 与 last_token_refresh_status 会被永久占住，账号静默停摆
且无退避、无重试、无告警（2026-09-16 事故根因）。
"""
import asyncio
from pathlib import Path

import pytest


def test_password_login_await_is_bounded_by_deadline():
    """源契约：密码登录的 await 必须包在 asyncio.wait_for 里，超时走专门收口。"""
    src = Path("xianyu_auth_recovery.py").read_text(encoding="utf-8")

    assert "_PASSWORD_LOGIN_DEADLINE_SECONDS" in src
    assert "asyncio.wait_for(" in src
    assert "except asyncio.TimeoutError:" in src
    assert "_handle_password_login_timeout(" in src
    # 超时后必须杀残留浏览器，否则 profile 锁与内存会被继续占用
    assert "_force_kill_timeout_browser(" in src


@pytest.mark.asyncio
async def test_timeout_handler_records_backoff_status_and_notification():
    """超时收口必须置失败状态、写退避、记风控日志并通知——不能静默。"""
    import xianyu_auth_recovery as mod

    recorded = {}

    class FakeHost(mod.XianyuAuthRecoveryMixin):
        cookie_id = "c-timeout"

        def __init__(self):
            self.last_token_refresh_status = 'started'
            self.last_token_refresh_error_message = ''
            self.risk_logs = []
            self.notifications = []

        def classify_password_login_failure(self, message):
            recorded['classified'] = message
            return ('slider_failed', 3600)

        def set_password_login_failure_backoff(self, cookie_id, reason, seconds):
            recorded['backoff'] = (cookie_id, reason, seconds)

        def _build_risk_event_meta(self, trigger_scene=None, extra=None):
            return {'scene': trigger_scene, **(extra or {})}

        def _update_risk_log(self, log_id, **kwargs):
            self.risk_logs.append((log_id, kwargs))

        async def send_token_refresh_notification(self, message, notification_type, **kwargs):
            self.notifications.append((message, notification_type))

        def _force_kill_timeout_browser(self, slider):
            recorded['killed_slider'] = slider
            return '已清理 Chromium 进程数=1'

    host = FakeHost()
    fake_slider = object()

    await host._handle_password_login_timeout(
        fake_slider,
        risk_session_id='risk-timeout',
        refresh_risk_log_id=42,
        trigger_scene='token_refresh',
        base_event_meta={'cookie_id': 'c-timeout'},
        risk_log_started_at=0.0,
    )

    assert recorded['killed_slider'] is fake_slider
    assert host.last_token_refresh_status == 'failed'
    assert '超' in host.last_token_refresh_error_message
    assert recorded['backoff'] == ('c-timeout', 'slider_failed', 3600)

    log_id, kwargs = host.risk_logs[0]
    assert log_id == 42
    assert kwargs['result_code'] == 'password_login_timeout'
    assert kwargs['processing_status'] == 'failed'
    assert kwargs['session_id'] == 'risk-timeout'
    assert kwargs['event_meta']['backoff_reason'] == 'slider_failed'

    assert host.notifications
    assert host.notifications[0][1] == 'password_login_timeout'


def test_force_kill_timeout_browser_never_raises():
    """清理是尽力而为：slidex 缺失或目录解析失败都不能把异常抛给主流程。"""
    import xianyu_auth_recovery as mod

    class FakeHost(mod.XianyuAuthRecoveryMixin):
        cookie_id = "c-kill"

        def _safe_str(self, e):
            return str(e)

    class FakeSlider:
        def _resolve_account_persistent_profile_dir(self):
            return '/tmp/definitely-not-a-real-profile-dir'

    note = FakeHost()._force_kill_timeout_browser(FakeSlider())

    assert isinstance(note, str)
    assert note
