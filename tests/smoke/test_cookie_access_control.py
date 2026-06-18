"""Cross-user cookie access control regressions."""


def test_item_info_routes_are_scoped_to_cookie_owner(client, auth, user_auth):
    client.post(
        "/cookies",
        headers=auth,
        json={"id": "admin_item_cookie", "value": "unb=admin"},
    )
    from db_manager import db_manager

    assert db_manager.save_item_info(
        "admin_item_cookie",
        "admin_item_001",
        {
            "title": "Owner item",
            "description": "Owner only",
            "category": "digital",
            "price": "9.90",
        },
    )

    foreign_list = client.get("/items/cookie/admin_item_cookie", headers=user_auth)
    foreign_read = client.get("/items/admin_item_cookie/admin_item_001", headers=user_auth)
    foreign_update = client.put(
        "/items/admin_item_cookie/admin_item_001",
        headers=user_auth,
        json={"item_detail": "{\"title\":\"stolen\"}"},
    )
    foreign_delete = client.delete(
        "/items/admin_item_cookie/admin_item_001",
        headers=user_auth,
    )
    owner_list = client.get("/items/cookie/admin_item_cookie", headers=auth)
    owner_read = client.get("/items/admin_item_cookie/admin_item_001", headers=auth)
    owner_update = client.put(
        "/items/admin_item_cookie/admin_item_001",
        headers=auth,
        json={"item_detail": "{\"title\":\"updated\"}"},
    )
    owner_delete = client.delete("/items/admin_item_cookie/admin_item_001", headers=auth)

    assert foreign_list.status_code == 403
    assert foreign_read.status_code == 403
    assert foreign_update.status_code == 403
    assert foreign_delete.status_code == 403
    assert owner_list.status_code == 200
    assert [item["item_id"] for item in owner_list.json()["items"]] == ["admin_item_001"]
    assert owner_read.status_code == 200
    assert owner_read.json()["item"]["item_title"] == "Owner item"
    assert owner_update.status_code == 200
    assert owner_delete.status_code == 200


def test_item_reply_routes_are_scoped_to_cookie_owner(client, auth, user_auth):
    client.post(
        "/cookies",
        headers=auth,
        json={"id": "admin_reply_cookie", "value": "unb=admin"},
    )
    client.post(
        "/cookies",
        headers=user_auth,
        json={"id": "user_reply_cookie", "value": "unb=user"},
    )
    from db_manager import db_manager

    shared_item_id = "shared_reply_item"
    assert db_manager.save_item_info(
        "admin_reply_cookie",
        shared_item_id,
        {
            "title": "Owner reply item",
            "description": "Owner metadata",
            "category": "digital",
            "price": "19.90",
        },
    )
    assert db_manager.save_item_info(
        "user_reply_cookie",
        shared_item_id,
        {
            "title": "Foreign reply item",
            "description": "Foreign metadata",
            "category": "digital",
            "price": "29.90",
        },
    )
    assert db_manager.update_item_reply(
        "admin_reply_cookie",
        shared_item_id,
        "owner reply",
    )

    foreign_list = client.get("/itemReplays/cookie/admin_reply_cookie", headers=user_auth)
    foreign_read = client.get(
        f"/item-reply/admin_reply_cookie/{shared_item_id}",
        headers=user_auth,
    )
    foreign_update = client.put(
        f"/item-reply/admin_reply_cookie/{shared_item_id}",
        headers=user_auth,
        json={"reply_content": "stolen reply"},
    )
    foreign_delete = client.delete(
        f"/item-reply/admin_reply_cookie/{shared_item_id}",
        headers=user_auth,
    )
    foreign_batch_delete = client.request(
        "DELETE",
        "/item-reply/batch",
        headers=user_auth,
        json={"items": [{"cookie_id": "admin_reply_cookie", "item_id": shared_item_id}]},
    )
    owner_list = client.get("/itemReplays/cookie/admin_reply_cookie", headers=auth)
    owner_read = client.get(
        f"/item-reply/admin_reply_cookie/{shared_item_id}",
        headers=auth,
    )
    owner_update = client.put(
        f"/item-reply/admin_reply_cookie/{shared_item_id}",
        headers=auth,
        json={"reply_content": "updated owner reply"},
    )
    owner_delete = client.delete(
        f"/item-reply/admin_reply_cookie/{shared_item_id}",
        headers=auth,
    )

    assert foreign_list.status_code == 403
    assert foreign_read.status_code == 403
    assert foreign_update.status_code == 403
    assert foreign_delete.status_code == 403
    assert foreign_batch_delete.status_code == 403
    assert owner_list.status_code == 200
    assert owner_list.json()["items"] == [
        {
            **owner_list.json()["items"][0],
            "item_id": shared_item_id,
            "cookie_id": "admin_reply_cookie",
            "reply_content": "owner reply",
            "item_title": "Owner reply item",
        }
    ]
    assert owner_read.status_code == 200
    assert owner_read.json()["reply_content"] == "owner reply"
    assert owner_update.status_code == 200
    assert owner_delete.status_code == 200


def test_item_flag_routes_are_scoped_to_cookie_owner(client, auth, user_auth):
    client.post(
        "/cookies",
        headers=auth,
        json={"id": "admin_item_flag_cookie", "value": "unb=admin"},
    )
    from db_manager import db_manager

    assert db_manager.save_item_info(
        "admin_item_flag_cookie",
        "flag_item_001",
        {
            "title": "Flag item",
            "description": "Owner only",
            "category": "digital",
            "price": "39.90",
        },
    )

    foreign_multi_spec = client.put(
        "/items/admin_item_flag_cookie/flag_item_001/multi-spec",
        headers=user_auth,
        json={"is_multi_spec": True},
    )
    foreign_multi_quantity = client.put(
        "/items/admin_item_flag_cookie/flag_item_001/multi-quantity-delivery",
        headers=user_auth,
        json={"multi_quantity_delivery": True},
    )
    owner_multi_spec = client.put(
        "/items/admin_item_flag_cookie/flag_item_001/multi-spec",
        headers=auth,
        json={"is_multi_spec": True},
    )
    owner_multi_quantity = client.put(
        "/items/admin_item_flag_cookie/flag_item_001/multi-quantity-delivery",
        headers=auth,
        json={"multi_quantity_delivery": True},
    )

    assert foreign_multi_spec.status_code == 403
    assert foreign_multi_quantity.status_code == 403
    assert owner_multi_spec.status_code == 200
    assert owner_multi_quantity.status_code == 200
    assert db_manager.get_item_multi_spec_status("admin_item_flag_cookie", "flag_item_001") is True
    assert (
        db_manager.get_item_multi_quantity_delivery_status(
            "admin_item_flag_cookie",
            "flag_item_001",
        )
        is True
    )


def test_ai_reply_settings_and_test_are_scoped_to_cookie_owner(
    client,
    auth,
    user_auth,
    monkeypatch,
):
    client.post(
        "/cookies",
        headers=auth,
        json={"id": "admin_ai_reply_cookie", "value": "unb=admin"},
    )
    client.post(
        "/cookies",
        headers=user_auth,
        json={"id": "user_ai_reply_cookie", "value": "unb=user"},
    )
    from db_manager import db_manager
    import reply_server

    assert db_manager.save_ai_reply_settings(
        "admin_ai_reply_cookie",
        {
            "ai_enabled": True,
            "model_name": "owner-model",
            "api_key": "owner-secret",
            "base_url": "https://owner.example/v1",
            "api_type": "openai",
            "max_discount_percent": 12,
            "max_discount_amount": 34,
            "max_bargain_rounds": 2,
            "custom_prompts": "owner prompt",
        },
    )
    assert db_manager.save_ai_reply_settings(
        "user_ai_reply_cookie",
        {
            "ai_enabled": True,
            "model_name": "user-model",
            "api_key": "user-secret",
            "base_url": "https://user.example/v1",
            "api_type": "openai",
            "max_discount_percent": 8,
            "max_discount_amount": 21,
            "max_bargain_rounds": 1,
            "custom_prompts": "user prompt",
        },
    )
    monkeypatch.setattr(
        reply_server.ai_reply_engine,
        "is_ai_enabled",
        lambda cid: cid in {"admin_ai_reply_cookie", "user_ai_reply_cookie"},
    )
    monkeypatch.setattr(
        reply_server.ai_reply_engine,
        "generate_reply",
        lambda **kwargs: f"reply for {kwargs['cookie_id']}",
    )

    foreign_read = client.get("/ai-reply-settings/admin_ai_reply_cookie", headers=user_auth)
    foreign_update = client.put(
        "/ai-reply-settings/admin_ai_reply_cookie",
        headers=user_auth,
        json={"ai_enabled": False, "model_name": "stolen"},
    )
    foreign_test = client.post(
        "/ai-reply-test/admin_ai_reply_cookie",
        headers=user_auth,
        json={"message": "test"},
    )
    user_list = client.get("/ai-reply-settings", headers=user_auth)
    owner_read = client.get("/ai-reply-settings/admin_ai_reply_cookie", headers=auth)
    owner_update = client.put(
        "/ai-reply-settings/admin_ai_reply_cookie",
        headers=auth,
        json={
            "ai_enabled": True,
            "model_name": "updated-owner-model",
            "api_key": "updated-owner-secret",
            "base_url": "https://updated.example/v1",
            "api_type": "openai",
            "max_discount_percent": 15,
            "max_discount_amount": 45,
            "max_bargain_rounds": 4,
            "custom_prompts": "updated owner prompt",
        },
    )
    owner_test = client.post(
        "/ai-reply-test/admin_ai_reply_cookie",
        headers=auth,
        json={"message": "test", "item_title": "Item"},
    )

    assert foreign_read.status_code == 403
    assert foreign_update.status_code == 403
    assert foreign_test.status_code == 403
    assert user_list.status_code == 200
    assert set(user_list.json()) == {"user_ai_reply_cookie"}
    assert user_list.json()["user_ai_reply_cookie"]["model_name"] == "user-model"
    assert owner_read.status_code == 200
    assert owner_read.json()["model_name"] == "owner-model"
    assert owner_update.status_code == 200
    assert db_manager.get_ai_reply_settings("admin_ai_reply_cookie")["model_name"] == "updated-owner-model"
    assert owner_test.status_code == 200
    assert owner_test.json()["reply"] == "reply for admin_ai_reply_cookie"


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


def test_cookie_auto_confirm_is_scoped_to_owner(client, auth, user_auth):
    client.post(
        "/cookies",
        headers=auth,
        json={"id": "admin_auto_confirm_cookie", "value": "unb=admin"},
    )

    foreign_read = client.get("/cookies/admin_auto_confirm_cookie/auto-confirm", headers=user_auth)
    foreign_write = client.put(
        "/cookies/admin_auto_confirm_cookie/auto-confirm",
        headers=user_auth,
        json={"auto_confirm": True},
    )
    owner_write = client.put(
        "/cookies/admin_auto_confirm_cookie/auto-confirm",
        headers=auth,
        json={"auto_confirm": True},
    )
    owner_read = client.get("/cookies/admin_auto_confirm_cookie/auto-confirm", headers=auth)

    assert foreign_read.status_code == 403
    assert foreign_write.status_code == 403
    assert owner_write.status_code == 200
    assert owner_write.json()["auto_confirm"] is True
    assert owner_read.status_code == 200
    assert owner_read.json()["auto_confirm"] is True


def test_cookie_auto_comment_is_scoped_to_owner(client, auth, user_auth):
    client.post(
        "/cookies",
        headers=auth,
        json={"id": "admin_auto_comment_cookie", "value": "unb=admin"},
    )

    foreign_read = client.get("/cookies/admin_auto_comment_cookie/auto-comment", headers=user_auth)
    foreign_write = client.put(
        "/cookies/admin_auto_comment_cookie/auto-comment",
        headers=user_auth,
        json={"auto_comment": True},
    )
    owner_write = client.put(
        "/cookies/admin_auto_comment_cookie/auto-comment",
        headers=auth,
        json={"auto_comment": True},
    )
    owner_read = client.get("/cookies/admin_auto_comment_cookie/auto-comment", headers=auth)

    assert foreign_read.status_code == 403
    assert foreign_write.status_code == 403
    assert owner_write.status_code == 200
    assert owner_write.json()["auto_comment"] is True
    assert owner_read.status_code == 200
    assert owner_read.json()["auto_comment"] is True


def test_comment_template_id_must_belong_to_cookie(client, auth, user_auth):
    client.post(
        "/cookies",
        headers=auth,
        json={"id": "admin_template_cookie", "value": "unb=admin"},
    )
    client.post(
        "/cookies",
        headers=user_auth,
        json={"id": "user_template_cookie", "value": "unb=user"},
    )
    create_resp = client.post(
        "/cookies/admin_template_cookie/comment-templates",
        headers=auth,
        json={"name": "admin template", "content": "admin comment", "is_active": True},
    )
    template_id = create_resp.json()["template_id"]

    update_resp = client.put(
        f"/cookies/user_template_cookie/comment-templates/{template_id}",
        headers=user_auth,
        json={"name": "stolen template", "content": "stolen comment", "is_active": True},
    )
    activate_resp = client.put(
        f"/cookies/user_template_cookie/comment-templates/{template_id}/activate",
        headers=user_auth,
    )
    delete_resp = client.delete(
        f"/cookies/user_template_cookie/comment-templates/{template_id}",
        headers=user_auth,
    )
    owner_read = client.get("/cookies/admin_template_cookie/comment-templates", headers=auth)

    assert update_resp.status_code == 404
    assert activate_resp.status_code == 404
    assert delete_resp.status_code == 404
    assert owner_read.status_code == 200
    assert owner_read.json()["templates"] == [
        {
            **owner_read.json()["templates"][0],
            "id": template_id,
            "name": "admin template",
            "content": "admin comment",
            "is_active": True,
        }
    ]


def test_comment_template_list_and_create_are_scoped_to_owner(client, auth, user_auth):
    client.post(
        "/cookies",
        headers=auth,
        json={"id": "admin_template_list_cookie", "value": "unb=admin"},
    )

    foreign_read = client.get(
        "/cookies/admin_template_list_cookie/comment-templates",
        headers=user_auth,
    )
    foreign_create = client.post(
        "/cookies/admin_template_list_cookie/comment-templates",
        headers=user_auth,
        json={"name": "foreign template", "content": "foreign comment", "is_active": True},
    )
    owner_create = client.post(
        "/cookies/admin_template_list_cookie/comment-templates",
        headers=auth,
        json={"name": "owner template", "content": "owner comment", "is_active": True},
    )
    owner_read = client.get(
        "/cookies/admin_template_list_cookie/comment-templates",
        headers=auth,
    )

    assert foreign_read.status_code == 403
    assert foreign_create.status_code == 403
    assert owner_create.status_code == 200
    assert owner_read.status_code == 200
    templates = owner_read.json()["templates"]
    assert len(templates) == 1
    assert templates[0]["id"] == owner_create.json()["template_id"]
    assert templates[0]["name"] == "owner template"
    assert templates[0]["content"] == "owner comment"
    assert templates[0]["is_active"] is True
