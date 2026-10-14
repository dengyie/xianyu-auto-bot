"""日志降噪三项 + 初始化鉴权熔断窗口回归(2026-10-14)。

线上复核:38 次 init 鉴权失败全部记为 `(1/3)`、`进入冷静期` 0 次,原因是
`_init_auth_failure_window` 只有 60s,而实际重试间隔(票据沉淀 219~247s、
预算冷却 1799s)远大于它 —— 每次重试都开出新窗口,计数器永远回不到 3,
熔断器形同虚设。窗口放宽到 600s 后必须:连败 3 次真的进入冷静期,且一次
刻意的长冷却(>窗口)仍能重置计数、不误触发。

同时锁定三处日志等级:截图回退失败不再 ERROR/WARNING 刷屏、Cookie 更新
成功走 INFO、后台任务快照走 DEBUG。
"""
import asyncio
import time

from loguru import logger

from XianyuAutoAsync import XianyuLive


def _capture_loguru_records():
    records = []
    sink_id = logger.add(lambda msg: records.append(msg.record), level="DEBUG")
    return records, sink_id


def _levels(records, needle):
    return [r["level"].name for r in records if needle in r["message"]]


# --------------------------------------------------------------------------
# 项四:初始化鉴权失败熔断窗口
# --------------------------------------------------------------------------

class _BreakerHost(XianyuLive):
    """借用熔断 classmethod,不构造完整 XianyuLive 实例。"""


def test_breaker_window_covers_throttled_retry_cadence():
    """线上真实节流节奏(≈240s/次)连败 3 次必须进入冷静期。"""
    cls = _BreakerHost
    cid = f"breaker-cadence-{time.time_ns()}"
    try:
        cls.record_init_auth_failure(cid, "账号被限流")
        # 把首次失败时间前移 240s,复刻线上"票据沉淀期"重试节奏
        with cls._init_auth_failure_lock:
            cls._init_auth_failure_state[cid]['window_started_at'] = time.time() - 240

        state = cls.record_init_auth_failure(cid, "账号被限流")
        assert state['count'] == 2, f"240s 间隔应落在窗口内累计: {state}"

        with cls._init_auth_failure_lock:
            cls._init_auth_failure_state[cid]['window_started_at'] = time.time() - 200
        state = cls.record_init_auth_failure(cid, "账号被限流")
        assert state['count'] == 3, f"第三次应到达阈值: {state}"
        assert state['circuit_until'] > time.time(), f"到达阈值必须开冷却: {state}"
    finally:
        cls.clear_init_auth_failure_state(cid)


def test_breaker_window_resets_after_long_cooldown():
    """超过窗口的一次刻意长冷却(1799s 预算冷却)后计数必须归零,不误触发。"""
    cls = _BreakerHost
    cid = f"breaker-reset-{time.time_ns()}"
    try:
        cls.record_init_auth_failure(cid, "预算冷却")
        cls.record_init_auth_failure(cid, "预算冷却")
        with cls._init_auth_failure_lock:
            cls._init_auth_failure_state[cid]['window_started_at'] = time.time() - 700
        state = cls.record_init_auth_failure(cid, "预算冷却")
        assert state['count'] == 1, f"700s > 600s 窗口应开新窗口: {state}"
        assert state['circuit_until'] == 0, "重置后不得残留冷却"
    finally:
        cls.clear_init_auth_failure_state(cid)


def test_breaker_window_is_wider_than_observed_cadence():
    """窗口须显著大于线上节流重试间隔,否则熔断器是死代码;又须小于预算冷却间隔。"""
    assert XianyuLive._init_auth_failure_window >= 480
    assert XianyuLive._init_auth_failure_window < 1799


# --------------------------------------------------------------------------
# 项一:验证截图回退失败降噪
# --------------------------------------------------------------------------

class _ClosedPage:
    def query_selector(self, *a, **k):
        raise Exception("Target page, context or browser has been closed")

    def screenshot(self, *a, **k):
        raise Exception("Target page, context or browser has been closed")


class _ClosedFrame:
    def frame_element(self):
        raise Exception("Target page, context or browser has been closed")


def _make_slider_stub():
    # 必须经宿主模块导入:slider_stealth_mixins 依赖 _host 惰性代理,
    # 直接 import 该模块会触发循环导入。
    from utils.xianyu_slider_stealth import XianyuSliderStealth

    m = XianyuSliderStealth.__new__(XianyuSliderStealth)
    m.pure_user_id = "test-user"
    m._is_timed_out_verification_text = lambda t: False
    m._read_frame_text_for_detection = lambda f: ""
    m._collect_page_text_for_detection = lambda p: ""
    return m


def _screenshot_log_levels(records):
    return [r["level"].name for r in records if "截图" in r["message"] or "方式" in r["message"]]


def test_screenshot_closed_browser_logs_info_not_error():
    m = _make_slider_stub()
    import utils.slider_stealth_mixins as mod

    records, sink_id = _capture_loguru_records()
    orig_sleep = mod.time.sleep
    mod.time.sleep = lambda *_: None
    try:
        result = m._capture_verification_screenshot(
            page=_ClosedPage(), frame=_ClosedFrame(), iframe_selector=None
        )
    finally:
        mod.time.sleep = orig_sleep
        logger.remove(sink_id)

    assert result is None
    assert _levels(records, "截图跳过") == ["INFO"], _levels(records, "截图跳过")
    assert not _levels(records, "所有截图方式均失败"), "closed 分支不应再打通用失败日志"
    levels = _screenshot_log_levels(records)
    assert "ERROR" not in levels and "WARNING" not in levels, levels


def test_screenshot_generic_failure_logs_debug():
    m = _make_slider_stub()
    import utils.slider_stealth_mixins as mod

    records, sink_id = _capture_loguru_records()
    orig_sleep = mod.time.sleep
    mod.time.sleep = lambda *_: None
    try:
        result = m._capture_verification_screenshot(page=None, frame=None, iframe_selector="")
    finally:
        mod.time.sleep = orig_sleep
        logger.remove(sink_id)

    assert result is None
    assert _levels(records, "所有截图方式均失败") == ["DEBUG"]
    levels = _screenshot_log_levels(records)
    assert "ERROR" not in levels and "WARNING" not in levels, levels


# --------------------------------------------------------------------------
# 项二:Cookie 更新成功走 INFO
# --------------------------------------------------------------------------

class _CookieHost:
    """最小宿主:update_config_cookies 只用到 cookie_id / cookies_str / user_id。"""

    cookie_id = "1926782908"
    cookies_str = "unb=1; _m_h5_tk=abc"
    user_id = 1

    def _safe_str(self, e):
        return str(e)


def test_cookie_update_success_logs_info(monkeypatch):
    class _FakeDB:
        @staticmethod
        def update_cookie_account_info(*a, **k):
            return True

    import xianyu_cookie_mixin as cookie_mod

    monkeypatch.setattr(cookie_mod, "_db_package", lambda: _FakeDB())

    m = _CookieHost()
    records, sink_id = _capture_loguru_records()
    try:
        asyncio.run(cookie_mod.CookieMixin.update_config_cookies(m))
    finally:
        logger.remove(sink_id)

    assert _levels(records, "已更新Cookie到数据库") == ["INFO"], (
        [(r["level"].name, r["message"]) for r in records]
    )


# --------------------------------------------------------------------------
# 项三:后台任务快照走 DEBUG(直接驱真实方法,不做源码字符串断言)
# --------------------------------------------------------------------------

def test_background_task_snapshot_logs_debug():
    m = XianyuLive.__new__(XianyuLive)
    m.cookie_id = "1926782908"
    for name in ("heartbeat_task", "token_refresh_task", "cleanup_task",
                 "cookie_refresh_task", "stream_watchdog_task"):
        setattr(m, name, None)

    records, sink_id = _capture_loguru_records()
    try:
        m._log_background_task_snapshot()
    finally:
        logger.remove(sink_id)

    assert _levels(records, "准备启动后台任务") == ["DEBUG"]


# --------------------------------------------------------------------------
# 项五:冷静期日志必须报最终生效等待(2026-10-15 线上复核)
#
# `retry_delay = max(retry_delay, circuit_wait)` 保证冷静期只抬高下限、绝不缩短。
# 原实现打印的是 circuit_wait,于是 max(1800,59)=1800s 却打"冷静期 59 秒",误导。
# --------------------------------------------------------------------------

def _make_retry_host(status=None, cooldown_remaining=None, connection_failures=0):
    m = XianyuLive.__new__(XianyuLive)
    m.cookie_id = "1926782908"
    m.last_token_refresh_status = status
    m.connection_failures = connection_failures
    m._is_account_pause_status = lambda s: False
    m._is_in_qr_login_grace_period = lambda t: False
    if cooldown_remaining is not None:
        m._slider_budget_cooldown_remaining = lambda: cooldown_remaining
    return m


def test_circuit_cooldown_does_not_shorten_wait_and_logs_effective():
    """线下实况:重试间隔 1800s、冷静期仅 59s → 应等 1800s 且日志报 1800。"""
    m = _make_retry_host("slider_budget_cooldown", cooldown_remaining=1800)
    records, sink_id = _capture_loguru_records()
    try:
        delay = m._resolve_init_auth_retry_delay(
            "Token获取失败(status=slider_budget_cooldown)",
            {"circuit_until": time.time() + 59},
        )
    finally:
        logger.remove(sink_id)

    assert delay == 1800, f"冷静期不得缩短等待: {delay}"
    msgs = [r["message"] for r in records if "初始化鉴权失败已达到阈值" in r["message"]]
    assert msgs and "1800 秒后重试" in msgs[0], msgs
    assert "冷静期余" in msgs[0], msgs


def test_circuit_cooldown_raises_floor_when_retry_delay_small():
    """快速连败(基础重试 5s)时,冷静期 59s 必须抬高下限。"""
    m = _make_retry_host(connection_failures=0)
    cooldown = 59
    records, sink_id = _capture_loguru_records()
    try:
        delay = m._resolve_init_auth_retry_delay(
            "no close frame received or sent",
            {"circuit_until": time.time() + cooldown},
        )
    finally:
        logger.remove(sink_id)

    # 冷静期剩余由整数截断,允许 1s 误差(CI 慢机器上会取到 58)
    assert cooldown - 1 <= delay <= cooldown, f"冷静期应抬高下限: {delay}"
    msgs = [r["message"] for r in records if "初始化鉴权失败已达到阈值" in r["message"]]
    assert msgs, msgs
    # 日志必须报最终生效值(与返回一致),而不是写死的 59
    assert f"{delay} 秒后重试" in msgs[0], msgs


def test_no_active_circuit_logs_plain_retry():
    """冷静期已过 / 未开 → 打普通重试日志,报按错误类型算出的间隔。"""
    m = _make_retry_host("slider_budget_cooldown", cooldown_remaining=1800)
    records, sink_id = _capture_loguru_records()
    try:
        delay = m._resolve_init_auth_retry_delay(
            "Token获取失败(status=slider_budget_cooldown)", {}
        )
    finally:
        logger.remove(sink_id)

    assert delay == 1800
    assert any("将在 1800 秒后重试初始化鉴权" in r["message"] for r in records)