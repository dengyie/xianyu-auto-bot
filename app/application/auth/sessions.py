"""In-memory session use cases, independent from FastAPI."""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable, MutableMapping
from typing import Any


class SessionService:
    def __init__(
        self,
        sessions: MutableMapping[str, dict[str, Any]],
        expire_seconds: int,
        token_factory: Callable[[], str] = lambda: secrets.token_urlsafe(32),
        clock: Callable[[], float] = time.time,
        session_store: Any = None,
    ):
        self.sessions = sessions
        self.expire_seconds = expire_seconds
        self.token_factory = token_factory
        self.clock = clock
        self.session_store = session_store

    def issue(self, user: dict[str, Any]) -> str:
        token = self.token_factory()
        now = self.clock()
        self.sessions[token] = {
            "user_id": user["id"],
            "username": user["username"],
            "is_admin": bool(user.get("is_admin", False)),
            "timestamp": now,
        }
        if self.session_store and hasattr(self.session_store, "save_user_session"):
            try:
                self.session_store.save_user_session(
                    token=token,
                    user_id=user["id"],
                    username=user["username"],
                    is_admin=bool(user.get("is_admin", False)),
                    created_at=now,
                    expires_at=now + self.expire_seconds,
                )
            except Exception:
                pass
        return token

    def verify(
        self,
        token: str,
        user_loader: Callable[[int], dict[str, Any] | None],
    ) -> dict[str, Any] | None:
        session = self.sessions.get(token)
        # 内存未命中时，尝试从持久化存储中回源加载
        if not session and self.session_store and hasattr(self.session_store, "get_user_session"):
            try:
                db_session = self.session_store.get_user_session(token)
                if db_session:
                    session = {
                        "user_id": db_session["user_id"],
                        "username": db_session["username"],
                        "is_admin": db_session["is_admin"],
                        "timestamp": db_session["created_at"],
                    }
                    self.sessions[token] = session
            except Exception:
                pass

        if not session:
            return None
        if self.clock() - float(session.get("timestamp", 0)) > self.expire_seconds:
            self.sessions.pop(token, None)
            if self.session_store and hasattr(self.session_store, "delete_user_session"):
                try:
                    self.session_store.delete_user_session(token)
                except Exception:
                    pass
            return None

        user = user_loader(session.get("user_id"))
        if not user or not user.get("is_active", True):
            self.sessions.pop(token, None)
            if self.session_store and hasattr(self.session_store, "delete_user_session"):
                try:
                    self.session_store.delete_user_session(token)
                except Exception:
                    pass
            return None

        refreshed = {
            **session,
            "user_id": user["id"],
            "username": user["username"],
            "is_admin": bool(user.get("is_admin", False)),
        }
        self.sessions[token] = refreshed
        return refreshed

    def revoke(self, token: str) -> bool:
        popped = self.sessions.pop(token, None) is not None
        db_deleted = False
        if self.session_store and hasattr(self.session_store, "delete_user_session"):
            try:
                db_deleted = bool(self.session_store.delete_user_session(token))
            except Exception:
                pass
        return popped or db_deleted

    def revoke_user(self, user_id: int) -> int:
        tokens = [
            token
            for token, session in list(self.sessions.items())
            if session.get("user_id") == user_id
        ]
        for token in tokens:
            self.sessions.pop(token, None)
        db_count = 0
        if self.session_store and hasattr(self.session_store, "delete_user_sessions_by_user_id"):
            try:
                db_count = int(self.session_store.delete_user_sessions_by_user_id(user_id) or 0)
            except Exception:
                pass
        return max(len(tokens), db_count)

    def clear(self) -> None:
        self.sessions.clear()

