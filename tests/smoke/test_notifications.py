"""Notification API ownership and template authorization regressions."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer


def _add_cookie(client, headers, cookie_id):
    resp = client.post(
        "/cookies",
        headers=headers,
        json={"id": cookie_id, "value": f"unb={cookie_id}; cookie2=test"},
    )
    assert resp.status_code == 200


def _create_channel(client, headers, name="webhook channel"):
    resp = client.post(
        "/notification-channels",
        headers=headers,
        json={
            "name": name,
            "type": "webhook",
            "config": json.dumps({"webhook_url": "https://example.invalid/hook"}),
        },
    )
    assert resp.status_code == 200
    return resp.json()["id"]


class _WebhookRecorder:
    def __init__(self):
        self.requests = []
        self._event = threading.Event()

    def record(self, body):
        self.requests.append(body)
        self._event.set()

    def wait(self, timeout=2.0):
        return self._event.wait(timeout)


def _run_webhook_server(recorder):
    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("content-length", "0") or "0")
            body = self.rfile.read(length).decode("utf-8")
            recorder.record(body)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, format, *args):
            return

    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def test_notification_channels_are_scoped_to_current_user(client, auth, user_auth):
    _create_channel(client, auth, name="admin channel")

    resp = client.get("/notification-channels", headers=user_auth)

    assert resp.status_code == 200
    assert resp.json() == []


def test_regular_user_cannot_update_or_delete_another_users_channel(client, auth, user_auth):
    channel_id = _create_channel(client, auth, name="admin channel")

    update = client.put(
        f"/notification-channels/{channel_id}",
        headers=user_auth,
        json={
            "name": "stolen",
            "config": json.dumps({"webhook_url": "https://example.invalid/other"}),
            "enabled": False,
        },
    )
    delete = client.delete(f"/notification-channels/{channel_id}", headers=user_auth)

    assert update.status_code == 404
    assert delete.status_code == 404


def test_regular_user_cannot_read_another_users_channel(client, auth, user_auth):
    channel_id = _create_channel(client, auth, name="admin channel")

    read = client.get(f"/notification-channels/{channel_id}", headers=user_auth)

    assert read.status_code == 404


def test_message_notifications_read_is_scoped_to_cookie_owner(client, auth, user_auth):
    _add_cookie(client, auth, "admin_notify_cookie")
    _add_cookie(client, user_auth, "user_notify_cookie")
    admin_channel_id = _create_channel(client, auth, name="admin channel")
    user_channel_id = _create_channel(client, user_auth, name="user channel")

    client.post(
        "/message-notifications/admin_notify_cookie",
        headers=auth,
        json={"channel_id": admin_channel_id, "enabled": True},
    )
    client.post(
        "/message-notifications/user_notify_cookie",
        headers=user_auth,
        json={"channel_id": user_channel_id, "enabled": True},
    )

    foreign = client.get("/message-notifications/admin_notify_cookie", headers=user_auth)
    owner = client.get("/message-notifications/admin_notify_cookie", headers=auth)

    assert foreign.status_code == 403
    assert owner.status_code == 200
    assert owner.json()


def test_message_notification_requires_owned_cookie_and_owned_channel(client, auth, user_auth):
    _add_cookie(client, auth, "admin_notify_cookie")
    _add_cookie(client, user_auth, "user_notify_cookie")
    admin_channel_id = _create_channel(client, auth, name="admin channel")

    foreign_cookie = client.post(
        "/message-notifications/admin_notify_cookie",
        headers=user_auth,
        json={"channel_id": admin_channel_id, "enabled": True},
    )
    foreign_channel = client.post(
        "/message-notifications/user_notify_cookie",
        headers=user_auth,
        json={"channel_id": admin_channel_id, "enabled": True},
    )

    assert foreign_cookie.status_code == 403
    assert foreign_channel.status_code == 404


def test_message_notifications_list_filters_foreign_cookie_entries(client, auth, user_auth):
    _add_cookie(client, auth, "admin_notify_cookie")
    _add_cookie(client, user_auth, "user_notify_cookie")
    user_channel_id = _create_channel(client, user_auth, name="user channel")
    admin_channel_id = _create_channel(client, auth, name="admin channel")

    client.post(
        "/message-notifications/user_notify_cookie",
        headers=user_auth,
        json={"channel_id": user_channel_id, "enabled": True},
    )
    client.post(
        "/message-notifications/admin_notify_cookie",
        headers=auth,
        json={"channel_id": admin_channel_id, "enabled": True},
    )

    resp = client.get("/message-notifications", headers=user_auth)

    assert resp.status_code == 200
    data = resp.json()
    assert "user_notify_cookie" in data
    assert "admin_notify_cookie" not in data


def test_message_notifications_account_delete_is_scoped_to_cookie_owner(client, auth, user_auth):
    _add_cookie(client, auth, "admin_notify_cookie")
    _add_cookie(client, user_auth, "user_notify_cookie")
    admin_channel_id = _create_channel(client, auth, name="admin channel")
    user_channel_id = _create_channel(client, user_auth, name="user channel")

    client.post(
        "/message-notifications/admin_notify_cookie",
        headers=auth,
        json={"channel_id": admin_channel_id, "enabled": True},
    )
    client.post(
        "/message-notifications/user_notify_cookie",
        headers=user_auth,
        json={"channel_id": user_channel_id, "enabled": True},
    )

    foreign = client.delete("/message-notifications/account/admin_notify_cookie", headers=user_auth)
    owner = client.delete("/message-notifications/account/admin_notify_cookie", headers=auth)
    owner_after = client.get("/message-notifications/admin_notify_cookie", headers=auth)

    assert foreign.status_code == 403
    assert owner.status_code == 200
    assert owner_after.status_code == 200
    assert owner_after.json() == []


def test_message_notification_delete_is_scoped_to_owner_channel(client, auth, user_auth):
    _add_cookie(client, auth, "admin_notify_cookie")
    _add_cookie(client, user_auth, "user_notify_cookie")
    admin_channel_id = _create_channel(client, auth, name="admin channel")
    user_channel_id = _create_channel(client, user_auth, name="user channel")

    client.post(
        "/message-notifications/admin_notify_cookie",
        headers=auth,
        json={"channel_id": admin_channel_id, "enabled": True},
    )
    client.post(
        "/message-notifications/user_notify_cookie",
        headers=user_auth,
        json={"channel_id": user_channel_id, "enabled": True},
    )

    owner_before = client.get("/message-notifications/admin_notify_cookie", headers=auth)
    notification_id = owner_before.json()[0]["id"]

    foreign = client.delete(f"/message-notifications/{notification_id}", headers=user_auth)
    owner = client.delete(f"/message-notifications/{notification_id}", headers=auth)
    owner_after = client.get("/message-notifications/admin_notify_cookie", headers=auth)

    assert foreign.status_code == 404
    assert owner.status_code == 200
    assert owner_after.status_code == 200
    assert owner_after.json() == []


def test_notification_template_test_send_uses_only_current_users_channels(client, auth, user_auth):
    _create_channel(client, auth, name="admin channel")

    resp = client.post(
        "/notification-templates/test",
        headers=user_auth,
        json={"template_type": "message", "template": "test message: {message}"},
    )

    assert resp.status_code == 400
    assert "没有已启用的通知渠道" in resp.json()["detail"]


def test_notification_template_test_send_succeeds_with_current_users_enabled_channel(client, user_auth, monkeypatch):
    recorder = _WebhookRecorder()
    server, _thread = _run_webhook_server(recorder)
    webhook_url = f"http://127.0.0.1:{server.server_port}/webhook"
    try:
        channel_id = _create_channel(
            client,
            user_auth,
            name="user webhook channel",
        )
        client.put(
            f"/notification-channels/{channel_id}",
            headers=user_auth,
            json={
                "name": "user webhook channel",
                "config": json.dumps({"webhook_url": webhook_url}),
                "enabled": True,
            },
        )

        resp = client.post(
            "/notification-templates/test",
            headers=user_auth,
            json={"template_type": "message", "template": "test message: {message}"},
        )

        assert resp.status_code == 200
        assert resp.json()["success"] is True
        assert recorder.wait()
        assert recorder.requests
    finally:
        server.shutdown()
        server.server_close()


def test_notification_template_mutation_is_admin_only(client, auth, user_auth):
    regular_update = client.put(
        "/notification-templates/message",
        headers=user_auth,
        json={"template": "regular user should not change global template"},
    )
    admin_update = client.put(
        "/notification-templates/message",
        headers=auth,
        json={"template": "Admin template: {message}"},
    )

    assert regular_update.status_code == 403
    assert admin_update.status_code == 200


def test_notification_template_reset_is_admin_only(client, auth, user_auth):
    regular_reset = client.post("/notification-templates/message/reset", headers=user_auth)
    admin_reset = client.post("/notification-templates/message/reset", headers=auth)

    assert regular_reset.status_code == 403
    assert admin_reset.status_code == 200
