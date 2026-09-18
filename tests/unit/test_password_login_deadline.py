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

        def _force_kill_timeout_browser(self, slider, captured_browser_pids=None):
            recorded['killed_slider'] = slider
            recorded['captured_browser_pids'] = captured_browser_pids
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


class _FakeLifecycle:
    """替身 slidex._chromium_lifecycle，记录被杀 PID。"""

    def __init__(self, dir_pids=()):
        self.dir_pids = list(dir_pids)
        self.killed = []
        self.dir_queries = []

    def find_chromium_pids_by_user_data_dir(self, user_data_dir):
        self.dir_queries.append(user_data_dir)
        return list(self.dir_pids)

    def kill_chromium_process_tree(self, pid):
        self.killed.append(pid)
        return 1


@pytest.fixture
def fake_lifecycle(monkeypatch):
    """把假 lifecycle 模块挂进 sys.modules，拦截 `from slidex import ...`。"""
    import sys
    import types

    holder = {}

    def _install(dir_pids=()):
        lifecycle = _FakeLifecycle(dir_pids)
        pkg = types.ModuleType("slidex")
        pkg._chromium_lifecycle = lifecycle
        monkeypatch.setitem(sys.modules, "slidex", pkg)
        monkeypatch.setitem(sys.modules, "slidex._chromium_lifecycle", lifecycle)
        holder["lifecycle"] = lifecycle
        return lifecycle

    holder["install"] = _install
    return holder


def _kill_host():
    import xianyu_auth_recovery as mod

    class FakeHost(mod.XianyuAuthRecoveryMixin):
        cookie_id = "c-kill"

        def _safe_str(self, e):
            return str(e)

    return FakeHost()


class _FakeSlider:
    def __init__(self, launcher=None):
        self.launcher_calls = []
        if launcher is not None:
            self._launch_clean_cookie_seeded_context = launcher

    def _resolve_account_persistent_profile_dir(self):
        return '/tmp/fake-profile-dir'


def test_force_kill_timeout_browser_kills_captured_pids(fake_lifecycle):
    """干净上下文无 user-data-dir：捕获到的 PID 必须能被杀掉（P2#1 回归）。"""
    lifecycle = fake_lifecycle["install"](dir_pids=[])
    slider = _FakeSlider()

    note = _kill_host()._force_kill_timeout_browser(slider, [4321, 4322])

    assert lifecycle.killed == [4321, 4322]
    assert '已清理 Chromium 进程数=2' in note


def test_force_kill_timeout_browser_falls_back_to_profile_dir(fake_lifecycle):
    """没有捕获到 PID 时（持久化上下文路径），仍按 profile 目录兜底清理。"""
    lifecycle = fake_lifecycle["install"](dir_pids=[777])
    slider = _FakeSlider()

    note = _kill_host()._force_kill_timeout_browser(slider, [])

    assert lifecycle.killed == [777]
    assert lifecycle.dir_queries == ['/tmp/fake-profile-dir']
    assert '已清理 Chromium 进程数=1' in note


def test_force_kill_timeout_browser_dedupes_and_ignores_bad_pids(fake_lifecycle):
    """同一 PID 不得重复杀；非法 PID 值要跳过而不是抛异常。"""
    lifecycle = fake_lifecycle["install"](dir_pids=[4321])

    note = _kill_host()._force_kill_timeout_browser(_FakeSlider(), [4321, 'x', None, 0])

    assert lifecycle.killed == [4321]
    assert '已清理 Chromium 进程数=1' in note


def test_install_pid_capture_records_new_chromium_pids(monkeypatch):
    """启动干净上下文前后的进程集合 diff 必须落进 sink，供超时收口使用。"""
    import xianyu_auth_recovery as mod

    sequences = [{100, 101}, {100, 101, 555}]
    monkeypatch.setattr(
        mod, '_iter_system_chromium_pids', lambda: sequences.pop(0) if sequences else {100, 101, 555}
    )

    launched = []

    class _Launcher:
        def __call__(self):
            launched.append(True)
            return ('browser', 'context')

    class Slider:
        _launch_clean_cookie_seeded_context = _Launcher()

    sink = []
    slider = Slider()
    note = _kill_host()._install_timeout_browser_pid_capture(slider, sink)

    assert '已安装' in note
    # 安装后真正调用被包装的启动器，才会发生前后 diff
    assert slider._launch_clean_cookie_seeded_context() == ('browser', 'context')
    assert launched == [True]
    assert sink == [555]


def test_install_pid_capture_is_idempotent():
    """重复安装不得层层包裹启动器（否则同一次启动会被重复 diff）。"""
    import xianyu_auth_recovery as mod

    class Slider:
        def _launch_clean_cookie_seeded_context(self):
            return ('b', 'c')

    slider = Slider()
    host = _kill_host()

    assert '已安装' in host._install_timeout_browser_pid_capture(slider, [])
    first = slider._launch_clean_cookie_seeded_context
    assert '已安装' in host._install_timeout_browser_pid_capture(slider, [])
    assert slider._launch_clean_cookie_seeded_context is first


def test_capture_is_installed_at_password_login_call_site():
    """源契约：超时收口依赖 captured_browser_pids，调用点必须安装捕获并回传。"""
    src = Path("xianyu_auth_recovery.py").read_text(encoding="utf-8")

    assert "_install_timeout_browser_pid_capture(" in src
    assert "captured_browser_pids=captured_browser_pids" in src
    # 安装必须发生在 await 之前，否则超时时启动器已被丢弃、PID 无从捕获
    assert src.index("_install_timeout_browser_pid_capture(slider, captured_browser_pids)") < src.index(
        "timeout=_PASSWORD_LOGIN_DEADLINE_SECONDS,"
    )
