"""滑块成功后的两条防线回归（2026-10-06）。

背景（2026-10-05 生产实锤）：用户拖过滑块、x5sec 已合并入库，但 13s 内浏览器
侧重试与 aiohttp 回退两次被服务端重判（FAIL_SYS_USER_VALIDATE），每次失败又
触发新滑块求解 → 预算烧光进入冷却；而冷却期初始化循环仍 10s 一发自锤——
1) 通用 except 吞掉预算门控的 InitAuthError 并改写状态，使
   _calculate_retry_delay 的 max(300, 剩余冷却) 分支永远失效；
2) 无票据沉淀期：刚合并的 x5sec 立刻被新一轮求解/重试挥霍掉。

本组测试钉住：InitAuthError 放行（源码契约）、沉淀期先于预算门控（源码契约）、
沉淀期剩余时间语义、settling 状态的延迟分支、成功链路记录两阶段落库。
"""
import asyncio
import inspect
import time

import pytest

from XianyuAutoAsync import XianyuLive
from xianyu_token_mixins import TokenMixin


def _make_live():
    m = XianyuLive.__new__(XianyuLive)
    m.cookie_id = "1926782908"
    m.connection_failures = 0
    m.last_token_refresh_status = None
    m.last_x5sec_merged_at = 0.0
    m._pending_slider_success_record = None
    m._is_account_pause_status = lambda s: False
    m._is_in_qr_login_grace_period = lambda t: False
    return m


# ---------- 沉淀期剩余时间语义 ----------

def test_settle_remaining_zero_without_merge(monkeypatch):
    monkeypatch.delenv("XY_SLIDER_X5SEC_SETTLE_PERIOD", raising=False)
    assert _make_live()._x5sec_settle_remaining() == 0


def test_settle_remaining_counts_from_merge(monkeypatch):
    monkeypatch.delenv("XY_SLIDER_X5SEC_SETTLE_PERIOD", raising=False)
    m = _make_live()
    m.last_x5sec_merged_at = time.time() - 10
    remaining = m._x5sec_settle_remaining()
    assert 285 <= remaining <= 300


def test_settle_period_env_zero_disables(monkeypatch):
    monkeypatch.setenv("XY_SLIDER_X5SEC_SETTLE_PERIOD", "0")
    m = _make_live()
    m.last_x5sec_merged_at = time.time()
    assert m._x5sec_settle_remaining() == 0


def test_settle_period_env_invalid_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("XY_SLIDER_X5SEC_SETTLE_PERIOD", "abc")
    m = _make_live()
    m.last_x5sec_merged_at = time.time()
    assert 0 < m._x5sec_settle_remaining() <= 300


def test_settle_remaining_expires_to_zero(monkeypatch):
    monkeypatch.delenv("XY_SLIDER_X5SEC_SETTLE_PERIOD", raising=False)
    m = _make_live()
    m.last_x5sec_merged_at = time.time() - 3600
    assert m._x5sec_settle_remaining() == 0


# ---------- settling 状态的延迟分支 ----------

def test_settling_status_delay_uses_remaining(monkeypatch):
    monkeypatch.delenv("XY_SLIDER_X5SEC_SETTLE_PERIOD", raising=False)
    m = _make_live()
    m.last_token_refresh_status = "x5sec_settling"
    m.last_x5sec_merged_at = time.time() - 10
    delay = m._calculate_retry_delay("Token获取失败(status=x5sec_settling)")
    assert 60 <= delay <= 300
    assert delay == max(60, m._x5sec_settle_remaining())


def test_settling_status_expired_delay_floors_at_60(monkeypatch):
    monkeypatch.delenv("XY_SLIDER_X5SEC_SETTLE_PERIOD", raising=False)
    m = _make_live()
    m.last_token_refresh_status = "x5sec_settling"
    m.last_x5sec_merged_at = time.time() - 3600
    assert m._calculate_retry_delay("x") == 60


def test_budget_cooldown_delay_unchanged():
    m = _make_live()
    m.last_token_refresh_status = "slider_budget_cooldown"
    m._slider_budget_cooldown_remaining = lambda: 473
    assert m._calculate_retry_delay("Token获取失败(status=slider_budget_cooldown)") == 473


# ---------- 源码契约：异常放行 + 沉淀期先于预算门控 ----------

def test_init_auth_error_reraised_before_generic_except():
    """P1：预算门控/沉淀期抛的 InitAuthError 必须在通用 except 之前原样上传。

    通用处理器改写 last_token_refresh_status 并吞掉异常，会让
    _calculate_retry_delay 的冷却分支失效（09-28 修复的复发路径）。
    """
    src = inspect.getsource(TokenMixin._refresh_token_impl)
    guard = src.find("except _host.InitAuthError:")
    generic = src.find("except Exception as e:")
    assert guard != -1 and generic != -1, "InitAuthError 放行分支缺失"
    assert guard < generic, "InitAuthError 放行必须位于通用 except 之前"
    block = src[guard:generic]
    assert "raise" in block, "InitAuthError 分支必须 re-raise"


def test_settle_gate_precedes_budget_gate():
    """P2：票据沉淀期判定必须先于预算门控——期内被重判时先沉淀再谈预算。"""
    src = inspect.getsource(TokenMixin._refresh_token_impl)
    assert src.find("x5sec_settling") != -1
    assert src.find("x5sec_settling") < src.find("_slider_budget_gate()")


def test_retry_delay_has_settling_branch():
    src = inspect.getsource(XianyuLive._calculate_retry_delay)
    assert "x5sec_settling" in src
    assert "_x5sec_settle_remaining" in src


# ---------- 成功链路记录：两阶段落库 ----------

def _sample_record(run_id="run-test-1"):
    return {
        "schema_version": 1,
        "run_id": run_id,
        "cookie_id": "1926782908",
        "outcome": "voucher_harvest",
        "duration_s": 12.34,
        "voucher": {"bx_voucher_present": True, "x5sec_len": 198, "settle_source": "bx_header"},
        "fingerprint": {"ua": "UA-X", "glRenderer": "ANGLE (Mesa)"},
        "environment": {"mode": "cdp", "backend": "playwright", "channel": None,
                        "slidex_version": "0.6.29"},
    }


def test_upsert_slider_success_record_two_phase():
    from db_manager import db_manager
    try:
        first_id = db_manager.upsert_slider_success_record(_sample_record())
        assert first_id
        rows = db_manager.get_slider_success_records(cookie_id="1926782908")
        assert rows and rows[0]["run_id"] == "run-test-1"
        assert rows[0]["final_outcome"] == "slider_pass"
        assert rows[0]["mode"] == "cdp"
        assert rows[0]["duration_ms"] == 12340

        # token 成功：同 run_id 二次 upsert 只更新结局，不产生新行
        payload = _sample_record()
        payload["bot_context"] = {"final_outcome": "token_success", "token_path": "browser"}
        second_id = db_manager.upsert_slider_success_record(payload)
        assert second_id == first_id
        rows = db_manager.get_slider_success_records(cookie_id="1926782908")
        assert len(rows) == 1
        assert rows[0]["final_outcome"] == "token_success"
        assert rows[0]["bot_context"]["token_path"] == "browser"
        assert rows[0]["record_json"]["voucher"]["x5sec_len"] == 198
    finally:
        db_manager._execute_sql(
            db_manager.conn.cursor(), "DELETE FROM slider_success_records WHERE run_id = 'run-test-1'"
        )
        db_manager.conn.commit()


def test_upsert_requires_run_id():
    from db_manager import db_manager
    assert db_manager.upsert_slider_success_record({"cookie_id": "x"}) is None
    assert db_manager.upsert_slider_success_record(None) is None
    assert db_manager.upsert_slider_success_record("not-a-dict") is None


def test_capture_slider_success_record_writes_and_stashes():
    from db_manager import db_manager
    m = _make_live()
    try:
        m._capture_slider_success_record(_sample_record("run-capture-1"))
        assert m._pending_slider_success_record["run_id"] == "run-capture-1"
        rows = db_manager.get_slider_success_records(cookie_id="1926782908")
        assert any(r["run_id"] == "run-capture-1" for r in rows)
        # 空记录：不 stash、不落库、不抛
        m._capture_slider_success_record(None)
        assert m._pending_slider_success_record is None
    finally:
        db_manager._execute_sql(
            db_manager.conn.cursor(), "DELETE FROM slider_success_records WHERE run_id = 'run-capture-1'"
        )
        db_manager.conn.commit()


def test_finalize_persists_token_outcome():
    from db_manager import db_manager
    m = _make_live()
    m._pending_slider_success_record = _sample_record("run-final-1")
    m.proxy_config = {"server": "http://x"}

    async def _noop(*a, **k):
        return None

    m._clear_qr_login_grace_period = _noop
    m.clear_init_auth_failure_state = lambda cid: None
    m._slider_budget_reset = lambda: None
    m._consume_pending_slider_success_notice = lambda: False
    m.send_token_refresh_notification = _noop

    try:
        result = asyncio.run(m._finalize_token_success("token-x", token_path="browser"))
        assert result == "token-x"
        assert m._pending_slider_success_record is None  # 消费即清
        rows = db_manager.get_slider_success_records(cookie_id="1926782908")
        row = next(r for r in rows if r["run_id"] == "run-final-1")
        assert row["final_outcome"] == "token_success"
        assert row["bot_context"]["token_path"] == "browser"
        assert row["bot_context"]["account_proxy"] is True
    finally:
        db_manager._execute_sql(
            db_manager.conn.cursor(), "DELETE FROM slider_success_records WHERE run_id = 'run-final-1'"
        )
        db_manager.conn.commit()
