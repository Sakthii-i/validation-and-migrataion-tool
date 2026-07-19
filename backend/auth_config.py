from __future__ import annotations

# Admin credentials are intentionally defined here ("hardcoded config"),
# as requested. Change these before deploying.

ADMIN_USERNAME = "admin"
ADMIN_EMAIL = "sakthid282004@gmail.com"

# Default admin password is: admin123
# Generated with PBKDF2-SHA256 (200k iterations).
ADMIN_PASSWORD_HASH = (
    "pbkdf2_sha256$200000$uPhvmKw0PqDhCDul1UMJGw==$I23o7zB2rbNAqxpfc2SIVz13AYJWDdSUH4kE1po7h6E="
)

# User credentials are admin-managed in auth.credentials (PostgreSQL).
