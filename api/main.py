from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from rq import Retry
from api.auth import load_locked_credentials, require_api_key
from validation_tool.api.schemas import (
    CreateSessionRequest,
    CreateSessionResponse,
    GetValidationResponse,
    SubmitValidationsResponse,
    ValidationJobPublicStatus,
    ValidationResultRow,
)
from validation_tool.backend.csv_parser import parse_validations_csv
from validation_tool.backend.supabase_jobs_store import (
    ensure_jobs_table,
    get_job,
    get_pg_conn,
    get_result,
    upsert_job_state,
)
from validation_tool.backend.session_store import create_session
from validation_tool.worker.queue import get_queue
from validation_tool.worker.tasks import run_validation_task
from fastapi.middleware.cors import CORSMiddleware

from .validation_routes import router as validation_router
from .react_routes import router as react_router
from .migration_routes import router as migration_router

# Then, after creating the app instance, include the router:


app = FastAPI(title="Validation API", version="1.0")

# CORS for React dev server
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://frontend:3000", "*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(validation_router, prefix="")
app.include_router(react_router)
app.include_router(migration_router)

@app.post("/sessions", response_model=CreateSessionResponse, dependencies=[Depends(require_api_key)])
def create_session_endpoint(req: CreateSessionRequest):
    source_engine = (req.source_engine or "").strip().lower()

    if source_engine == "snowflake":
        if req.credential_password:
            locked = load_locked_credentials(req.credential_password)
            source_payload = locked["snowflake"]
            target_payload = locked["databricks"]
        else:
            source_payload = req.source
            target_payload = req.target.model_dump() if req.target else {}
    elif source_engine == "bigquery":
        if req.credential_password:
            locked = load_locked_credentials(req.credential_password)
            target_payload = locked["databricks"]
        else:
            target_payload = req.target.model_dump() if req.target else {}
        source_payload = req.source
    elif source_engine == "trino":
        if req.credential_password:
            locked = load_locked_credentials(req.credential_password)
            source_payload = locked.get("trino", req.source)
            target_payload = locked["databricks"]
        else:
            source_payload = req.source
            target_payload = req.target.model_dump() if req.target else {}
    else:
        raise HTTPException(status_code=400, detail="source_engine must be bigquery, snowflake, or trino")

    payload = {
        "source_engine": source_engine,
        "source": source_payload,
        "target": target_payload,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    session_id, expires_at = create_session(payload)
    return CreateSessionResponse(session_id=session_id, expires_at=expires_at)


@app.post(
    "/validations",
    response_model=SubmitValidationsResponse,
    dependencies=[Depends(require_api_key)],
)
def submit_validations(
    session_id: str = Form(...),
    file: UploadFile = File(...),
):
    if not session_id.strip():
        raise HTTPException(status_code=400, detail="session_id is required")

    csv_bytes = file.file.read()
    rows = parse_validations_csv(csv_bytes)

    pg = get_pg_conn()
    try:
        ensure_jobs_table(pg)

        q = get_queue()
        validation_ids: list[str] = []

        for row in rows:
            validation_id = str(uuid.uuid4())
            validation_ids.append(validation_id)

            upsert_job_state(pg, validation_id, state="PENDING")

            q.enqueue(
                run_validation_task,
                validation_id,
                session_id,
                row,
                job_id=validation_id,
                retry=Retry(max=60, interval=[2, 5, 10, 20, 30, 60]),
                result_ttl=3600,
                failure_ttl=86400,
            )

        return SubmitValidationsResponse(session_id=session_id, validation_ids=validation_ids)

    finally:
        try:
            pg.close()
        except Exception:
            pass


@app.get(
    "/validations/{validation_id}",
    response_model=GetValidationResponse,
    dependencies=[Depends(require_api_key)],
)
def get_validation(validation_id: str):
    pg = get_pg_conn()
    try:
        job = get_job(pg, validation_id)
        if not job:
            raise HTTPException(status_code=404, detail="validation_id not found")

        state = (job.get("state") or "").upper()
        status_map = {
            "PENDING": "queued",
            "RUNNING": "running",
            "SUCCEEDED": "completed",
            "FAILED": "failed",
        }
        job["status"] = status_map.get(state, state.lower() if state else None)

        created_at = job.get("submitted_ts")
        updated_at = job.get("completed_ts") or job.get("started_ts") or job.get("submitted_ts")
        error = job.get("error_message")

        result = None
        if job.get("state") in {"SUCCEEDED", "FAILED"}:
            result = get_result(pg, validation_id)
            if result is not None and job.get("overall_status") and not result.get("overall_status"):
                result["overall_status"] = job.get("overall_status")

        job_model = ValidationJobPublicStatus(
            status=job.get("status") or "unknown",
            created_at=created_at,
            updated_at=updated_at,
            error=error,
        )
        result_model = ValidationResultRow(**result) if result else None

        return GetValidationResponse(validation_id=validation_id, job=job_model, result=result_model)

    finally:
        try:
            pg.close()
        except Exception:
            pass
