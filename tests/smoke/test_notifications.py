"""Notification API ownership and template authorization regressions."""

import json


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
