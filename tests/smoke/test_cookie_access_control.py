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


def test_cookie_remark_is_scoped_to_owner(client, auth, user_auth):
    client.post(
        "/cookies",
        headers=auth,
        json={"id": "admin_remark_cookie", "value": "unb=admin"},
    )

    foreign_read = client.get("/cookies/admin_remark_cookie/remark", headers=user_auth)
    foreign_write = client.put(
        "/cookies/admin_remark_cookie/remark",
        headers=user_auth,
        json={"remark": "stolen remark"},
    )
    owner_write = client.put(
        "/cookies/admin_remark_cookie/remark",
        headers=auth,
        json={"remark": "admin remark"},
    )
    owner_read = client.get("/cookies/admin_remark_cookie/remark", headers=auth)

    assert foreign_read.status_code == 403
    assert foreign_write.status_code == 403
    assert owner_write.status_code == 200
    assert owner_write.json()["remark"] == "admin remark"
    assert owner_read.status_code == 200
    assert owner_read.json()["remark"] == "admin remark"


def test_cookie_pause_duration_is_scoped_to_owner(client, auth, user_auth):
    client.post(
        "/cookies",
        headers=auth,
        json={"id": "admin_pause_cookie", "value": "unb=admin"},
    )

    foreign_read = client.get("/cookies/admin_pause_cookie/pause-duration", headers=user_auth)
    foreign_write = client.put(
        "/cookies/admin_pause_cookie/pause-duration",
        headers=user_auth,
        json={"pause_duration": 15},
    )
    owner_write = client.put(
        "/cookies/admin_pause_cookie/pause-duration",
        headers=auth,
        json={"pause_duration": 15},
    )
    owner_read = client.get("/cookies/admin_pause_cookie/pause-duration", headers=auth)

    assert foreign_read.status_code == 403
    assert foreign_write.status_code == 403
    assert owner_write.status_code == 200
    assert owner_write.json()["pause_duration"] == 15
    assert owner_read.status_code == 200
    assert owner_read.json()["pause_duration"] == 15
