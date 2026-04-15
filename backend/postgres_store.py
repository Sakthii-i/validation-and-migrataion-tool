from __future__ import annotations

from datetime import datetime

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


def ensure_validation_results_table(pg_conn) -> None:
    ddl = """
    CREATE SCHEMA IF NOT EXISTS table_validation;

    CREATE TABLE IF NOT EXISTS table_validation.validation_results (
        validation_id TEXT PRIMARY KEY,
        validation_ts TIMESTAMPTZ,
        src_table_name TEXT,
        tgt_table_name TEXT,
        validation_type TEXT,
        run_by TEXT,
        row_count TEXT,
        schema_check TEXT,
        numeric_check TEXT,
        hash_validation TEXT
    );
    """

    cur = pg_conn.cursor()
    for stmt in [s.strip() for s in ddl.split(";") if s.strip()]:
        cur.execute(stmt)
    # Backward-compatible migrations for existing deployments.
    cur.execute("ALTER TABLE IF EXISTS table_validation.validation_results ADD COLUMN IF NOT EXISTS run_by TEXT")
    pg_conn.commit()
    cur.close()


def ensure_jobs_table(pg_conn) -> None:
    ddl = """
    CREATE SCHEMA IF NOT EXISTS table_validation;

    CREATE TABLE IF NOT EXISTS table_validation.validation_jobs (
        validation_id TEXT PRIMARY KEY,
        state TEXT NOT NULL,
        overall_status TEXT NULL,
        submitted_ts TIMESTAMPTZ NOT NULL,
        started_ts TIMESTAMPTZ NULL,
        completed_ts TIMESTAMPTZ NULL,
        error_message TEXT NULL
    );
    """

    cur = pg_conn.cursor()
    for stmt in [s.strip() for s in ddl.split(";") if s.strip()]:
        cur.execute(stmt)
    pg_conn.commit()
    cur.close()


def upsert_job_state(
    pg_conn,
    validation_id: str,
    state: str,
    overall_status: str | None = None,
    started_ts: datetime | None = None,
    completed_ts: datetime | None = None,
    error_message: str | None = None,
) -> None:
    cur = pg_conn.cursor()
    cur.execute(
        """
        INSERT INTO table_validation.validation_jobs (
            validation_id, state, overall_status, submitted_ts, started_ts, completed_ts, error_message
        ) VALUES (%s, %s, %s, NOW(), %s, %s, %s)
        ON CONFLICT (validation_id) DO UPDATE SET
            state = EXCLUDED.state,
            overall_status = COALESCE(EXCLUDED.overall_status, table_validation.validation_jobs.overall_status),
            started_ts = COALESCE(EXCLUDED.started_ts, table_validation.validation_jobs.started_ts),
            completed_ts = COALESCE(EXCLUDED.completed_ts, table_validation.validation_jobs.completed_ts),
            error_message = COALESCE(EXCLUDED.error_message, table_validation.validation_jobs.error_message)
        """,
        (validation_id, state, overall_status, started_ts, completed_ts, error_message),
    )
    pg_conn.commit()
    cur.close()


def insert_validation_result(pg_conn, record: dict) -> None:
    ensure_validation_results_table(pg_conn)

    cur = pg_conn.cursor()
    cur.execute(
        """
        INSERT INTO table_validation.validation_results (
            validation_id,
            validation_ts,
            src_table_name,
            tgt_table_name,
            validation_type,
            run_by,
            row_count,
            schema_check,
            numeric_check,
            hash_validation
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (validation_id) DO UPDATE SET
            validation_ts = EXCLUDED.validation_ts,
            src_table_name = EXCLUDED.src_table_name,
            tgt_table_name = EXCLUDED.tgt_table_name,
            validation_type = EXCLUDED.validation_type,
            run_by = COALESCE(EXCLUDED.run_by, table_validation.validation_results.run_by),
            row_count = EXCLUDED.row_count,
            schema_check = EXCLUDED.schema_check,
            numeric_check = EXCLUDED.numeric_check,
            hash_validation = EXCLUDED.hash_validation
        """,
        (
            record.get("validation_id"),
            record.get("validation_ts"),
            record.get("src_table_name"),
            record.get("tgt_table_name"),
            record.get("validation_type"),
            record.get("run_by"),
            record.get("row_count"),
            record.get("schema_check"),
            record.get("numeric_check"),
            record.get("hash_validation"),
        ),
    )
    pg_conn.commit()
    cur.close()


def get_job(pg_conn, validation_id: str) -> dict | None:
    cur = pg_conn.cursor()
    cur.execute(
        """
        SELECT validation_id, state, overall_status, submitted_ts, started_ts, completed_ts, error_message
        FROM table_validation.validation_jobs
        WHERE validation_id = %s
        """,
        (validation_id,),
    )
    row = cur.fetchone()
    cols = [d[0] for d in cur.description] if cur.description else []
    cur.close()
    if not row:
        return None
    return dict(zip(cols, row))


def get_result(pg_conn, validation_id: str) -> dict | None:
    # On fresh databases, the results table may not exist yet (e.g., job queued/running).
    # Ensure it exists so SELECT returns 0 rows instead of raising UndefinedTable.
    ensure_validation_results_table(pg_conn)

    cur = pg_conn.cursor()
    cur.execute(
        """
        SELECT validation_id, validation_ts, src_table_name, tgt_table_name, validation_type,
               row_count, schema_check, numeric_check, hash_validation
        FROM table_validation.validation_results
        WHERE validation_id = %s
        """,
        (validation_id,),
    )
    row = cur.fetchone()
    cols = [d[0] for d in cur.description] if cur.description else []
    cur.close()
    if not row:
        return None
    return dict(zip(cols, row))
