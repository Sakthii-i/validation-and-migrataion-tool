from __future__ import annotations

import psycopg2

from validation_tool.connections.postgres import POSTGRES_CONFIG


def get_pg_conn():
    return psycopg2.connect(
        host=POSTGRES_CONFIG["host"],
        port=POSTGRES_CONFIG["port"],
        dbname=POSTGRES_CONFIG["db"],
        user=POSTGRES_CONFIG["user"],
        password=POSTGRES_CONFIG["password"],
        sslmode=POSTGRES_CONFIG.get("sslmode", "require"),
    )


def ensure_credentials_table(pg_conn) -> None:
    ddl = """
    CREATE SCHEMA IF NOT EXISTS auth;

    CREATE TABLE IF NOT EXISTS auth.credentials (
        username TEXT PRIMARY KEY,
        password_hash TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    """

    cur = pg_conn.cursor()
    for stmt in [s.strip() for s in ddl.split(";") if s.strip()]:
        cur.execute(stmt)
    pg_conn.commit()
    cur.close()


def upsert_user(pg_conn, username: str, password_hash: str) -> None:
    ensure_credentials_table(pg_conn)

    cur = pg_conn.cursor()
    cur.execute(
        """
        INSERT INTO auth.credentials (username, password_hash)
        VALUES (%s, %s)
        ON CONFLICT (username) DO UPDATE SET
            password_hash = EXCLUDED.password_hash,
            updated_at = NOW()
        """,
        (username, password_hash),
    )
    pg_conn.commit()
    cur.close()


def get_password_hash(pg_conn, username: str) -> str | None:
    ensure_credentials_table(pg_conn)

    cur = pg_conn.cursor()
    cur.execute("SELECT password_hash FROM auth.credentials WHERE username = %s", (username,))
    row = cur.fetchone()
    cur.close()
    return row[0] if row else None


def list_usernames(pg_conn) -> list[str]:
    ensure_credentials_table(pg_conn)

    cur = pg_conn.cursor()
    cur.execute("SELECT username FROM auth.credentials ORDER BY username ASC")
    rows = cur.fetchall() or []
    cur.close()
    return [str(r[0]) for r in rows if r and r[0] is not None]


def delete_user(pg_conn, username: str) -> bool:
    ensure_credentials_table(pg_conn)

    cur = pg_conn.cursor()
    cur.execute("DELETE FROM auth.credentials WHERE username = %s", (username,))
    deleted = cur.rowcount > 0
    pg_conn.commit()
    cur.close()
    return deleted
