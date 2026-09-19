"""Unit contracts for the first layered architecture slices."""

from __future__ import annotations

import importlib

import pytest


def _import_required(module_name: str):
    try:
        return importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        pytest.fail(f"required layered module is missing: {exc}")


def test_session_service_issues_validates_expires_and_revokes_tokens():
    module = _import_required("app.application.auth.sessions")
    now = 100.0
    sessions = {}
    service = module.SessionService(
        sessions=sessions,
        expire_seconds=60,
        token_factory=lambda: "token-1",
        clock=lambda: now,
    )
    user = {"id": 7, "username": "alice", "is_admin": False, "is_active": True}

    token = service.issue(user)
    assert token == "token-1"
    assert service.verify(token, lambda user_id: user if user_id == 7 else None)["username"] == "alice"

    now = 161.0
    assert service.verify(token, lambda _user_id: user) is None
    assert token not in sessions

    now = 200.0
    token = service.issue(user)
    assert service.revoke(token) is True
    assert service.revoke(token) is False


def test_session_service_supports_session_store_persistence_and_rehydration():
    module = _import_required("app.application.auth.sessions")
    now = 100.0
    sessions = {}
    store_db = {}

    class FakeSessionStore:
        def save_user_session(self, token, user_id, username, is_admin, created_at, expires_at):
            store_db[token] = {
                "token": token,
                "user_id": user_id,
                "username": username,
                "is_admin": is_admin,
                "created_at": created_at,
                "expires_at": expires_at,
            }
            return True

        def get_user_session(self, token):
            item = store_db.get(token)
            if item and item["expires_at"] > now:
                return item
            return None

        def delete_user_session(self, token):
            return store_db.pop(token, None) is not None

        def delete_user_sessions_by_user_id(self, user_id):
            to_del = [k for k, v in store_db.items() if v["user_id"] == user_id]
            for k in to_del:
                del store_db[k]
            return len(to_del)

    fake_store = FakeSessionStore()
    service = module.SessionService(
        sessions=sessions,
        expire_seconds=60,
        token_factory=lambda: "token-persisted-1",
        clock=lambda: now,
        session_store=fake_store,
    )
    user = {"id": 10, "username": "bob", "is_admin": False, "is_active": True}

    # 1. issue 存入内存与持久化
    token = service.issue(user)
    assert token == "token-persisted-1"
    assert token in sessions
    assert token in store_db

    # 2. 模拟内存被清空，verify 应从持久化恢复
    sessions.clear()
    assert token not in sessions
    res = service.verify(token, lambda uid: user if uid == 10 else None)
    assert res is not None
    assert res["username"] == "bob"
    assert token in sessions  # 验证重新写回内存

    # 3. 禁用用户应踢出会话并同步删除持久化记录
    user["is_active"] = False
    assert service.verify(token, lambda uid: user if uid == 10 else None) is None
    assert token not in sessions
    assert token not in store_db

    # 4. revoke_user 级联删除
    user["is_active"] = True
    token2 = service.issue(user)
    assert token2 in store_db
    revoked_count = service.revoke_user(10)
    assert revoked_count >= 1
    assert token2 not in store_db


def test_account_ownership_policy_returns_owned_id_and_raises_typed_errors():
    module = _import_required("app.domain.accounts.ownership")

    class Accounts:
        def get_all_cookies(self, user_id):
            return {"owned": "cookie"} if user_id == 3 else {}

    policy = module.AccountOwnershipPolicy(Accounts())

    assert policy.require_owned_account(3, " owned ") == "owned"
    with pytest.raises(module.MissingAccountId):
        policy.require_owned_account(3, " ")
    with pytest.raises(module.AccountForbidden):
        policy.require_owned_account(3, "foreign")


def test_manual_delivery_context_loader_validates_order_ownership():
    module = _import_required("app.application.orders.delivery")

    class Repository:
        def get_order_by_id(self, order_id):
            if order_id == "missing":
                return None
            if order_id == "orphan":
                return {"order_id": order_id, "cookie_id": None}
            return {"order_id": order_id, "cookie_id": "account-1"}

        def get_cookie_details(self, cookie_id):
            return {"id": cookie_id, "user_id": 9}

    loader = module.ManualDeliveryContextLoader(Repository())

    context = loader.load("order-1", user_id=9)
    assert context.cookie_id == "account-1"
    assert context.order["order_id"] == "order-1"
    with pytest.raises(module.OrderNotFound):
        loader.load("missing", user_id=9)
    with pytest.raises(module.MissingOrderAccount):
        loader.load("orphan", user_id=9)
    with pytest.raises(module.ForbiddenOrder):
        loader.load("order-1", user_id=8)
