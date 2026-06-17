"""Scheduled task ownership and validation regressions."""


def _add_cookie(client, headers, cookie_id):
    resp = client.post(
        "/cookies",
        headers=headers,
        json={"id": cookie_id, "value": f"unb={cookie_id}; cookie2=test"},
    )
    assert resp.status_code == 200


def _create_task(client, headers, account_id, run_hour=8, enabled=True):
    resp = client.post(
        "/scheduled-tasks",
        headers=headers,
        json={
            "account_id": account_id,
            "run_hour": run_hour,
            "random_delay_max": 3,
            "enabled": enabled,
        },
    )
    assert resp.status_code == 200
    return resp


def test_user_can_create_and_list_own_scheduled_task(client, user_auth):
    _add_cookie(client, user_auth, "user_task_cookie")

    create = _create_task(client, user_auth, "user_task_cookie", run_hour=9)
    listed = client.get("/scheduled-tasks", headers=user_auth)

    assert create.json()["success"] is True
    assert listed.status_code == 200
    assert listed.json()["success"] is True
    assert len(listed.json()["tasks"]) == 1
    task = listed.json()["tasks"][0]
    assert task["account_id"] == "user_task_cookie"
    assert task["delay_minutes"] == 9


def test_duplicate_account_task_updates_existing_task(client, user_auth):
    _add_cookie(client, user_auth, "duplicate_task_cookie")

    first = _create_task(client, user_auth, "duplicate_task_cookie", run_hour=8)
    second = _create_task(client, user_auth, "duplicate_task_cookie", run_hour=17, enabled=False)
    listed = client.get("/scheduled-tasks", headers=user_auth)

    assert first.json()["success"] is True
    assert second.json()["success"] is True
    assert second.json()["task_id"] == first.json()["task_id"]
    assert len(listed.json()["tasks"]) == 1
    task = listed.json()["tasks"][0]
    assert task["delay_minutes"] == 17
    assert task["enabled"] is False


def test_invalid_run_hour_returns_non_success_without_creating_task(client, user_auth):
    _add_cookie(client, user_auth, "invalid_hour_cookie")

    create = _create_task(client, user_auth, "invalid_hour_cookie", run_hour=24)
    listed = client.get("/scheduled-tasks", headers=user_auth)

    assert create.json()["success"] is False
    assert listed.json()["tasks"] == []


def test_regular_user_cannot_create_task_for_foreign_cookie(client, auth, user_auth):
    _add_cookie(client, auth, "admin_task_cookie")

    create = _create_task(client, user_auth, "admin_task_cookie", run_hour=10)

    assert create.json()["success"] is False


def test_regular_user_cannot_update_delete_or_toggle_another_users_task(client, auth, user_auth):
    _add_cookie(client, auth, "admin_owned_task_cookie")
    task_id = _create_task(client, auth, "admin_owned_task_cookie", run_hour=7).json()["task_id"]

    update = client.put(
        f"/scheduled-tasks/{task_id}",
        headers=user_auth,
        json={"name": "stolen", "run_hour": 12, "enabled": False},
    )
    toggle = client.put(f"/scheduled-tasks/{task_id}/toggle", headers=user_auth)
    delete = client.delete(f"/scheduled-tasks/{task_id}", headers=user_auth)

    assert update.status_code == 200
    assert toggle.status_code == 200
    assert delete.status_code == 200
    assert update.json()["success"] is False
    assert toggle.json()["success"] is False
    assert delete.json()["success"] is False
