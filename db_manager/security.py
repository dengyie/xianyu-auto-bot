import hashlib
import os
import secrets
import string

from loguru import logger
from passlib.context import CryptContext


password_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_user_password(password: str) -> str:
    return password_context.hash(password)


def is_legacy_sha256_hash(password_hash: str) -> bool:
    return isinstance(password_hash, str) and len(password_hash) == 64 and all(c in string.hexdigits for c in password_hash)


def verify_password_hash(password: str, password_hash: str) -> bool:
    if not password or not password_hash:
        return False
    if is_legacy_sha256_hash(password_hash):
        return hashlib.sha256(password.encode()).hexdigest() == password_hash
    try:
        return password_context.verify(password, password_hash)
    except Exception as e:
        logger.warning(f"密码哈希校验失败: {e}")
        return False


def generate_initial_admin_password() -> str:
    configured = (os.getenv('ADMIN_PASSWORD') or '').strip()
    if configured and configured not in {'admin123', 'default-secret-key', 'change-me-in-production'}:
        return configured
    return secrets.token_urlsafe(18)
