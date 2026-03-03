import os
import json
import hashlib
import hmac
import base64
from pathlib import Path
from fastapi import Header, HTTPException
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC


HARDCODED_FILE_PASSWORD_HASH = "cde0a2b0950a47712ac7040323874f3fa2cc292d37d2cc798d270b9be067add2"
CREDENTIAL_FILE_PATH = Path(__file__).with_name("credential.txt")
ENCRYPTION_SALT = b"validation_tool_salt_v1"


def _load_api_keys() -> set[str]:
    raw = os.getenv("VALIDATION_API_KEYS") or os.getenv("VALIDATION_API_KEY") or ""
    keys = {k.strip() for k in raw.split(",") if k.strip()}
    return keys


def require_api_key(x_api_key: str | None = Header(default=None, alias="x-api-key")) -> None:
    keys = _load_api_keys()
    if not keys:
        raise HTTPException(status_code=500, detail="Server is missing VALIDATION_API_KEYS")

    if not x_api_key or x_api_key.strip() not in keys:
        raise HTTPException(status_code=401, detail="Invalid API key")


def _derive_key_from_password(password: str) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=ENCRYPTION_SALT,
        iterations=100000,
    )
    return base64.urlsafe_b64encode(kdf.derive(password.encode("utf-8")))


def _decrypt_content(encrypted_content: str, password: str) -> str:
    try:
        key = _derive_key_from_password(password)
        f = Fernet(key)
        decrypted = f.decrypt(encrypted_content.encode("utf-8"))
        return decrypted.decode("utf-8")
    except Exception as e:
        raise HTTPException(status_code=401, detail="Failed to decrypt credential file - invalid password or corrupted file")


def _encrypt_content(plain_content: str, password: str) -> str:
    key = _derive_key_from_password(password)
    f = Fernet(key)
    encrypted = f.encrypt(plain_content.encode("utf-8"))
    return encrypted.decode("utf-8")


def require_file_password(file_password: str | None) -> None:
    if not file_password or not file_password.strip():
        raise HTTPException(status_code=401, detail="credential_password is required")

    submitted_hash = hashlib.sha256(file_password.strip().encode("utf-8")).hexdigest()
    if not hmac.compare_digest(submitted_hash, HARDCODED_FILE_PASSWORD_HASH):
        raise HTTPException(status_code=401, detail="Invalid credential password")


def _parse_credentials_text(raw: str) -> dict[str, str]:
    text = (raw or "").strip()
    if not text:
        raise HTTPException(status_code=500, detail="credential.txt is empty")

    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return {str(k): "" if v is None else str(v) for k, v in parsed.items()}
    except Exception:
        pass

    values: dict[str, str] = {}
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if "=" not in s:
            raise HTTPException(status_code=500, detail="Invalid credential.txt format")
        key, val = s.split("=", 1)
        key = key.strip()
        if not key:
            raise HTTPException(status_code=500, detail="Invalid credential.txt key")
        values[key] = val.strip()

    if not values:
        raise HTTPException(status_code=500, detail="credential.txt has no usable entries")

    return values


def _required(credentials: dict[str, str], key: str) -> str:
    v = credentials.get(key)
    if v is None or not str(v).strip():
        raise HTTPException(status_code=500, detail=f"Missing credential '{key}' in credential.txt")
    return str(v).strip()


def load_locked_credentials(file_password: str | None) -> dict:
    require_file_password(file_password)

    if not CREDENTIAL_FILE_PATH.exists():
        raise HTTPException(status_code=500, detail="credential.txt not found")

    encrypted_content = CREDENTIAL_FILE_PATH.read_text(encoding="utf-8").strip()
    
    if not encrypted_content:
        raise HTTPException(status_code=500, detail="credential.txt is empty")
    
    decrypted_content = _decrypt_content(encrypted_content, file_password.strip())
    values = _parse_credentials_text(decrypted_content)

    return {
        "snowflake": {
            "account": _required(values, "snowflake.account"),
            "user": _required(values, "snowflake.user"),
            "password": _required(values, "snowflake.password"),
            "warehouse": _required(values, "snowflake.warehouse"),
            "role": (values.get("snowflake.role") or "").strip() or None,
        },
        "databricks": {
            "server_hostname": _required(values, "databricks.server_hostname"),
            "http_path": _required(values, "databricks.http_path"),
            "access_token": _required(values, "databricks.access_token"),
        },
    }
