import os


def _env(name: str, default: str | None = None) -> str | None:
    v = os.getenv(name)
    if v is None:
        return default
    v = v.strip()
    return v if v else default


def _env_int(name: str, default: int) -> int:
    v = _env(name)
    if v is None:
        return default
    try:
        return int(v)
    except Exception:
        return default


# =============================
# POSTGRES CONNECTION
# - For local/dev, set env vars: POSTGRES_HOST, POSTGRES_PORT, POSTGRES_DB,
#   POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_SSLMODE
# - Falls back to existing hardcoded defaults for backward compatibility.
# =============================
POSTGRES_CONFIG = {
    "host": _env("POSTGRES_HOST", "mrid.postgres.database.azure.com"),
    "port": _env_int("POSTGRES_PORT", 5432),
    "db": _env("POSTGRES_DB", "postgres"),
    "user": _env("POSTGRES_USER", "mridulla"),
    "password": _env("POSTGRES_PASSWORD", "snu-1309"),
    "sslmode": _env("POSTGRES_SSLMODE", "require"),
}
