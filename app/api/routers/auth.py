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
    async def verify(
        request: Request,
        user_info: Optional[dict[str, Any]] = Depends(verify_dependency),
        credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    ):
        if user_info:
            payload = {
                "authenticated": True,
                "user_id": user_info["user_id"],
                "username": user_info["username"],
                "is_admin": user_info.get("is_admin", False)
                or user_info["username"] == admin_username,
            }
            # Only echo the token when the client authenticated via Cookie.
            # Callers that already sent Authorization already have it; echoing
            # it back would keep copying the HttpOnly session into JS storage.
            header_token = (credentials.credentials or "").strip() if credentials else ""
            if not header_token:
                payload["token"] = user_info.get("token") or request.cookies.get("auth_token")
            return payload
        return {"authenticated": False}

    @router.post("/logout")
    async def logout(
        request: Request,
        response: Response,
        credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    ):
        raw_token = (credentials.credentials or "").strip() if credentials else ""
        if not raw_token:
            raw_token = request.cookies.get("auth_token")
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
