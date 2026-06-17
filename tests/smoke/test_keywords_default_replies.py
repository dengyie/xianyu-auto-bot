"""Keyword and default-reply account ownership regressions."""


def _add_cookie(client, headers, cookie_id):
    resp = client.post(
        "/cookies",
        headers=headers,
        json={"id": cookie_id, "value": f"unb={cookie_id}; cookie2=test"},
    )
    assert resp.status_code == 200


def test_owner_can_set_get_and_delete_default_reply(client, user_auth):
    _add_cookie(client, user_auth, "user_reply_cookie")

    update = client.put(
        "/default-replies/user_reply_cookie",
        headers=user_auth,
        json={"enabled": True, "reply_content": "Thanks for reaching out", "reply_once": True},
    )
    fetched = client.get("/default-replies/user_reply_cookie", headers=user_auth)
    delete = client.delete("/default-replies/user_reply_cookie", headers=user_auth)
    after_delete = client.get("/default-replies/user_reply_cookie", headers=user_auth)

    assert update.status_code == 200
    assert fetched.status_code == 200
    assert fetched.json()["enabled"] is True
    assert fetched.json()["reply_content"] == "Thanks for reaching out"
    assert fetched.json()["reply_once"] is True
    assert delete.status_code == 200
    assert after_delete.status_code == 200
    assert after_delete.json() == {"enabled": False, "reply_content": "", "reply_once": False}


def test_default_reply_rejects_foreign_cookie_access(client, auth, user_auth):
    _add_cookie(client, auth, "admin_reply_cookie")

    read = client.get("/default-replies/admin_reply_cookie", headers=user_auth)
    update = client.put(
        "/default-replies/admin_reply_cookie",
        headers=user_auth,
        json={"enabled": True, "reply_content": "stolen", "reply_once": False},
    )
    delete = client.delete("/default-replies/admin_reply_cookie", headers=user_auth)

    assert read.status_code == 403
    assert update.status_code == 403
    assert delete.status_code == 403


def test_owner_can_set_get_and_delete_keywords(client, user_auth):
    _add_cookie(client, user_auth, "user_keywords_cookie")

    update = client.post(
        "/keywords/user_keywords_cookie",
        headers=user_auth,
        json={"keywords": {"hello": "Hi there", "price": "The listed price applies"}},
    )
    fetched = client.get("/keywords/user_keywords_cookie", headers=user_auth)
    delete = client.delete("/keywords/user_keywords_cookie/0", headers=user_auth)
    after_delete = client.get("/keywords/user_keywords_cookie", headers=user_auth)

    assert update.status_code == 200
    assert update.json()["count"] == 2
    assert fetched.status_code == 200
    assert fetched.json() == [
        {"keyword": "hello", "reply": "Hi there", "item_id": None, "type": "normal"},
        {"keyword": "price", "reply": "The listed price applies", "item_id": None, "type": "normal"},
    ]
    assert delete.status_code == 200
    assert len(after_delete.json()) == 1
    assert after_delete.json()[0]["keyword"] == "price"


def test_keywords_reject_foreign_cookie_access(client, auth, user_auth):
    _add_cookie(client, auth, "admin_keywords_cookie")

    read = client.get("/keywords/admin_keywords_cookie", headers=user_auth)
    update = client.post(
        "/keywords/admin_keywords_cookie",
        headers=user_auth,
        json={"keywords": {"hello": "stolen"}},
    )
    delete = client.delete("/keywords/admin_keywords_cookie/0", headers=user_auth)

    assert read.status_code == 403
    assert update.status_code == 403
    assert delete.status_code == 404
