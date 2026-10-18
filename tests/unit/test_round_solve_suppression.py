"""同一轮内求解抑制回归（2026-10-19）。

生产实测（xianyu_2026-10-19.log，账号 1926782908 处于上游惩罚窗口）：

    03:02:31 初始化鉴权失败第1次 → 10s 后重试
    03:02:46 检测到滑块 → 求解#1 → 03:03:25 求解"成功"
    03:04:21 重判 → 票据沉淀期（剩余 243s）→ 不求解（沉淀期守卫生效）
    03:04:22 初始化鉴权失败第2次 → 242s 后重试
    03:08:29 检测到滑块 → 求解#2 → 03:09:01 求解"成功"
    03:10:19 重判 → 票据沉淀期（剩余 222s）→ 初始化鉴权失败第3次（熔断）
    03:14:05 求解预算耗尽 → 冷却 1800s
    03:44:11 Token刷新成功（惩罚窗口自行结束，零人工、零求解）

一轮 600s 窗口里两次求解**全部成功**，却全部被服务端立刻重判——票据没被接受，
是 baxia 会话级判罚而不是票据未生效。第二次求解是纯粹浪费：票据已经证明救不回来。
所以本轮剩下的尝试不该再求解，省下的预算留给下一个窗口的探测。

本组测试钉住：抑制窗口语义、抑制门控先于预算门控（不扣预算）、沉淀期判定即置位、
被抑制时抛状态性异常而不是走求解、Token 成功后解除抑制。
"""
from __future__ import annotations

import inspect
import time

import pytest

import xianyu_token_mixins
from XianyuAutoAsync import XianyuLive
from xianyu_token_mixins import TokenMixin, _slider_budget_lock, _slider_budget_state

_COOKIE = "1926782908"


def _make_live():
    m = XianyuLive.__new__(XianyuLive)
    m.cookie_id = _COOKIE
    m.connection_failures = 0
    m.last_token_refresh_status = None
    m.last_x5sec_merged_at = 0.0
    m._round_solve_suppressed_until = 0.0
    m._pending_slider_success_record = None
    m._is_account_pause_status = lambda s: False
    m._is_in_qr_login_grace_period = lambda t: False
    return m


@pytest.fixture(autouse=True)
def _clean_budget():
    with _slider_budget_lock:
        _slider_budget_state.clear()
    yield
    with _slider_budget_lock:
        _slider_budget_state.clear()


# ---------- 抑制窗口语义 ----------

def test_no_suppression_by_default():
    assert _make_live()._round_solve_suppression_remaining() == 0


def test_suppress_arms_window_matching_init_auth_window():
    m = _make_live()
    m._suppress_round_solve("测试置位")
    remaining = m._round_solve_suppression_remaining()
    assert 0 < remaining <= int(XianyuLive._init_auth_failure_window)


def test_suppress_is_monotonic_and_never_shortens():
    """幂等置位只拉长不缩短——否则连续判罚会把窗口越压越短，抑制形同虚设。"""
    m = _make_live()
    far_future = time.time() + 5000
    m._round_solve_suppressed_until = far_future
    m._suppress_round_solve("重复置位")
    assert m._round_solve_suppressed_until == far_future

    m._round_solve_suppressed_until = 0.0
    m._suppress_round_solve("首次置位")
    assert m._round_solve_suppression_remaining() > 0


def test_suppression_expires():
    m = _make_live()
    m._round_solve_suppressed_until = time.time() - 1
    assert m._round_solve_suppression_remaining() == 0


# ---------- 窗口长度：默认对齐初始化鉴权窗口，可 env 覆盖 ----------

def test_window_defaults_to_init_auth_window(monkeypatch):
    monkeypatch.delenv("XY_ROUND_SOLVE_SUPPRESS_WINDOW", raising=False)
    m = _make_live()
    assert m._round_solve_suppress_window() == int(XianyuLive._init_auth_failure_window)


def test_window_env_override(monkeypatch):
    monkeypatch.setenv("XY_ROUND_SOLVE_SUPPRESS_WINDOW", "900")
    assert _make_live()._round_solve_suppress_window() == 900


def test_window_env_tolerant_parsing(monkeypatch):
    """坏值一律回落默认，不能抛 ValueError 把鉴权链打断（同 settle 周期语义）。"""
    m = _make_live()
    default = int(XianyuLive._init_auth_failure_window)
    for bad in ("", "   ", "abc", "0", "-5"):
        monkeypatch.setenv("XY_ROUND_SOLVE_SUPPRESS_WINDOW", bad)
        assert m._round_solve_suppress_window() == default, f"{bad!r} 应回落默认"


def test_window_env_has_floor(monkeypatch):
    """下限 60s：窗口过短等于没抑制，判罚期内立刻又烧一次求解。"""
    monkeypatch.setenv("XY_ROUND_SOLVE_SUPPRESS_WINDOW", "5")
    assert _make_live()._round_solve_suppress_window() == 60


def test_suppress_uses_env_window(monkeypatch):
    """env 覆盖必须真的落到置位上，否则这个旋钮只是摆设。"""
    monkeypatch.setenv("XY_ROUND_SOLVE_SUPPRESS_WINDOW", "777")
    m = _make_live()
    m._suppress_round_solve("测试")
    assert 700 <= m._round_solve_suppression_remaining() <= 777


def test_window_env_inf_falls_back(monkeypatch):
    """int(float('inf')) 抛 OverflowError —— 容错解析必须兜住，坏 env 不能把鉴权链打断。"""
    m = _make_live()
    for bad in ("inf", "infinity", "INF"):
        monkeypatch.setenv("XY_ROUND_SOLVE_SUPPRESS_WINDOW", bad)
        assert m._round_solve_suppress_window() == int(XianyuLive._init_auth_failure_window), f"{bad!r} 应回落默认"


def test_settle_period_env_inf_falls_back(monkeypatch):
    """同一根因也存在于结算期解析：'inf' 回落默认 300s，不得抛异常。"""
    m = _make_live()
    m.last_x5sec_merged_at = time.time()
    for bad in ("inf", "infinity"):
        monkeypatch.setenv("XY_SLIDER_X5SEC_SETTLE_PERIOD", bad)
        assert 0 < m._x5sec_settle_remaining() <= 300, f"{bad!r} 应回落默认"


# ---------- 延迟分支 ----------

def test_suppressed_status_delay_tracks_remaining():
    m = _make_live()
    m.last_token_refresh_status = "risk_round_solve_suppressed"
    m._round_solve_suppressed_until = time.time() + 243
    delay = m._calculate_retry_delay("Token获取失败(status=risk_round_solve_suppressed)")
    assert 60 <= delay <= 243
    assert delay == max(60, m._round_solve_suppression_remaining())


def test_suppressed_status_delay_floors_at_60():
    """窗口已过时仍要有下限，否则 10s 级空转自锤（09-28 同类事故）。"""
    m = _make_live()
    m.last_token_refresh_status = "risk_round_solve_suppressed"
    assert m._calculate_retry_delay("x") == 60


# ---------- 门控次序：抑制必须先于预算门控 ----------

def test_suppression_gate_precedes_budget_gate():
    """被抑制的尝试不求解，自然也不该扣预算——否则窗口内的空转会把预算烧光。"""
    src = inspect.getsource(TokenMixin._refresh_token_impl)
    gate = src.find("_round_solve_suppression_remaining()")
    budget = src.find("_slider_budget_gate()")
    assert gate != -1, "抑制门控缺失"
    assert budget != -1
    assert gate < budget, "抑制门控必须先于预算门控"


def test_settle_branch_arms_suppression():
    """沉淀期判定 = 判罚签名（求解成功却立刻被重判），必须置位抑制。"""
    src = inspect.getsource(TokenMixin._refresh_token_impl)
    settle_branch = src.find("进入票据沉淀期")
    assert settle_branch != -1
    call = src.rfind("_suppress_round_solve(", 0, settle_branch)
    assert call != -1, "沉淀期判定必须置位本轮求解抑制"
    assert "_round_solve_suppressed_until" in inspect.getsource(TokenMixin._suppress_round_solve)


# ---------- 行为：一轮内只求解一次 ----------

class _FakeResp:
    def __init__(self, payload):
        self.status = 200
        self.headers = {}
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def json(self, content_type=None):
        return self._payload


class _FakeSession:
    def __init__(self, payload):
        self._payload = payload
        self.post_calls = 0

    def post(self, *args, **kwargs):
        self.post_calls += 1
        return _FakeResp(self._payload)


def _make_challenged_live(solve_calls):
    """构造一个"每次 Token 请求都被下发风控挑战"的实例（复刻惩罚窗口）。"""
    m = XianyuLive.__new__(XianyuLive)
    m.cookie_id = _COOKIE
    m.session = _FakeSession({"ret": ["FAIL_SYS_USER_VALIDATE::校验失败"]})
    m._http_proxy_url = None
    m.device_id = "device-id"
    m.cookies_str = "_m_h5_tk=test_token_12345; cookie2=dummy_cookie2"
    m.current_token = None
    m.last_token_refresh_time = 0
    m.last_message_received_time = 0
    m.message_cookie_refresh_cooldown = 0
    m.max_captcha_verification_count = 3
    m.last_token_refresh_status = None
    m.last_token_refresh_error_message = None
    m.restarted_in_browser_refresh = True
    m.last_x5sec_merged_at = 0.0
    m._round_solve_suppressed_until = 0.0
    m._reload_latest_cookies_from_db = lambda *_a, **_k: None
    m._extract_set_cookie_updates = lambda headers: {}
    m._need_captcha_verification = lambda _payload: True
    m.get_qr_login_grace = lambda _cid: None
    m.get_manual_refresh_state = lambda _cid: None
    m.is_manual_refresh_active = lambda *_a, **_k: False
    m._should_skip_token_refresh_for_login_backoff = lambda *_a, **_k: False
    m._x5sec_settle_remaining = lambda: 0
    m.pending_slider_success_notice = None
    m._update_risk_log = lambda *_a, **_k: None

    async def fake_solve(_res_json):
        solve_calls.append(time.time())
        return ""

    m._handle_captcha_verification = fake_solve
    return m


@pytest.mark.asyncio
async def test_settle_signature_arms_suppression_and_skips_solve():
    """求解成功 → 票据合并 → 下一次请求立刻被重判：第一次就置位抑制，不再求解。"""
    solve_calls = []
    m = _make_challenged_live(solve_calls)
    m.last_x5sec_merged_at = time.time() - 10  # 刚合并过票据
    m._x5sec_settle_remaining = lambda: 243

    with pytest.raises(Exception) as excinfo:
        await m._refresh_token_impl()
    assert "x5sec_settling" in str(excinfo.value)
    assert solve_calls == [], "沉淀期判定不该发起求解"
    assert m._round_solve_suppression_remaining() > 0, "判罚签名必须置位本轮抑制"
    assert m.last_token_refresh_status == "x5sec_settling"


@pytest.mark.asyncio
async def test_same_round_second_challenge_does_not_solve_again():
    """核心回归：同一轮内的第二次挑战不再求解（生产实测的求解#2）。"""
    solve_calls = []
    m = _make_challenged_live(solve_calls)
    m._round_solve_suppressed_until = time.time() + 300  # 本轮已被判罚过

    with pytest.raises(Exception) as excinfo:
        await m._refresh_token_impl()
    assert "risk_round_solve_suppressed" in str(excinfo.value)
    assert solve_calls == [], "抑制窗口内的尝试必须跳过求解"
    assert m.last_token_refresh_status == "risk_round_solve_suppressed"


@pytest.mark.asyncio
async def test_suppressed_attempt_does_not_consume_budget():
    solve_calls = []
    m = _make_challenged_live(solve_calls)
    m._round_solve_suppressed_until = time.time() + 300

    with pytest.raises(Exception):
        await m._refresh_token_impl()
    with _slider_budget_lock:
        assert _COOKIE not in _slider_budget_state, "被抑制的尝试不该扣预算"


@pytest.mark.asyncio
async def test_next_round_can_solve_again():
    """抑制窗口过期后必须放行下一次探测，否则恢复链路被永久掐死。"""
    solve_calls = []
    m = _make_challenged_live(solve_calls)
    m._round_solve_suppressed_until = time.time() - 1

    await m._refresh_token_impl()
    assert len(solve_calls) == 1, "抑制窗口结束后应放行一次求解"


@pytest.mark.asyncio
async def test_round_flow_settle_arm_then_suppress_then_release():
    """整轮状态机串联：结算置位 → 抑制接管不求解 → 窗口过期放行求解。

    前面几条只各测一段（契约测试更是只比字符串位置）。这条连续驱动**同一个
    实例**走完三段，验证状态真的在段之间衔接、且全程只在放行那一次求解。
    """
    solve_calls = []
    m = _make_challenged_live(solve_calls)
    m.last_x5sec_merged_at = time.time() - 10
    m._x5sec_settle_remaining = lambda: 243  # 刚求解成功合并票据，紧接着被重判

    # 第 1 段：结算分支 = 判罚签名 → 置位抑制并 raise（不求解）
    with pytest.raises(Exception) as e1:
        await m._refresh_token_impl()
    assert "x5sec_settling" in str(e1.value)
    assert solve_calls == [], "结算分支不该求解"
    _armed = m._round_solve_suppression_remaining()
    assert _armed > 0, "结算分支必须置位本轮抑制"
    assert m.last_token_refresh_status == "x5sec_settling"

    # 第 2 段：结算期已过 → 抑制门控接管，仍不求解（这一步改前会再烧一次求解）
    m._x5sec_settle_remaining = lambda: 0
    with pytest.raises(Exception) as e2:
        await m._refresh_token_impl()
    assert "risk_round_solve_suppressed" in str(e2.value)
    assert solve_calls == [], "抑制窗口内的尝试必须跳过求解"
    assert m.last_token_refresh_status == "risk_round_solve_suppressed"

    # 第 3 段：窗口过期 → 放行一次求解（恢复能力不受损）
    m._round_solve_suppressed_until = time.time() - 1
    m.last_token_refresh_status = None
    await m._refresh_token_impl()
    assert len(solve_calls) == 1, "抑制窗口结束后应放行且只放行一次求解"


@pytest.mark.asyncio
async def test_token_success_clears_suppression():
    """Token 恢复成功 = 上一轮翻篇，抑制必须解除（否则下一轮白等 600s）。

    ``_finalize_token_success`` 是 aiohttp 与浏览器侧两条成功路径的唯一收尾点，
    抑制解除放在这里才能保证两条路径都不会漏。
    """
    m = _make_live()
    m._round_solve_suppressed_until = time.time() + 400
    m.proxy_config = None

    async def _noop(*_a, **_k):
        return None

    m._clear_qr_login_grace_period = lambda *_a, **_k: None
    m.clear_init_auth_failure_state = lambda *_a, **_k: None
    m._slider_budget_reset = lambda: None
    m._consume_pending_slider_success_notice = lambda: False
    m.send_token_refresh_notification = _noop

    result = await m._finalize_token_success("token-x", token_path="aiohttp")
    assert result == "token-x"
    assert m._round_solve_suppressed_until == 0.0
