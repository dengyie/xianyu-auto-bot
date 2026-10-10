"""风控惩罚(300 other-punish)退避语义回归。

惩罚类失败与本地滑块失败必须区别对待:
- slider_punish: 2x 陡峭升级 + 4h 封顶, 计入连续失败保护, 不被"最近刚过滑块"旁路;
- slider_failed: 维持全局 1.5x + 1h 默认曲线, 保留旁路。
短间隔重探只会加深设备判罚, 所以曲线与旁路语义是根因修复的一部分。
"""
from __future__ import annotations

import pytest

import xianyu_auth_recovery as mod
import xianyu_token_mixins
from xianyu_token_mixins import TokenMixin, _slider_budget_lock, _slider_budget_state


class _BackoffHost(mod.XianyuAuthRecoveryMixin):
    _password_login_failure_backoff = {}
    cookie_id = "1926782908"

    def _has_recent_slider_success(self):
        return True

    def consume_manual_refresh_slider_failed_bypass(self, cookie_id):
        return False


@pytest.fixture(autouse=True)
def _clean_state():
    _BackoffHost._password_login_failure_backoff.clear()
    with _slider_budget_lock:
        _slider_budget_state.clear()
    yield
    _BackoffHost._password_login_failure_backoff.clear()
    with _slider_budget_lock:
        _slider_budget_state.clear()


class TestCountedReason:
    def test_slider_punish_is_counted(self):
        assert _BackoffHost._is_counted_password_login_failure_reason("slider_punish") is True
        assert _BackoffHost._is_counted_password_login_failure_reason("slider_failed") is True
        assert _BackoffHost._is_counted_password_login_failure_reason("risk_control") is True

    def test_unrelated_reasons_not_counted(self):
        for reason in ("", None, "credentials", "verification_required", "unknown"):
            assert _BackoffHost._is_counted_password_login_failure_reason(reason) is False


class TestEscalationCurve:
    def test_punish_curve_doubles_and_caps_at_4h(self):
        host = _BackoffHost()
        seen = []
        for _ in range(7):
            host.set_password_login_failure_backoff(
                host.cookie_id, "slider_punish", 600,
                escalation_factor=2.0, max_cap_seconds=14400,
            )
            seen.append(host.get_password_login_failure_backoff(host.cookie_id)["seconds"])
        assert seen == [600, 1200, 2400, 4800, 9600, 14400, 14400]

    def test_slider_failed_keeps_default_curve(self):
        host = _BackoffHost()
        seen = []
        for _ in range(4):
            host.set_password_login_failure_backoff(host.cookie_id, "slider_failed", 600)
            seen.append(host.get_password_login_failure_backoff(host.cookie_id)["seconds"])
        # 全局默认 1.5x + 1h 封顶
        assert seen == [600, 900, 1350, 2025]

    def test_reason_switch_resets_consecutive_count(self):
        host = _BackoffHost()
        host.set_password_login_failure_backoff(
            host.cookie_id, "slider_punish", 600, escalation_factor=2.0, max_cap_seconds=14400
        )
        host.set_password_login_failure_backoff(
            host.cookie_id, "slider_punish", 600, escalation_factor=2.0, max_cap_seconds=14400
        )
        host.set_password_login_failure_backoff(host.cookie_id, "slider_failed", 600)
        assert host.get_password_login_failure_backoff(host.cookie_id)["seconds"] == 600


class TestSkipWhitelist:
    def test_punish_backoff_skips_token_precheck(self):
        host = _BackoffHost()
        host.last_token_refresh_status = "started"
        host.set_password_login_failure_backoff(
            host.cookie_id, "slider_punish", 600, escalation_factor=2.0, max_cap_seconds=14400
        )
        assert host._should_skip_token_refresh_for_login_backoff() is True


class TestBypassSemantics:
    def test_recent_success_does_not_bypass_punish(self):
        host = _BackoffHost()
        host.set_password_login_failure_backoff(
            host.cookie_id, "slider_punish", 600, escalation_factor=2.0, max_cap_seconds=14400
        )
        # 惩罚是设备级判罚, 不该被"最近刚过滑块"清掉
        assert host._get_active_password_login_failure_backoff() is not None

    def test_recent_success_bypasses_slider_failed(self):
        host = _BackoffHost()
        host.set_password_login_failure_backoff(host.cookie_id, "slider_failed", 600)
        assert host._get_active_password_login_failure_backoff() is None


class TestBudgetDefault:
    def test_default_budget_is_two(self, monkeypatch):
        monkeypatch.delenv("XY_SLIDER_SOLVE_BUDGET", raising=False)
        monkeypatch.setattr(xianyu_token_mixins, "_slider_budget_state", _slider_budget_state)
        m = TokenMixin.__new__(TokenMixin)
        m.cookie_id = "1926782908"
        m._safe_str = staticmethod(lambda e: str(e))
        assert m._slider_budget_gate()[0] is False
        assert m._slider_budget_gate()[0] is False
        exhausted, _ = m._slider_budget_gate()
        assert exhausted is True, "默认预算应为 2"
