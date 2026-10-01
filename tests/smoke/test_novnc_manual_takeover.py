"""noVNC manual risk takeover: runtime gate + UI/infra contract."""

from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from starlette.websockets import WebSocketDisconnect

import reply_server


def _make_fake_live(**overrides):
    now = 1_700_000_000
    base = {
        'connection_state': SimpleNamespace(value='connected'),
        'ws': SimpleNamespace(closed=False),
        'session': SimpleNamespace(closed=False),
        'current_token': 'token',
        'last_token_refresh_status': 'success',
        'last_token_refresh_error_message': None,
        'last_session_keepalive_status': 'success',
        'last_session_keepalive_error_message': None,
        'last_heartbeat_response': now,
        'last_heartbeat_time': now,
        'last_token_refresh_time': now,
        'last_session_keepalive_time': now,
        'last_non_heartbeat_message_time': now,
        'last_sync_package_time': now,
        'last_user_chat_time': now,
        'last_stream_watchdog_reconnect_time': 0,
        'last_message_received_time': now,
        'last_successful_connection': now,
        'last_state_change_time': now,
        'heartbeat_interval': 15,
        'heartbeat_timeout': 30,
        'token_refresh_interval': 72000,
        'token_retry_interval': 180,
        'session_keepalive_interval': 600,
        'session_keepalive_retry_interval': 180,
        'stream_watchdog_grace_period': 60,
        'message_stream_watchdog_timeout': 1800,
        'cookie_refresh_enabled': True,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_runtime_status_defaults_include_vnc_fields():
    status = reply_server._build_live_runtime_status('')
    assert status['vnc_manual_action_available'] is False
    assert status['manual_browser_session_status'] is None
    assert status['manual_browser_reason'] is None


def test_runtime_status_marks_vnc_available_for_active_browser_session(_db):
    cookie_id = 'vnc-cookie-1'
    session_id = 'sess-vnc-1'
    reply_server.password_login_sessions[session_id] = {
        'account_id': cookie_id,
        'show_browser': True,
        'status': 'processing',
        'refresh_mode': True,
    }

    fake_live = _make_fake_live()
    fake_manager = mock.Mock(live_instances={cookie_id: fake_live})

    try:
        with mock.patch.object(reply_server.cookie_manager, 'manager', fake_manager), \
             mock.patch('XianyuAutoAsync.XianyuLive.get_instance', return_value=fake_live), \
             mock.patch('XianyuAutoAsync.XianyuLive.get_auth_recovery_lock_state', return_value={}), \
             mock.patch('XianyuAutoAsync.XianyuLive.is_manual_refresh_active', return_value=False):
            status = reply_server._build_live_runtime_status(cookie_id)
    finally:
        reply_server.password_login_sessions.pop(session_id, None)

    assert status['vnc_manual_action_available'] is True
    assert status['manual_browser_session_status'] == 'processing'
    assert status['manual_browser_reason'] == 'active_password_refresh'


def test_runtime_status_marks_vnc_available_for_manual_token_status(_db):
    cookie_id = 'vnc-cookie-2'
    fake_live = _make_fake_live(
        current_token=None,
        last_token_refresh_status='verification_pending_manual',
        last_token_refresh_error_message='need manual',
        last_session_keepalive_status=None,
    )
    fake_manager = mock.Mock(live_instances={cookie_id: fake_live})

    with mock.patch.object(reply_server.cookie_manager, 'manager', fake_manager), \
         mock.patch('XianyuAutoAsync.XianyuLive.get_instance', return_value=fake_live), \
         mock.patch('XianyuAutoAsync.XianyuLive.get_auth_recovery_lock_state', return_value={}), \
         mock.patch('XianyuAutoAsync.XianyuLive.is_manual_refresh_active', return_value=True):
        status = reply_server._build_live_runtime_status(cookie_id)

    assert status['vnc_manual_action_available'] is True
    assert status['manual_browser_session_status'] is None
    assert status['token_refresh_status'] == 'verification_pending_manual'


def test_novnc_source_contract():
    entry = Path('entrypoint.sh').read_text(encoding='utf-8')
    assert 'websockify --web=/usr/share/novnc 6080 localhost:5900' in entry
    assert 'fluxbox' in entry
    # VNC 服务端必须用 TigerVNC x0vncserver（x11vnc 0.9.16 + libvncserver 0.9.14
    # 在 Debian 12 上 accept 挂起，连接后不发送 RFB 握手，导致 noVNC 黑屏）
    assert 'x0vncserver' in entry
    # 5900/6080 均不发布到宿主机，唯一公网入口是 /websockify（应用层会话鉴权），
    # 故 VNC 服务端用 -SecurityTypes None，不再依赖 VncAuth/vncpasswd 密码文件。
    assert '-SecurityTypes None' in entry
    assert '-SecurityTypes VncAuth' not in entry
    assert 'vncpasswd' not in entry
    # 确保没有残留 x11vnc 的启动命令（注释里说明迁移原因的 "x11vnc" 除外）
    assert 'x11vnc -display' not in entry

    dockerfile = Path('Dockerfile').read_text(encoding='utf-8')
    assert 'novnc' in dockerfile
    assert 'websockify' in dockerfile
    assert 'EXPOSE 6080' in dockerfile
    assert 'tigervnc-scraping-server' in dockerfile
    assert 'x11vnc' not in dockerfile

    compose = Path('docker-compose.yml').read_text(encoding='utf-8')
    assert '6080:6080' in compose

    dockerfile_cn = Path('Dockerfile-cn').read_text(encoding='utf-8')
    assert 'novnc' in dockerfile_cn
    assert 'websockify' in dockerfile_cn
    assert 'fluxbox' in dockerfile_cn
    assert 'EXPOSE 6080' in dockerfile_cn
    assert 'tigervnc-scraping-server' in dockerfile_cn
    assert 'x11vnc' not in dockerfile_cn

    compose_cn = Path('docker-compose-cn.yml').read_text(encoding='utf-8')
    assert '6080:6080' in compose_cn

    runtime = Path('reply_server.py').read_text(encoding='utf-8')
    assert 'vnc_manual_action_available' in runtime
    assert 'manual_browser_session_status' in runtime
    assert 'manual_browser_reason' in runtime

    dashboard_js = Path('static/js/app-dashboard.js').read_text(encoding='utf-8')
    assert 'function getNoVncUrl' in dashboard_js
    assert 'function isVncManualActionAvailable' in dashboard_js
    assert 'function buildManualInterventionAlert' in dashboard_js
    assert 'function buildAboutVncAccessPanel' in dashboard_js
    assert 'vnc.html?autoconnect=1&resize=scale' in dashboard_js
    assert 'window.location.origin' in dashboard_js

    accounts_js = Path('static/js/app-accounts.js').read_text(encoding='utf-8')
    assert 'buildManualInterventionAlert' in accounts_js
    assert 'buildAboutVncAccessPanel' in accounts_js

    css = Path('static/css/accounts.css').read_text(encoding='utf-8')
    assert '.manual-intervention-alert' in css
    assert '.account-diagnostics-vnc-panel' in css


def test_novnc_public_proxy_contract():
    """reply_server 在公网下能承接 /vnc.html、noVNC 静态资源与 /websockify 代理。"""
    runtime = Path('reply_server.py').read_text(encoding='utf-8')
    assert '_NOVNC_WEB_ROOT' in runtime
    assert '_NOVNC_BACKEND_WS' in runtime
    assert 'vnc.html' in runtime
    assert 'vnc_lite.html' in runtime
    assert 'vnc_auto.html' in runtime
    assert "'/websockify'" in runtime
    assert '_proxy_websocket_bidirectional' in runtime
    # /websockify 是唯一公网入口，必须做会话鉴权（auth_token Cookie），
    # 否则 -SecurityTypes None 的 VNC 流会对公网裸奔。
    assert '_websockify_session_authed' in runtime
    assert "cookies.get('auth_token')" in runtime
    assert 'session_service.verify' in runtime
    assert 'max_size=None' in runtime
    assert 'NOVNC_WEB_ROOT' in runtime
    assert 'NOVNC_BACKEND_WS' in runtime


def _ws_handshake_outcome(client, headers=None) -> str:
    """Attempt a /websockify handshake and report 'accepted' vs 'rejected'."""
    try:
        with client.websocket_connect('/websockify', headers=headers or {}) as ws:
            return 'accepted'
    except WebSocketDisconnect:
        return 'rejected'


def test_websockify_rejects_without_session(client):
    """未登录（无 auth_token Cookie）必须拒绝，不能把 VNC 流暴露给公网。"""
    assert _ws_handshake_outcome(client) == 'rejected'


def test_websockify_rejects_cross_origin(client, admin_token):
    """即使带有效会话，跨站 Origin 也必须拒绝（CSWSH 防御）。"""
    assert _ws_handshake_outcome(client, headers={
        'cookie': f'auth_token={admin_token}',
        'origin': 'http://evil.example',
    }) == 'rejected'


def test_websockify_accepts_authenticated_same_origin(client, admin_token):
    """有效会话 + 同源 Origin 应放行并进入 noVNC 反向代理。"""
    calls = []

    async def _fake_proxy(client_ws, backend_url):
        await client_ws.accept()
        calls.append(backend_url)

    with mock.patch.object(reply_server, '_proxy_websocket_bidirectional', _fake_proxy):
        assert _ws_handshake_outcome(client, headers={
            'cookie': f'auth_token={admin_token}',
            'origin': 'http://testserver',
        }) == 'accepted'

    assert calls == [reply_server._NOVNC_BACKEND_WS]
