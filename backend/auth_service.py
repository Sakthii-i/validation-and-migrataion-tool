from __future__ import annotations

from validation_tool.backend.auth_config import ADMIN_PASSWORD_HASH, ADMIN_USERNAME
from validation_tool.backend.auth_crypto import hash_password, verify_password
from validation_tool.backend.supabase_auth_store import delete_user, get_password_hash, get_pg_conn, list_users, list_usernames, upsert_user


def is_admin_login(username: str, password: str) -> bool:
    if not username or not password:
        return False
    if username.strip() != ADMIN_USERNAME:
        return False
    return verify_password(password, ADMIN_PASSWORD_HASH)


def is_user_authorized(username: str, password: str) -> bool:
    if not username or not password:
        return False

    uname = username.strip()
    if not uname:
        return False

    try:
        conn = get_pg_conn()
        try:
            stored = get_password_hash(conn, uname)
        finally:
            if conn is not None:
                conn.close()
    except Exception:
        return False

    return verify_password(password, stored or "")


def grant_user_access(
    username: str,
    password: str,
    email: str | None = None,
    smtp_password: str | None = None,
) -> None:
    uname = (username or "").strip()
    pwd = password or ""
    if not uname or not pwd:
        raise ValueError("username and password are required")

    encoded = hash_password(pwd)
    conn = get_pg_conn()
    try:
        upsert_user(
            conn,
            uname,
            encoded,
            email=(email or "").strip() or None,
            smtp_password=smtp_password or None,
        )
    finally:
        if conn is not None:
            conn.close()


def revoke_user_access(username: str) -> bool:
    uname = (username or "").strip()
    if not uname:
        return False

    conn = get_pg_conn()
    try:
        return delete_user(conn, uname)
    finally:
        if conn is not None:
            conn.close()


def list_authorized_users() -> list[str]:
    conn = get_pg_conn()
    try:
        return list_usernames(conn)
    finally:
        if conn is not None:
            conn.close()


def list_authorized_user_details() -> list[dict]:
    conn = get_pg_conn()
    try:
        return list_users(conn)
    finally:
        if conn is not None:
            conn.close()
