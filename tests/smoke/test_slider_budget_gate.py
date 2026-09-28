"""滑块求解预算门控回归（2026-09-28 整夜锤风控的根治）。

token 刷新链每次被挑战都消耗一次风控惩罚额度；预算耗尽 → 冷却拒绝求解，
窗口过后自动放行探测周期；Token 成功清零预算。
"""
import os

import pytest
from unittest import mock

import xianyu_token_mixins
from xianyu_token_mixins import TokenMixin, _slider_budget_lock, _slider_budget_state


@pytest.fixture(autouse=True)
def _clean_budget():
    with _slider_budget_lock:
        _slider_budget_state.clear()
    yield
    with _slider_budget_lock:
        _slider_budget_state.clear()


def _make_mixin(monkeypatch, budget="3", cooldown="600"):
    monkeypatch.setenv("XY_SLIDER_SOLVE_BUDGET", budget)
    monkeypatch.setenv("XY_SLIDER_SOLVE_COOLDOWN", cooldown)
    m = TokenMixin.__new__(TokenMixin)
    m.cookie_id = "1926782908"
    m._safe_str = staticmethod(lambda e: str(e))
    return m


def test_budget_allows_up_to_limit_then_defers_with_cooldown(monkeypatch):
    monkeypatch.setattr(xianyu_token_mixins, "_slider_budget_state", _slider_budget_state)
    m = _make_mixin(monkeypatch, budget="3")
    for _ in range(3):
        exhausted, _ = m._slider_budget_gate()
        assert exhausted is False
    exhausted, remaining = m._slider_budget_gate()
    assert exhausted is True
    assert remaining == 600  # 进入冷却


def test_cooldown_blocks_until_expiry_then_reopens(monkeypatch):
    monkeypatch.setattr(xianyu_token_mixins, "_slider_budget_state", _slider_budget_state)
    m = _make_mixin(monkeypatch, budget="1", cooldown="120")
    assert m._slider_budget_gate()[0] is False
    exhausted, remaining = m._slider_budget_gate()
    assert exhausted is True and 100 <= remaining <= 120
    # 冷却未到期 → 持续拒绝
    assert m._slider_budget_gate()[0] is True


def test_token_success_resets_budget(monkeypatch):
    monkeypatch.setattr(xianyu_token_mixins, "_slider_budget_state", _slider_budget_state)
    m = _make_mixin(monkeypatch, budget="2")
    m._slider_budget_gate()
    m._slider_budget_gate()
    m._slider_budget_reset()
    exhausted, _ = m._slider_budget_gate()
    assert exhausted is False


def test_state_is_per_cookie(monkeypatch):
    monkeypatch.setattr(xianyu_token_mixins, "_slider_budget_state", _slider_budget_state)
    m1 = _make_mixin(monkeypatch, budget="2")
    m2 = TokenMixin.__new__(TokenMixin)
    m2.cookie_id = "other_account"
    m1._slider_budget_gate()
    m1._slider_budget_gate()
    exhausted, _ = m2._slider_budget_gate()
    assert exhausted is False  # 其他账号预算互不影响
