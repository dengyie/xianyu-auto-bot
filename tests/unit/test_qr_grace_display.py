"""扫码稳定期实时展示（修复"剩余N秒"写死不动、面板像卡死的误读）。

后端 _build_qr_grace_display 从持久化 qr_login_grace_until 现算剩余并重写
token_refresh_error_message；徽章/连接文案由前端按 qr_grace_* 字段渲染。
"""
from reply_server import _build_qr_grace_display


def test_non_grace_status_returns_none():
    assert _build_qr_grace_display('success', 9999999999) is None
    assert _build_qr_grace_display('token_expired_recovery_failed', 9999999999) is None
    assert _build_qr_grace_display(None, 9999999999) is None


def test_missing_deadline_keeps_static_message():
    # 无截止时间（DB 无记录/异常）时不动静态文案，交由原路径展示
    assert _build_qr_grace_display('qr_login_grace_wait', 0) is None
    assert _build_qr_grace_display('qr_login_grace_wait', None) is None


def test_active_grace_computes_live_remaining():
    grace_until = 1_000_000_500
    now = 1_000_000_000
    display = _build_qr_grace_display('qr_login_grace_wait', grace_until, now=now)
    assert display is not None
    assert display['qr_grace_remaining_seconds'] == 500
    assert display['qr_grace_until'] == grace_until
    assert '剩余500秒' in display['token_refresh_error_message']
    assert '自动恢复' in display['token_refresh_error_message']
    # 预计恢复时刻来自截止时间而非 now
    assert display['qr_grace_until_display'] in display['token_refresh_error_message']


def test_expired_grace_reports_recovery_phase():
    grace_until = 1_000_000_000
    now = 1_000_000_020
    display = _build_qr_grace_display('qr_login_grace_wait', grace_until, now=now)
    assert display is not None
    assert display['qr_grace_remaining_seconds'] == 0
    assert '稳定期已结束' in display['token_refresh_error_message']


def test_invalid_deadline_treated_as_missing():
    assert _build_qr_grace_display('qr_login_grace_wait', 'not-a-number') is None
