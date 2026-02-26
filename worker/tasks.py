from __future__ import annotations

from datetime import datetime, timezone

from validation_tool.backend.postgres_store import (
    ensure_jobs_table,
    get_pg_conn,
    insert_validation_result,
    upsert_job_state,
)
from validation_tool.backend.session_store import get_session
from validation_tool.backend.validators import run_validation_job


def run_validation_task(validation_id: str, session_id: str, row: dict) -> None:
    pg = get_pg_conn()
    try:
        ensure_jobs_table(pg)
        upsert_job_state(pg, validation_id, state="RUNNING", started_ts=datetime.now(timezone.utc))

        session_payload = get_session(session_id)
        if not session_payload:
            upsert_job_state(
                pg,
                validation_id,
                state="FAILED",
                overall_status="ERROR",
                completed_ts=datetime.now(timezone.utc),
                error_message="Session expired or not found",
            )
            return

        row = dict(row or {})
        row["validation_id"] = validation_id

        record = run_validation_job(session_payload, row)
        insert_validation_result(pg, record)

        upsert_job_state(
            pg,
            validation_id,
            state="SUCCEEDED",
            overall_status=record.get("overall_status"),
            completed_ts=datetime.now(timezone.utc),
        )

    except Exception as e:
        upsert_job_state(
            pg,
            validation_id,
            state="FAILED",
            overall_status="ERROR",
            completed_ts=datetime.now(timezone.utc),
            error_message=str(e),
        )
        raise
    finally:
        try:
            pg.close()
        except Exception:
            pass
