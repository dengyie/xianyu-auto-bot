"""Shared test fixtures — tokens, auth headers, and FastAPI TestClient."""
import os
import sys
import time
import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

os.environ["DB_PATH"] = ":memory:"
os.environ.setdefault("SQL_LOG_ENABLED", "false")

import reply_server


def _make_token(user_id, username="admin", is_admin=True):
    """Put a token into SESSION_TOKENS and return it."""
    import secrets
    token = secrets.token_urlsafe(32)
    reply_server.SESSION_TOKENS[token] = {
        "user_id": user_id,
        "username": username,
        "is_admin": is_admin,
        "timestamp": time.time(),
    }
    return token


@pytest.fixture
def admin_token() -> str:
    """Create an admin user in DB and return a valid Bearer token."""
    db = reply_server.db_manager
    if db.get_user_by_username("admin"):
        db.update_user_password("admin", "admin123")
    else:
        db.create_user("admin", "admin@test.local", "admin123")
    return _make_token(1, "admin", True)


@pytest.fixture
def auth(admin_token: str) -> dict:
    """Return auth headers for admin user."""
    return {"Authorization": f"Bearer {admin_token}"}


@pytest.fixture
def user_token() -> str:
    """Create a regular user and return a valid Bearer token."""
    db = reply_server.db_manager
    db.create_user("testuser", "test@test.local", "test123")
    return _make_token(2, "testuser", False)


@pytest.fixture
def user_auth(user_token: str) -> dict:
    """Return auth headers for regular user."""
    return {"Authorization": f"Bearer {user_token}"}


@pytest.fixture
def other_user_token() -> str:
    """Create a second regular user and return a valid Bearer token."""
    db = reply_server.db_manager
    username = "testuser2"
    if db.get_user_by_username(username):
        db.update_user_password(username, "test123")
    else:
        db.create_user(username, "test2@test.local", "test123")
    return _make_token(3, username, False)


@pytest.fixture
def other_user_auth(other_user_token: str) -> dict:
    """Return auth headers for a second regular user."""
    return {"Authorization": f"Bearer {other_user_token}"}


@pytest.fixture
def client():
    """Return a FastAPI TestClient for the app."""
    from fastapi.testclient import TestClient
    return TestClient(reply_server.app)
