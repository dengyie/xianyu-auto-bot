"""Cross-user cookie access control regressions."""


def test_regular_user_cannot_list_another_users_cookie(client, auth, user_auth):
    client.post(
        "/cookies",
        headers=auth,
        json={"id": "admin_owned_cookie", "value": "unb=admin; cookie2=secret"},
    )

    resp = client.get("/cookies", headers=user_auth)

    assert resp.status_code == 200
    assert "admin_owned_cookie" not in resp.json()


def test_regular_user_cannot_update_another_users_cookie(client, auth, user_auth):
    client.post(
        "/cookies",
        headers=auth,
        json={"id": "admin_cookie_to_update", "value": "unb=admin; cookie2=secret"},
    )

    resp = client.put(
        "/cookies/admin_cookie_to_update",
        headers=user_auth,
        json={"id": "admin_cookie_to_update", "value": "unb=stolen; cookie2=changed"},
    )

    assert resp.status_code == 403


def test_regular_user_cannot_toggle_another_users_cookie_status(client, auth, user_auth):
    client.post(
        "/cookies",
        headers=auth,
        json={"id": "admin_cookie_status", "value": "unb=admin"},
    )

    resp = client.put(
        "/cookies/admin_cookie_status/status",
        headers=user_auth,
        json={"enabled": False, "pause_duration": 10},
    )

    assert resp.status_code == 403


def test_regular_user_cannot_read_another_users_proxy_secret(client, auth, user_auth):
    client.post(
        "/cookies",
        headers=auth,
        json={"id": "admin_proxy_cookie", "value": "unb=admin"},
    )

    resp = client.get(
        "/cookie/admin_proxy_cookie/proxy?include_secret=true",
        headers=user_auth,
    )

    assert resp.status_code == 403


def test_duplicate_cookie_id_owned_by_other_user_is_rejected(client, auth, user_auth):
    client.post(
        "/cookies",
        headers=auth,
        json={"id": "shared_cookie_id", "value": "unb=admin"},
    )

    resp = client.post(
        "/cookies",
        headers=user_auth,
        json={"id": "shared_cookie_id", "value": "unb=user"},
    )

    assert resp.status_code == 400
