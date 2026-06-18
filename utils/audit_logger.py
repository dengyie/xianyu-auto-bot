"""Structured audit logging helpers."""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from loguru import logger


SENSITIVE_KEYWORDS = (
    "authorization",
    "captcha",
    "cookie",
    "key",
    "password",
    "proxy_pass",
    "secret",
    "token",
)

VALID_STATUSES = {"success", "failed", "denied", "error"}


def _is_sensitive_key(key: Any) -> bool:
    normalized = str(key or "").lower()
    return any(keyword in normalized for keyword in SENSITIVE_KEYWORDS)


def redact_audit_details(value: Any) -> Any:
    """Recursively redact sensitive values before persistence."""
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            if _is_sensitive_key(key):
                redacted[str(key)] = "<redacted>"
            else:
                redacted[str(key)] = redact_audit_details(item)
        return redacted
    if isinstance(value, list):
        return [redact_audit_details(item) for item in value]
    if isinstance(value, tuple):
        return [redact_audit_details(item) for item in value]
    return value


def _safe_details_json(details: Optional[Dict[str, Any]]) -> Optional[str]:
    if details is None:
        return None
    try:
        return json.dumps(redact_audit_details(details), ensure_ascii=False, default=str)
    except Exception as exc:
        logger.warning(f"Failed to serialize audit details: {exc}")
        return json.dumps({"serialization_error": str(exc)}, ensure_ascii=False)


def normalize_audit_status(status: str) -> str:
    normalized = str(status or "success").lower()
    return normalized if normalized in VALID_STATUSES else "success"


def status_from_http_status_code(status_code: int) -> str:
    if status_code in (401, 403):
        return "denied"
    if 200 <= status_code < 400:
        return "success"
    if status_code >= 500:
        return "error"
    return "failed"


def actor_from_user(user: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not user:
        return {}
    return {
        "actor_user_id": user.get("user_id") or user.get("id"),
        "actor_username": user.get("username"),
        "actor_is_admin": bool(user.get("is_admin", False)),
    }


def request_ip(request: Any) -> Optional[str]:
    if not request:
        return None
    forwarded_for = request.headers.get("X-Forwarded-For", "") if hasattr(request, "headers") else ""
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    real_ip = request.headers.get("X-Real-IP", "") if hasattr(request, "headers") else ""
    if real_ip:
        return real_ip
    client = getattr(request, "client", None)
    return getattr(client, "host", None)


def record_audit_event(
    db_manager: Any,
    *,
    category: str,
    action: str,
    status: str = "success",
    actor: Optional[Dict[str, Any]] = None,
    request: Any = None,
    request_method: str = None,
    request_path: str = None,
    client_ip: str = None,
    resource_type: str = None,
    resource_id: Any = None,
    duration_ms: int = None,
    message: str = None,
    details: Optional[Dict[str, Any]] = None,
) -> int:
    """Persist an audit event without letting audit failures break callers."""
    try:
        actor_fields = actor_from_user(actor)
        return db_manager.add_audit_log(
            category=str(category or "system"),
            action=str(action or "unknown"),
            status=normalize_audit_status(status),
            actor_user_id=actor_fields.get("actor_user_id"),
            actor_username=actor_fields.get("actor_username"),
            actor_is_admin=actor_fields.get("actor_is_admin", False),
            client_ip=client_ip or request_ip(request),
            request_method=request_method or (getattr(request, "method", None) if request else None),
            request_path=request_path or (str(getattr(getattr(request, "url", None), "path", "")) if request else None),
            resource_type=resource_type,
            resource_id=None if resource_id is None else str(resource_id),
            duration_ms=duration_ms,
            message=message,
            details_json=_safe_details_json(details),
        )
    except Exception as exc:
        logger.warning(f"Audit event dropped: {exc}")
        return 0
