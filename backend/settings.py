import os


def redis_url() -> str:
    return os.getenv("REDIS_URL", "redis://localhost:6379/0")


def session_ttl_seconds() -> int:
    return int(os.getenv("VALIDATION_SESSION_TTL_SECONDS", "3600"))


def max_concurrent_jobs_per_session() -> int:
    return int(os.getenv("VALIDATION_MAX_CONCURRENT_JOBS_PER_SESSION", "2"))


def session_concurrency_slot_ttl_seconds() -> int:
    # Safety TTL so leaked slots (crash/kill) don't block a session forever.
    return int(os.getenv("VALIDATION_SESSION_CONCURRENCY_SLOT_TTL_SECONDS", "1800"))
