import hashlib
import os
import secrets
import string

import bcrypt
from loguru import logger


BCRYPT_SHA256_PREFIX = "bcrypt_sha256$"
BCRYPT_MAX_PASSWORD_BYTES = 72


def _password_bytes(password: str) -> bytes:
    return str(password or '').encode('utf-8')


def _bcrypt_sha256_bytes(password: str) -> bytes:
    digest = hashlib.sha256(_password_bytes(password)).hexdigest()
    return f"sha256:{digest}".encode('ascii')


def hash_user_password(password: str) -> str:
    raw_password = _password_bytes(password)
    if len(raw_password) > BCRYPT_MAX_PASSWORD_BYTES:
        hashed = bcrypt.hashpw(_bcrypt_sha256_bytes(password), bcrypt.gensalt()).decode('utf-8')
        return f"{BCRYPT_SHA256_PREFIX}{hashed}"
    return bcrypt.hashpw(raw_password, bcrypt.gensalt()).decode('utf-8')


def is_legacy_sha256_hash(password_hash: str) -> bool:
    return isinstance(password_hash, str) and len(password_hash) == 64 and all(c in string.hexdigits for c in password_hash)


def verify_password_hash(password: str, password_hash: str) -> bool:
    if not password or not password_hash:
        return False
    if is_legacy_sha256_hash(password_hash):
        return hashlib.sha256(password.encode()).hexdigest() == password_hash
    if password_hash.startswith(BCRYPT_SHA256_PREFIX):
        bcrypt_hash = password_hash[len(BCRYPT_SHA256_PREFIX):].encode('utf-8')
        try:
            return bcrypt.checkpw(_bcrypt_sha256_bytes(password), bcrypt_hash)
        except Exception as e:
            logger.warning(f"密码哈希校验失败: {e}")
            return False
    try:
        raw_password = _password_bytes(password)
        if len(raw_password) > BCRYPT_MAX_PASSWORD_BYTES:
            raw_password = raw_password[:BCRYPT_MAX_PASSWORD_BYTES]
        return bcrypt.checkpw(raw_password, password_hash.encode('utf-8'))
    except Exception as e:
        logger.warning(f"密码哈希校验失败: {e}")
        return False


def generate_initial_admin_password() -> str:
    configured = (os.getenv('ADMIN_PASSWORD') or '').strip()
    if configured and configured not in {'admin123', 'default-secret-key', 'change-me-in-production'}:
        return configured
    return secrets.token_urlsafe(18)
