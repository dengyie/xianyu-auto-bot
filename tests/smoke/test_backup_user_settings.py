"""User-scoped backup and user-settings regressions."""

import json


def _backup_table(columns, rows):
    return {"columns": columns, "rows": rows}


def test_user_settings_are_scoped_and_missing_key_returns_404(client, auth, user_auth):
    admin_update = client.put(
        "/user-settings/dashboard_density",
        headers=auth,
        json={"value": "compact", "description": "admin preference"},
    )
    user_update = client.put(
        "/user-settings/dashboard_density",
        headers=user_auth,
        json={"value": "comfortable", "description": "user preference"},
    )

    admin_list = client.get("/user-settings", headers=auth)
    user_list = client.get("/user-settings", headers=user_auth)
    admin_read = client.get("/user-settings/dashboard_density", headers=auth)
    user_read = client.get("/user-settings/dashboard_density", headers=user_auth)
    missing = client.get("/user-settings/missing_key", headers=user_auth)

    assert admin_update.status_code == 200
    assert user_update.status_code == 200
    assert admin_list.status_code == 200
    assert user_list.status_code == 200
    assert admin_list.json()["dashboard_density"]["value"] == "compact"
    assert user_list.json()["dashboard_density"]["value"] == "comfortable"
    assert admin_read.json()["value"] == "compact"
    assert user_read.json()["value"] == "comfortable"
    assert missing.status_code == 404


def test_user_backup_export_only_contains_current_user_cookie_data(client, auth, user_auth):
    from db_manager import db_manager

    client.post("/cookies", headers=auth, json={"id": "admin_backup_cookie", "value": "unb=admin"})
    client.post("/cookies", headers=user_auth, json={"id": "user_backup_cookie", "value": "unb=user"})
    db_manager.save_keywords(
        "admin_backup_cookie",
        [("admin-only", "admin reply")],
    )
    db_manager.save_keywords(
        "user_backup_cookie",
        [("user-only", "user reply")],
    )

    admin_export = client.get("/backup/export", headers=auth)
    user_export = client.get("/backup/export", headers=user_auth)

    assert admin_export.status_code == 200
    assert user_export.status_code == 200
    assert admin_export.json()["user_id"] == 1
    assert user_export.json()["user_id"] == 2
    assert [row[0] for row in admin_export.json()["data"]["cookies"]["rows"]] == ["admin_backup_cookie"]
    assert [row[0] for row in user_export.json()["data"]["cookies"]["rows"]] == ["user_backup_cookie"]
    assert admin_export.json()["data"]["keywords"]["rows"][0][1] == "admin-only"
    assert user_export.json()["data"]["keywords"]["rows"][0][1] == "user-only"


def test_user_backup_import_rebinds_owned_resources_and_skips_system_settings(client, user_auth):
    from db_manager import db_manager

    db_manager.set_system_setting("phase75_user_import_probe", "before")
    backup_data = {
        "version": "1.0",
        "timestamp": 1,
        "user_id": 999,
        "data": {
            "cookies": _backup_table(
                ["id", "value", "user_id"],
                [["imported_cookie", "unb=imported", 999]],
            ),
            "cards": _backup_table(
                ["id", "name", "type", "text_content", "user_id"],
                [[101, "imported card", "text", "hello", 999]],
            ),
            "delivery_rules": _backup_table(
                ["id", "keyword", "card_id", "delivery_count", "enabled", "description", "user_id"],
                [[201, "imported-keyword", 101, 1, 1, "imported rule", 999]],
            ),
            "notification_channels": _backup_table(
                ["id", "name", "type", "config", "enabled", "user_id"],
                [[301, "imported channel", "webhook", json.dumps({"url": "https://example.test"}), 1, 999]],
            ),
            "system_settings": _backup_table(
                ["key", "value", "description"],
                [["phase75_user_import_probe", "after", "should not import for user restore"]],
            ),
        },
    }

    imported = client.post(
        "/backup/import",
        headers=user_auth,
        files={"file": ("backup.json", json.dumps(backup_data), "application/json")},
    )

    assert imported.status_code == 200
    assert db_manager.get_cookie_details("imported_cookie")["user_id"] == 2
    assert db_manager.get_card_by_id(101, 2)["name"] == "imported card"
    assert db_manager.get_card_by_id(101, 999) is None
    assert [rule["id"] for rule in db_manager.get_all_delivery_rules(2)] == [201]
    assert db_manager.get_all_delivery_rules(999) == []
    assert [channel["id"] for channel in db_manager.get_notification_channels(2)] == [301]
    assert db_manager.get_notification_channels(999) == []
    assert db_manager.get_system_setting("phase75_user_import_probe") == "before"


def test_backup_import_preserves_validation_status_codes(client, user_auth):
    wrong_extension = client.post(
        "/backup/import",
        headers=user_auth,
        files={"file": ("backup.txt", "{}", "text/plain")},
    )
    malformed_json = client.post(
        "/backup/import",
        headers=user_auth,
        files={"file": ("backup.json", "{not-json", "application/json")},
    )

    assert wrong_extension.status_code == 400
    assert malformed_json.status_code == 400
