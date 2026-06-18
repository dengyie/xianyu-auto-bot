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


def test_owner_can_clear_default_reply_records_and_foreign_user_cannot(client, auth, user_auth):
    _add_cookie(client, auth, "admin_reply_cookie")
    _add_cookie(client, user_auth, "user_reply_cookie")

    client.put(
        "/default-replies/admin_reply_cookie",
        headers=auth,
        json={"enabled": True, "reply_content": "Admin reply", "reply_once": True},
    )
    client.put(
        "/default-replies/user_reply_cookie",
        headers=user_auth,
        json={"enabled": True, "reply_content": "User reply", "reply_once": True},
    )

    foreign = client.post("/default-replies/admin_reply_cookie/clear-records", headers=user_auth)
    owner = client.post("/default-replies/admin_reply_cookie/clear-records", headers=auth)
    owner_after = client.get("/default-replies/admin_reply_cookie", headers=auth)

    assert foreign.status_code == 403
    assert owner.status_code == 200
    assert owner_after.status_code == 200
    assert owner_after.json()["reply_once"] is True


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


def test_keywords_with_item_id_rejects_foreign_cookie_access(client, auth, user_auth):
    _add_cookie(client, auth, "admin_keywords_item_cookie")
    _add_cookie(client, user_auth, "user_keywords_item_cookie")

    owner_update = client.post(
        "/keywords-with-item-id/admin_keywords_item_cookie",
        headers=auth,
        json={
            "keywords": [
                {"keyword": "hello", "reply": "Hi there", "item_id": "item-1"},
            ]
        },
    )

    foreign_read = client.get("/keywords-with-item-id/admin_keywords_item_cookie", headers=user_auth)
    foreign_update = client.post(
        "/keywords-with-item-id/admin_keywords_item_cookie",
        headers=user_auth,
        json={
            "keywords": [
                {"keyword": "hello", "reply": "stolen", "item_id": "item-2"},
            ]
        },
    )
    owner_read = client.get("/keywords-with-item-id/admin_keywords_item_cookie", headers=auth)

    assert owner_update.status_code == 200
    assert owner_update.json()["count"] == 1
    assert foreign_read.status_code == 403
    assert foreign_update.status_code == 403
    assert owner_read.status_code == 200
    assert owner_read.json()[0]["keyword"] == "hello"
    assert owner_read.json()[0]["reply"] == "Hi there"
    assert owner_read.json()[0]["item_id"] == "item-1"
    assert owner_read.json()[0]["type"] == "text"


def test_keywords_with_type_rejects_foreign_cookie_access(client, auth, user_auth):
    _add_cookie(client, auth, "admin_keywords_type_cookie")

    owner_update = client.post(
        "/keywords-with-item-id/admin_keywords_type_cookie",
        headers=auth,
        json={
            "keywords": [
                {"keyword": "typed", "reply": "Typed reply", "item_id": ""},
            ]
        },
    )
    foreign_read = client.get("/keywords-with-type/admin_keywords_type_cookie", headers=user_auth)
    owner_read = client.get("/keywords-with-type/admin_keywords_type_cookie", headers=auth)

    assert owner_update.status_code == 200
    assert foreign_read.status_code == 404
    assert owner_read.status_code == 200
    assert owner_read.json()[0]["keyword"] == "typed"
    assert owner_read.json()[0]["reply"] == "Typed reply"
    assert owner_read.json()[0]["type"] == "text"


def test_chat_keyword_item_routes_are_scoped_to_cookie_owner(client, auth, user_auth):
    _add_cookie(client, auth, "admin_chat_keyword_cookie")
    _add_cookie(client, user_auth, "user_chat_keyword_cookie")

    from db_manager import db_manager

    assert db_manager.save_item_info(
        "admin_chat_keyword_cookie",
        "chat_item_001",
        {
            "title": "Chat source item",
            "description": "Owner source",
            "category": "digital",
            "price": "49.90",
        },
    )
    assert db_manager.save_item_info(
        "admin_chat_keyword_cookie",
        "chat_item_002",
        {
            "title": "Chat target item",
            "description": "Owner target",
            "category": "digital",
            "price": "59.90",
        },
    )
    assert db_manager.save_keywords_for_item(
        "admin_chat_keyword_cookie",
        "chat_item_001",
        [{"keyword": "hello", "reply": "Hi from owner", "type": "text"}],
    )
    assert db_manager.update_item_reply(
        "admin_chat_keyword_cookie",
        "chat_item_001",
        "Owner item reply",
    )

    foreign_read = client.get(
        "/api/chat/keywords/admin_chat_keyword_cookie/item/chat_item_001",
        headers=user_auth,
    )
    foreign_save = client.post(
        "/api/chat/keywords/admin_chat_keyword_cookie/item/chat_item_001",
        headers=user_auth,
        json={"keywords": [{"keyword": "stolen", "reply": "nope"}], "item_reply": "stolen"},
    )
    foreign_copy = client.post(
        "/api/chat/keywords/admin_chat_keyword_cookie/copy",
        headers=user_auth,
        json={"source_item_id": "chat_item_001", "target_item_ids": ["chat_item_002"]},
    )
    foreign_items = client.get("/api/chat/items/admin_chat_keyword_cookie", headers=user_auth)
    owner_items = client.get("/api/chat/items/admin_chat_keyword_cookie", headers=auth)
    owner_read = client.get(
        "/api/chat/keywords/admin_chat_keyword_cookie/item/chat_item_001",
        headers=auth,
    )
    owner_save = client.post(
        "/api/chat/keywords/admin_chat_keyword_cookie/item/chat_item_001",
        headers=auth,
        json={
            "keywords": [{"keyword": "updated", "reply": "Updated reply", "type": "text"}],
            "item_reply": "Updated item reply",
        },
    )
    owner_copy = client.post(
        "/api/chat/keywords/admin_chat_keyword_cookie/copy",
        headers=auth,
        json={"source_item_id": "chat_item_001", "target_item_ids": ["chat_item_002"]},
    )
    target_read = client.get(
        "/api/chat/keywords/admin_chat_keyword_cookie/item/chat_item_002",
        headers=auth,
    )

    assert foreign_read.status_code == 403
    assert foreign_save.status_code == 403
    assert foreign_copy.status_code == 403
    assert foreign_items.status_code == 403
    assert owner_items.status_code == 200
    assert owner_items.json()["items"] == [
        {"item_id": "chat_item_001", "item_title": "Chat source item"},
        {"item_id": "chat_item_002", "item_title": "Chat target item"},
    ]
    assert owner_read.status_code == 200
    assert owner_read.json()["item_reply"] == "Owner item reply"
    assert owner_read.json()["keywords"][0]["keyword"] == "hello"
    assert owner_save.status_code == 200
    assert owner_save.json() == {"success": True, "count": 1}
    assert owner_copy.status_code == 200
    assert owner_copy.json()["results"] == {"chat_item_002": 1}
    assert target_read.status_code == 200
    assert target_read.json()["item_reply"] == "Updated item reply"
    assert target_read.json()["keywords"][0]["keyword"] == "updated"
