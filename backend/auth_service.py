from __future__ import annotations

from backend.auth_config import ADMIN_PASSWORD_HASH, ADMIN_USERNAME, USER_PASSWORD, USER_USERNAME
from backend.auth_crypto import verify_password


def is_admin_login(username: str, password: str) -> bool:
    if not username or not password:
        return False
    if username.strip() != ADMIN_USERNAME:
        return False
    return verify_password(password, ADMIN_PASSWORD_HASH)


def is_user_authorized(username: str, password: str) -> bool:
    if not username or not password:
        return False
    if username.strip() != USER_USERNAME:
        return False
    return password == USER_PASSWORD
