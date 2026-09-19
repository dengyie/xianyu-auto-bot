"""Authentication HTTP adapter for session verification and logout."""

from __future__ import annotations

from typing import Any, Callable, Optional

from fastapi import APIRouter, Depends, Request, Response
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
                "token": user_info.get("token"),
            }
        return {"authenticated": False}

    @router.post("/logout")
    async def logout(
        request: Request,
        response: Response,
        credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    ):
        raw_token = credentials.credentials if credentials else request.cookies.get("auth_token")
        if raw_token:
            session_service.revoke(raw_token)
        forwarded_proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
        is_secure = request.url.scheme == "https" or forwarded_proto == "https"
        response.delete_cookie(
            key="auth_token",
            path="/",
            httponly=True,
            samesite="lax",
            secure=is_secure,
        )
        return {"message": "已登出"}

    return router
