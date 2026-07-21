"""Authentication HTTP adapter for session verification and logout."""

from __future__ import annotations

from typing import Any, Callable, Optional

from fastapi import APIRouter, Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer


def create_auth_router(
    session_service: Any,
    verify_dependency: Callable[..., Optional[dict[str, Any]]],
    security: HTTPBearer,
    admin_username: str,
) -> APIRouter:
    router = APIRouter()

    @router.get("/verify")
    async def verify(user_info: Optional[dict[str, Any]] = Depends(verify_dependency)):
        if user_info:
            return {
                "authenticated": True,
                "user_id": user_info["user_id"],
                "username": user_info["username"],
                "is_admin": user_info.get("is_admin", False)
                or user_info["username"] == admin_username,
            }
        return {"authenticated": False}

    @router.post("/logout")
    async def logout(
        credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    ):
        if credentials:
            session_service.revoke(credentials.credentials)
        return {"message": "已登出"}

    return router
