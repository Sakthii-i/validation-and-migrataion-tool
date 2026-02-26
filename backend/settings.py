import os


def redis_url() -> str:
    return os.getenv("REDIS_URL", "redis://localhost:6379/0")


def session_ttl_seconds() -> int:
    return int(os.getenv("VALIDATION_SESSION_TTL_SECONDS", "3600"))
