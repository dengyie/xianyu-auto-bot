"""init 重试退避下限护栏（2026-09-28 0 秒重试自锤循环回归）。

WS 掉线计数器 connection_failures 从 0 起算：init 内 WS 掉线时错误消息含
"no close frame received or sent" → 旧实现 min(3*0, 15) = 0 秒重试 → 与滑块
周期组合成 15s/圈的自锤循环（实测单晚 21 次 0 秒重试）。三个分支必须全部有
正数下限。
"""
from XianyuAutoAsync import XianyuLive


def _make_live(connection_failures=0):
    m = XianyuLive.__new__(XianyuLive)
    m.cookie_id = "1926782908"
    m.connection_failures = connection_failures
    m.last_token_refresh_status = None
    m._is_account_pause_status = lambda s: False
    m._is_in_qr_login_grace_period = lambda t: False
    return m


def test_ws_blip_error_never_returns_zero_delay():
    m = _make_live(connection_failures=0)
    delay = m._calculate_retry_delay("WebSocket连接异常: no close frame received or sent")
    assert delay >= 5, f"0 秒重试复发: {delay}"


def test_ws_blip_error_scales_then_caps():
    m = _make_live(connection_failures=4)
    assert m._calculate_retry_delay("no close frame received or sent") == 12
    m.connection_failures = 99
    assert m._calculate_retry_delay("no close frame received or sent") == 15


def test_refused_and_unknown_errors_have_floors():
    m = _make_live(connection_failures=0)
    assert m._calculate_retry_delay("Connection refused") >= 10
    assert m._calculate_retry_delay("read timeout") >= 10
    assert m._calculate_retry_delay("什么都不是的未知错误") >= 10


def test_normal_failure_types_untouched():
    """backoff 主路径（密码登录退避等）不受影响。"""
    m = _make_live()
    m.last_token_refresh_status = "password_login_backoff_wait"
    m._compute_token_retry_wait_seconds = lambda t: 656
    assert m._calculate_retry_delay("Token获取失败") == 656
