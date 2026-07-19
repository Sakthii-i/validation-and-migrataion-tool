from __future__ import annotations

import logging
import os
from typing import Any
from urllib.parse import urlencode

import requests

logger = logging.getLogger(__name__)

SUPABASE_URL = (os.getenv("SUPABASE_URL") or "").rstrip("/")
SUPABASE_KEY = (os.getenv("SUPABASE_PUBLISHABLE_KEY") or os.getenv("SUPABASE_ANON_KEY") or "").strip()
SUPABASE_AUTH_TABLE = (os.getenv("SUPABASE_AUTH_TABLE") or "auth_credentials").strip()


def is_enabled() -> bool:
    return bool(SUPABASE_URL and SUPABASE_KEY)


def _headers(prefer: str | None = None) -> dict[str, str]:
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }
    if prefer:
        headers["Prefer"] = prefer
    return headers


def _auth_endpoint() -> str:
    return f"{SUPABASE_URL}/rest/v1/{SUPABASE_AUTH_TABLE}"


def _request(method: str, url: str, **kwargs):
    kwargs.setdefault("timeout", 20)
    resp = requests.request(method, url, **kwargs)
    if resp.status_code >= 300:
        raise RuntimeError(f"Supabase request failed {resp.status_code}: {resp.text}")
    if not resp.text:
        return None
    return resp.json()


def get_pg_conn():
    """Dummy connection object for compatibility. Supabase doesn't use connection objects."""
    return None


def ensure_credentials_table(conn=None) -> None:
    """No-op for Supabase. Table must be created manually or via migrations."""
    # Table structure expected:
    # - username (TEXT, PRIMARY KEY)
    # - password_hash (TEXT)
    # - created_at (TIMESTAMP)
    # - updated_at (TIMESTAMP)
    pass


def upsert_user(
    conn,
    username: str,
    password_hash: str,
    email: str | None = None,
    smtp_password: str | None = None,
) -> None:
    if not is_enabled():
        raise RuntimeError("Supabase not configured")

    # First, try to delete existing user
    try:
        delete_url = f"{_auth_endpoint()}?username=eq.{username}"
        requests.delete(delete_url, headers=_headers(), timeout=20)
    except Exception:
        pass

    # Then insert new user
    payload = {
        "username": username,
        "password_hash": password_hash,
        "email": email,
        "smtp_password": smtp_password,
    }

    try:
        _request(
            "POST",
            _auth_endpoint(),
            headers=_headers("return=minimal"),
            json=payload,
        )
    except Exception as e:
        logger.error("Supabase upsert_user failed: %s", e)
        raise RuntimeError(f"Failed to upsert user: {e}")


def get_password_hash(conn, username: str) -> str | None:
    if not is_enabled():
        return None

    query = urlencode({
        "select": "password_hash",
        "username": f"eq.{username}",
        "limit": "1",
    })

    try:
        data = _request("GET", f"{_auth_endpoint()}?{query}", headers=_headers()) or []
        if data:
            return data[0].get("password_hash")
        return None
    except Exception as e:
        logger.error("Supabase get_password_hash failed: %s", e)
        return None


def get_user(conn, username: str) -> dict[str, Any] | None:
    if not is_enabled():
        return None

    query = urlencode({
        "select": "username,email,password_hash,smtp_password",
        "username": f"eq.{username}",
        "limit": "1",
    })

    try:
        data = _request("GET", f"{_auth_endpoint()}?{query}", headers=_headers()) or []
        return data[0] if data else None
    except Exception as e:
        logger.error("Supabase get_user failed: %s", e)
        return None


def list_users(conn) -> list[dict[str, Any]]:
    if not is_enabled():
        return []

    query = urlencode({
        "select": "username,email",
        "order": "username.asc",
    })

    try:
        data = _request("GET", f"{_auth_endpoint()}?{query}", headers=_headers()) or []
        return [row for row in data if row and row.get("username")]
    except Exception as e:
        logger.error("Supabase list_users failed: %s", e)
        return []


def list_usernames(conn) -> list[str]:
    if not is_enabled():
        return []

    return [str(row.get("username")) for row in list_users(conn)]


def delete_user(conn, username: str) -> bool:
    if not is_enabled():
        return False

    delete_url = f"{_auth_endpoint()}?username=eq.{username}"

    try:
        resp = requests.delete(delete_url, headers=_headers(), timeout=20)
        return resp.status_code < 300
    except Exception as e:
        logger.error("Supabase delete_user failed: %s", e)
        return False
