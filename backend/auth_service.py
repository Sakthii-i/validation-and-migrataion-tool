from __future__ import annotations

from backend.auth_config import ADMIN_PASSWORD_HASH, ADMIN_USERNAME
from backend.auth_crypto import verify_password
from backend.auth_store import get_password_hash


def is_admin_login(username: str, password: str) -> bool:
    if not username or not password:
        return False
    if username.strip() != ADMIN_USERNAME:
        return False
    return verify_password(password, ADMIN_PASSWORD_HASH)


def is_user_authorized(pg_conn, username: str, password: str) -> bool:
    if not username or not password:
        return False
    stored = get_password_hash(pg_conn, username.strip())
    if not stored:
        return False
    return verify_password(password, stored)
