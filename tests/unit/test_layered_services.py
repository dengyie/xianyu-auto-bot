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
