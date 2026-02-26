import os
from fastapi import Header, HTTPException


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
