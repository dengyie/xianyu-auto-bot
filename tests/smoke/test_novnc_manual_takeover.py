"""noVNC manual risk takeover: runtime gate + UI/infra contract."""

from pathlib import Path
from types import SimpleNamespace
from unittest import mock

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

    dockerfile = Path('Dockerfile').read_text(encoding='utf-8')
    assert 'novnc' in dockerfile
    assert 'websockify' in dockerfile
    assert 'EXPOSE 6080' in dockerfile

    compose = Path('docker-compose.yml').read_text(encoding='utf-8')
    assert '6080:6080' in compose

    dockerfile_cn = Path('Dockerfile-cn').read_text(encoding='utf-8')
    assert 'novnc' in dockerfile_cn
    assert 'websockify' in dockerfile_cn
    assert 'fluxbox' in dockerfile_cn
    assert 'EXPOSE 6080' in dockerfile_cn

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
    assert '6080/vnc.html' in dashboard_js

    accounts_js = Path('static/js/app-accounts.js').read_text(encoding='utf-8')
    assert 'buildManualInterventionAlert' in accounts_js
    assert 'buildAboutVncAccessPanel' in accounts_js

    css = Path('static/css/accounts.css').read_text(encoding='utf-8')
    assert '.manual-intervention-alert' in css
    assert '.account-diagnostics-vnc-panel' in css
