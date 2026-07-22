"""User-side success cookies can close a verification_required QR session."""

from utils.qr_login import QRLoginManager, QRLoginSession


def _session(manager: QRLoginManager, status: str = "verification_required") -> QRLoginSession:
    session_id = "test-session-user-cookies"
    session = QRLoginSession(session_id, user_id=1)
    session.status = status
    manager.sessions[session_id] = session
    return session


def test_normalize_cookie_string_and_json():
    manager = QRLoginManager()
    from_str = manager._normalize_cookie_dict("unb=123; cookie2=abc; empty=; =bad")
    assert from_str == {"unb": "123", "cookie2": "abc"}

    from_json = manager._normalize_cookie_dict('{"unb":"456","sgcookie":"sg1"}')
    assert from_json == {"unb": "456", "sgcookie": "sg1"}


def test_apply_external_cookies_marks_success_from_user():
    manager = QRLoginManager()
    session = _session(manager)

    result = manager.apply_external_cookies(
        session.session_id,
        "unb=999; cookie2=ck2; _tb_token_=tb1",
        source="user",
    )

    assert result["success"] is True
    assert result["status"] == "success"
    assert result["unb"] == "999"
    assert session.status == "success"
    assert session.success_source == "user"
    assert session.cookies["cookie2"] == "ck2"


def test_apply_external_cookies_rejects_incomplete():
    manager = QRLoginManager()
    session = _session(manager)

    result = manager.apply_external_cookies(session.session_id, "unb=only-unb")
    assert result["success"] is False
    assert session.status == "verification_required"


def test_apply_external_cookies_rejects_missing_session():
    manager = QRLoginManager()
    result = manager.apply_external_cookies("no-such", "unb=1; cookie2=2")
    assert result["success"] is False
    assert result["status"] == "not_found"


def test_get_session_status_accepts_user_cookies_flag():
    manager = QRLoginManager()
    session = _session(manager)
    session.screenshot_path = "/static/uploads/qr.png"
    session.verification_ended_elsewhere = True
    session.user_hint = "请粘贴Cookie"

    status = manager.get_session_status(session.session_id)
    assert status["status"] == "verification_required"
    assert status["accept_user_cookies"] is True
    assert status["verification_ended_elsewhere"] is True
    assert "粘贴" in status["message"]
