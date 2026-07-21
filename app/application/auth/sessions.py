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
    ):
        self.sessions = sessions
        self.expire_seconds = expire_seconds
        self.token_factory = token_factory
        self.clock = clock

    def issue(self, user: dict[str, Any]) -> str:
        token = self.token_factory()
        self.sessions[token] = {
            "user_id": user["id"],
            "username": user["username"],
            "is_admin": bool(user.get("is_admin", False)),
            "timestamp": self.clock(),
        }
        return token

    def verify(
        self,
        token: str,
        user_loader: Callable[[int], dict[str, Any] | None],
    ) -> dict[str, Any] | None:
        session = self.sessions.get(token)
        if not session:
            return None
        if self.clock() - float(session.get("timestamp", 0)) > self.expire_seconds:
            self.sessions.pop(token, None)
            return None

        user = user_loader(session.get("user_id"))
        if not user or not user.get("is_active", True):
            self.sessions.pop(token, None)
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
        return self.sessions.pop(token, None) is not None

    def revoke_user(self, user_id: int) -> int:
        tokens = [
            token
            for token, session in self.sessions.items()
            if session.get("user_id") == user_id
        ]
        for token in tokens:
            self.sessions.pop(token, None)
        return len(tokens)

    def clear(self) -> None:
        self.sessions.clear()
