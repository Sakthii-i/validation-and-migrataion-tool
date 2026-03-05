from __future__ import annotations

import base64
import hashlib
import hmac
import secrets


def hash_password(password: str, iterations: int = 200_000) -> str:
    if password is None:
        raise ValueError("password is required")
    password = str(password)
    salt = secrets.token_bytes(16)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return "pbkdf2_sha256${}${}${}".format(
        iterations,
        base64.b64encode(salt).decode("utf-8"),
        base64.b64encode(derived).decode("utf-8"),
    )


def verify_password(password: str, encoded_hash: str) -> bool:
    if not password or not encoded_hash:
        return False

    try:
        algo, it_s, salt_b64, hash_b64 = encoded_hash.split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        iterations = int(it_s)
        salt = base64.b64decode(salt_b64.encode("utf-8"))
        expected = base64.b64decode(hash_b64.encode("utf-8"))
    except Exception:
        return False

    derived = hashlib.pbkdf2_hmac("sha256", str(password).encode("utf-8"), salt, iterations)
    return hmac.compare_digest(derived, expected)
