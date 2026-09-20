"""In-memory session use cases, independent from FastAPI."""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable, MutableMapping
from typing import Any

from loguru import logger


class SessionPersistenceError(RuntimeError):
    """Raised when a session cannot be written to the durable store."""


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

    def _store_call(self, method_name: str, *args, default=None, required: bool = False, **kwargs):
        store = self.session_store
        method = getattr(store, method_name, None) if store is not None else None
        if method is None:
            if required:
                raise SessionPersistenceError(f"session store missing {method_name}")
            return default
        try:
            return method(*args, **kwargs)
        except Exception as exc:
            logger.error(f"session store {method_name} failed: {exc}")
            if required:
                raise SessionPersistenceError(str(exc)) from exc
            return default

    def _session_payload(
        self,
        user: dict[str, Any],
        created_at: float,
        expires_at: float | None = None,
    ) -> dict[str, Any]:
        return {
            "user_id": user["id"],
            "username": user["username"],
            "is_admin": bool(user.get("is_admin", False)),
            "timestamp": created_at,
            "expires_at": expires_at if expires_at is not None else created_at + self.expire_seconds,
        }

    def _is_expired(self, session: dict[str, Any]) -> bool:
        expires_at = session.get("expires_at")
        if expires_at is not None:
            return self.clock() >= float(expires_at)
        return self.clock() - float(session.get("timestamp", 0)) > self.expire_seconds

    def _evict(self, token: str) -> None:
        self.sessions.pop(token, None)
        self._store_call("delete_user_session", token, default=False)

    def _load_session(self, token: str) -> dict[str, Any] | None:
        session = self.sessions.get(token)
        if not session:
            db_session = self._store_call("get_user_session", token, default=None)
            if db_session:
                session = {
                    "user_id": db_session["user_id"],
                    "username": db_session["username"],
                    "is_admin": db_session["is_admin"],
                    "timestamp": db_session["created_at"],
                    "expires_at": db_session.get("expires_at"),
                }
                self.sessions[token] = session
        if not session:
            return None
        if self._is_expired(session):
            self._evict(token)
            return None
        return session

    def lookup(self, token: str) -> dict[str, Any] | None:
        """Return a live session without hitting the user table.

        Used by request logs and audit attribution so they share the same
        expiry rules as authentication, without a second user-loader path.
        """
        return self._load_session(token)

    def issue(self, user: dict[str, Any]) -> str:
        token = self.token_factory()
        now = self.clock()
        expires_at = now + self.expire_seconds
        payload = self._session_payload(user, now, expires_at)
        saved = self._store_call(
            "save_user_session",
            token=token,
            user_id=user["id"],
            username=user["username"],
            is_admin=bool(user.get("is_admin", False)),
            created_at=now,
            expires_at=expires_at,
            default=True,
            required=self.session_store is not None,
        )
        if self.session_store is not None and not saved:
            raise SessionPersistenceError("save_user_session returned a falsy result")
        self.sessions[token] = payload
        return token

    def verify(
        self,
        token: str,
        user_loader: Callable[[int], dict[str, Any] | None],
    ) -> dict[str, Any] | None:
        session = self._load_session(token)
        if not session:
            return None

        user = user_loader(session.get("user_id"))
        if not user or not user.get("is_active", True):
            self._evict(token)
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
        db_deleted = bool(self._store_call("delete_user_session", token, default=False))
        return popped or db_deleted

    def revoke_user(self, user_id: int) -> int:
        tokens = [
            token
            for token, session in list(self.sessions.items())
            if session.get("user_id") == user_id
        ]
        for token in tokens:
            self.sessions.pop(token, None)
        db_count = int(self._store_call("delete_user_sessions_by_user_id", user_id, default=0) or 0)
        return max(len(tokens), db_count)

    def clear(self) -> None:
        self.sessions.clear()
        logger.warning("cleared in-memory sessions only; durable user_sessions were left intact")
