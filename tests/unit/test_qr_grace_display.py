"""扫码稳定期实时展示（修复"剩余N秒"写死不动、面板像卡死的误读）。

后端 _build_qr_grace_display 从持久化 qr_login_grace_until 现算剩余并重写
token_refresh_error_message；其余带写死倒计时的状态（密码登录退避等）由
_rewrite_frozen_remaining_message 按记录的 last_token_refresh_error_until
统一现算；徽章/连接文案由前端按 qr_grace_* 字段渲染。
"""
from reply_server import _build_qr_grace_display, _rewrite_frozen_remaining_message


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


def test_rewrite_updates_frozen_countdown():
    message = '密码登录失败退避中，剩余1799.0秒'
    rewritten = _rewrite_frozen_remaining_message(message, 1_000_001_800, now=1_000_000_000)
    assert rewritten is not None
    assert '剩余1800秒' in rewritten
    assert '剩余1799.0秒' not in rewritten
    assert '预计' in rewritten


def test_rewrite_expired_reports_recovery():
    rewritten = _rewrite_frozen_remaining_message('密码登录失败退避中，剩余30秒', 1_000_000_000, now=1_000_000_050)
    assert '剩余0秒' in rewritten
    assert '已到期' in rewritten


def test_rewrite_passthrough_without_deadline_or_countdown():
    assert _rewrite_frozen_remaining_message('无倒计时消息', 1_000_000_000, now=1_000_000_000) == '无倒计时消息'
    assert _rewrite_frozen_remaining_message('剩余1799.0秒', 0, now=1_000_000_000) == '剩余1799.0秒'
    assert _rewrite_frozen_remaining_message('剩余1799.0秒', None, now=1_000_000_000) == '剩余1799.0秒'
    assert _rewrite_frozen_remaining_message(None, 1_000_000_000, now=1_000_000_000) is None


def test_runtime_status_builder_reads_grace_deadline_from_db_instance(monkeypatch):
    """回归：稳定期截止时间是急切求值的实参，reply_server 里 db_manager 本身就是
    DBManager 实例；属性链误写成 db_manager.db_manager 会让带实例的运行态构建
    每次都抛 AttributeError（'DBManager' object has no attribute 'db_manager'），
    稳定期实时倒计时永远出不来。"""
    import reply_server
    from types import SimpleNamespace

    called = []
    monkeypatch.setattr(
        reply_server.db_manager,
        'get_cookie_qr_login_grace_until',
        lambda cid: called.append(cid) or 0,
    )

    # 构建器对无实例账号提前返回，探不到稳定期查询；注入带空壳实例的 stub 走全路径
    class _StubManager:
        def __init__(self):
            self.live_instances = {}
            self.cookie_status = {}

        def get_cookie_status(self, cid):
            return self.cookie_status.get(cid, True)

    stub_manager = _StubManager()
    stub_manager.live_instances['grace_regression_cid'] = SimpleNamespace()
    monkeypatch.setattr(reply_server.cookie_manager, 'manager', stub_manager)

    status = reply_server._build_live_runtime_status('grace_regression_cid')

    assert called == ['grace_regression_cid']
    assert status['instance_exists'] is True
    assert not any(k.startswith('qr_grace') for k in status)
