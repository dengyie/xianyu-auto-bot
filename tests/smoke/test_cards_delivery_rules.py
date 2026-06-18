"""Card and delivery-rule ownership regressions."""


def _create_card(client, headers, name):
    resp = client.post(
        "/cards",
        headers=headers,
        json={
            "name": name,
            "type": "text",
            "text_content": f"content for {name}",
            "description": f"description for {name}",
            "enabled": True,
        },
    )
    assert resp.status_code == 200
    return resp.json()["id"]


def _create_delivery_rule(client, headers, *, keyword, card_id):
    resp = client.post(
        "/delivery-rules",
        headers=headers,
        json={
            "keyword": keyword,
            "card_id": card_id,
            "delivery_count": 1,
            "enabled": True,
            "description": f"rule for {keyword}",
        },
    )
    assert resp.status_code == 200
    return resp.json()["id"]


def test_cards_and_delivery_rules_are_scoped_to_owner(client, auth, user_auth):
    admin_card_id = _create_card(client, auth, "admin card")
    user_card_id = _create_card(client, user_auth, "user card")

    admin_cards = client.get("/cards", headers=auth)
    user_cards = client.get("/cards", headers=user_auth)
    foreign_card_read = client.get(f"/cards/{admin_card_id}", headers=user_auth)
    foreign_card_update = client.put(
        f"/cards/{admin_card_id}",
        headers=user_auth,
        json={"name": "stolen card", "type": "text", "text_content": "stolen"},
    )
    foreign_rule_create = client.post(
        "/delivery-rules",
        headers=user_auth,
        json={
            "keyword": "stolen rule",
            "card_id": admin_card_id,
            "delivery_count": 1,
            "enabled": True,
        },
    )
    owner_card_read = client.get(f"/cards/{admin_card_id}", headers=auth)
    owner_card_update = client.put(
        f"/cards/{admin_card_id}",
        headers=auth,
        json={"name": "updated admin card", "type": "text", "text_content": "updated"},
    )
    admin_rule_id = _create_delivery_rule(
        client,
        auth,
        keyword="admin keyword",
        card_id=admin_card_id,
    )
    admin_rules = client.get("/delivery-rules", headers=auth)
    user_rules = client.get("/delivery-rules", headers=user_auth)
    foreign_rule_read = client.get(f"/delivery-rules/{admin_rule_id}", headers=user_auth)
    foreign_rule_update = client.put(
        f"/delivery-rules/{admin_rule_id}",
        headers=user_auth,
        json={
            "keyword": "stolen keyword",
            "card_id": user_card_id,
            "delivery_count": 2,
            "enabled": False,
        },
    )
    owner_rule_read = client.get(f"/delivery-rules/{admin_rule_id}", headers=auth)
    owner_rule_update = client.put(
        f"/delivery-rules/{admin_rule_id}",
        headers=auth,
        json={
            "keyword": "updated keyword",
            "card_id": admin_card_id,
            "delivery_count": 3,
            "enabled": False,
            "description": "updated rule",
        },
    )
    foreign_rule_delete = client.delete(f"/delivery-rules/{admin_rule_id}", headers=user_auth)
    owner_rule_delete = client.delete(f"/delivery-rules/{admin_rule_id}", headers=auth)
    foreign_card_delete = client.delete(f"/cards/{admin_card_id}", headers=user_auth)
    owner_card_delete = client.delete(f"/cards/{admin_card_id}", headers=auth)

    assert admin_cards.status_code == 200
    assert [card["id"] for card in admin_cards.json()] == [admin_card_id]
    assert user_cards.status_code == 200
    assert [card["id"] for card in user_cards.json()] == [user_card_id]
    assert foreign_card_read.status_code == 404
    assert foreign_card_update.status_code == 404
    assert foreign_rule_create.status_code == 404
    assert owner_card_read.status_code == 200
    assert owner_card_read.json()["name"] == "admin card"
    assert owner_card_update.status_code == 200
    assert admin_rules.status_code == 200
    assert [rule["id"] for rule in admin_rules.json()] == [admin_rule_id]
    assert user_rules.status_code == 200
    assert user_rules.json() == []
    assert foreign_rule_read.status_code == 404
    assert foreign_rule_update.status_code == 404
    assert owner_rule_read.status_code == 200
    assert owner_rule_read.json()["keyword"] == "admin keyword"
    assert owner_rule_update.status_code == 200
    assert foreign_rule_delete.status_code == 404
    assert owner_rule_delete.status_code == 200
    assert foreign_card_delete.status_code == 404
    assert owner_card_delete.status_code == 200
