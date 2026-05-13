from __future__ import annotations

import csv
import io
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from validation_tool.migration.schemas import (
    CacheClearResponse,
    ConfigResponse,
    CsvQueryResult,
    CsvTranslateResponse,
    DatabricksExecuteRequest,
    DatabricksExecuteResponse,
    GitFileRequest,
    GitFileResponse,
    GitFilesRequest,
    GitFilesResponse,
    NormalizeRequest,
    NormalizeResponse,
    StoredExecuteRequest,
    TranslateRequest,
    TranslateResponse,
)
from validation_tool.migration.sql_processor import SQLPreprocessor
from validation_tool.migration.translator_service import PROVIDER_MODEL_OPTIONS, TranslatorService
from validation_tool.api.auth import load_locked_credentials

router = APIRouter(prefix="/api/migration")
service = TranslatorService()
SQL_FILE_SUFFIXES = {".sql", ".bql", ".ddl", ".dml", ".txt"}


def _backend_credential_password() -> str:
    import os

    return (os.getenv("CREDENTIAL_PASSWORD") or "").strip()


def _stored_databricks_config() -> dict:
    password = _backend_credential_password()
    if not password:
        raise HTTPException(status_code=500, detail="Server is missing CREDENTIAL_PASSWORD for stored Databricks credentials.")

    locked = load_locked_credentials(password)
    databricks = locked["databricks"]
    return {
        "host": databricks["server_hostname"],
        "token": databricks["access_token"],
        "warehouse_id": databricks["http_path"],
        "catalog": None,
        "schema": None,
        "timeout_seconds": 90,
        "max_rows": 5,
    }


def _is_missing_object_error(message: str) -> bool:
    text = (message or "").lower()
    patterns = [
        "table or view not found",
        "table not found",
        "schema not found",
        "catalog not found",
        "object not found",
        "does not exist",
        "not found",
        "unresolved relation",
        "no such table",
    ]
    return any(pattern in text for pattern in patterns)


def _sample_query(sql: str) -> str:
    cleaned = (sql or "").strip().rstrip(";")
    return f"SELECT * FROM ({cleaned}) AS source_query_sample LIMIT 5"


def _rows_from_source_session(source_engine: str, source_sql: str, session_id: str | None) -> dict | None:
    if (source_engine or "").strip().lower() != "snowflake" or not (source_sql or "").strip():
        return None

    cursor = None
    try:
        from validation_tool.api.react_routes import _get_session
        from validation_tool.validation_engine import normalize_result

        session = _get_session(session_id)
        conn = session["source_conn"]

        cursor = conn.cursor()
        cursor.execute(_sample_query(source_sql))
        statement_id = getattr(cursor, "sfqid", None)
        columns = [col[0] for col in cursor.description] if cursor.description else []
        rows_raw = cursor.fetchall()
        rows = [normalize_result(dict(zip(columns, row))) for row in rows_raw[:5]]
        columns = list(rows[0].keys()) if rows else []
        return {
            "status": "SUCCEEDED",
            "statement_id": statement_id,
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
            "truncated": True,
        }
    except Exception as exc:
        return {
            "status": "FAILED",
            "error": str(exc),
            "columns": [],
            "rows": [],
            "row_count": 0,
        }
    finally:
        if cursor is not None:
            cursor.close()


def _repair_databricks_sql(sql: str, error: str, provider: str, model: str, api_key: str | None) -> tuple[str | None, str | None]:
    client = service.get_llm_client(provider, api_key)
    if client is None:
        return None, f"Databricks failed and {provider} API key is not available for repair."

    prompt = (
        "You are a Databricks SQL expert. Fix the SQL so it runs in Databricks SQL.\n"
        "Return only SQL, no markdown, no explanation.\n\n"
        f"Databricks error:\n{error}\n\n"
        f"SQL:\n{sql}"
    )
    fixed_sql, llm_error = service._llm_fix_with_prompt(prompt=prompt, client=client, model=model, provider=provider)
    if llm_error:
        return None, llm_error
    return fixed_sql.strip(), None


def _run_git(args: list[str], cwd: str | None = None) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            text=True,
            capture_output=True,
            timeout=60,
            check=True,
        )
        return result.stdout
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail="Git is not installed on the backend host.") from exc
    except subprocess.CalledProcessError as exc:
        message = (exc.stderr or exc.stdout or str(exc)).strip()
        raise HTTPException(status_code=400, detail=f"Git command failed: {message}") from exc
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(status_code=504, detail="Git command timed out.") from exc


def _clone_repo(repo_url: str, ref: str | None) -> tuple[str, str]:
    repo = (repo_url or "").strip()
    if not repo:
        raise HTTPException(status_code=400, detail="Git repository URL is required.")

    checkout_ref = (ref or "").strip()
    tmp_dir = tempfile.mkdtemp(prefix="migration_git_")
    args = ["clone", "--depth", "1", "--filter=blob:none", "--no-checkout"]
    if checkout_ref:
        args.extend(["--branch", checkout_ref])
    args.extend([repo, tmp_dir])

    try:
        _run_git(args)
        resolved_ref = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=tmp_dir).strip()
        if resolved_ref == "HEAD":
            resolved_ref = _run_git(["rev-parse", "--short", "HEAD"], cwd=tmp_dir).strip()
        return tmp_dir, resolved_ref
    except Exception:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise


def _safe_git_path(path: str) -> str:
    cleaned = (path or "").replace("\\", "/").strip().lstrip("/")
    if not cleaned or cleaned.startswith("../") or "/../" in cleaned:
        raise HTTPException(status_code=400, detail="Invalid Git file path.")
    return cleaned


def _list_repo_files(repo_url: str, ref: str | None) -> tuple[list[str], str]:
    repo_dir, resolved_ref = _clone_repo(repo_url, ref)
    try:
        raw = _run_git(["ls-tree", "-r", "--name-only", "HEAD"], cwd=repo_dir)
        files = [line.strip() for line in raw.splitlines() if line.strip()]
        sql_files = [file for file in files if Path(file).suffix.lower() in SQL_FILE_SUFFIXES]
        return sorted(sql_files or files), resolved_ref
    finally:
        shutil.rmtree(repo_dir, ignore_errors=True)


def _read_repo_file(repo_url: str, ref: str | None, path: str) -> tuple[str, str]:
    git_path = _safe_git_path(path)
    repo_dir, resolved_ref = _clone_repo(repo_url, ref)
    try:
        content = _run_git(["show", f"HEAD:{git_path}"], cwd=repo_dir)
        return content, resolved_ref
    finally:
        shutil.rmtree(repo_dir, ignore_errors=True)


@router.get("/config", response_model=ConfigResponse)
def config() -> ConfigResponse:
    return ConfigResponse(
        providers=["OpenAI", "Gemini", "Claude"],
        provider_model_options=PROVIDER_MODEL_OPTIONS,
        modes=[
            "Auto (deterministic -> LLM migration -> validation)",
            "Force deterministic only",
        ],
    )


@router.post("/preview/normalized", response_model=NormalizeResponse)
def normalized_preview(payload: NormalizeRequest) -> NormalizeResponse:
    return NormalizeResponse(normalized_sql=SQLPreprocessor.clean_sql(payload.sql))


@router.post("/git/files", response_model=GitFilesResponse)
def git_files(payload: GitFilesRequest) -> GitFilesResponse:
    files, resolved_ref = _list_repo_files(payload.repo_url, payload.ref)
    return GitFilesResponse(files=files, ref=resolved_ref)


@router.post("/git/file", response_model=GitFileResponse)
def git_file(payload: GitFileRequest) -> GitFileResponse:
    content, resolved_ref = _read_repo_file(payload.repo_url, payload.ref, payload.path)
    return GitFileResponse(path=payload.path, content=content, ref=resolved_ref)


@router.get("/cache/stats")
def cache_stats() -> dict:
    components = service.components
    return {
        "persistent": components["cache"].stats(),
        "expression": components["expr_cache"].stats(),
    }


@router.post("/cache/clear", response_model=CacheClearResponse)
def clear_cache() -> CacheClearResponse:
    components = service.components
    cleared = components["cache"].clear_all()
    components["expr_cache"].clear_all()
    return CacheClearResponse(cleared_persistent_entries=cleared, expression_cache_cleared=True)


@router.post("/translate", response_model=TranslateResponse)
def translate(payload: TranslateRequest) -> TranslateResponse:
    if not payload.bq_sql.strip():
        raise HTTPException(status_code=400, detail="Please enter a BigQuery SQL query.")

    use_llm = payload.mode == "Auto (deterministic -> LLM migration -> validation)"
    translated_sql, explanation, stats, final_error = service.run_pipeline(
        bq_sql=payload.bq_sql,
        model=payload.model,
        provider=payload.provider,
        api_key=payload.api_key,
        source_engine=payload.source_engine,
        force_llm=False,
        use_llm=use_llm,
    )

    validator = service.components["validator"]
    validation = validator.validate(translated_sql)
    suggestions = validator.suggest_fixes(validation) if not validation.is_valid else []
    execution = None

    if payload.run_in_databricks:
        if payload.databricks is None:
            raise HTTPException(status_code=400, detail="Databricks config is required when run_in_databricks is true.")
        try:
            execution = service.execute_databricks_sql(translated_sql, payload.databricks.model_dump())
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Databricks execution failed: {exc}") from exc

    return TranslateResponse(
        translated_sql=translated_sql,
        explanation=explanation,
        stats=stats,
        final_error=final_error,
        validation={
            "is_valid": validation.is_valid,
            "error_message": validation.error_message,
            "error_type": validation.error_type,
            "line_number": validation.line_number,
        },
        suggestions=suggestions,
        execution=execution,
    )


@router.post("/databricks/execute", response_model=DatabricksExecuteResponse)
def execute_databricks(payload: DatabricksExecuteRequest) -> DatabricksExecuteResponse:
    try:
        execution = service.execute_databricks_sql(payload.sql, payload.databricks.model_dump())
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Databricks execution failed: {exc}") from exc
    return DatabricksExecuteResponse(execution=execution)


@router.post("/databricks/execute-stored", response_model=DatabricksExecuteResponse)
def execute_databricks_stored(payload: StoredExecuteRequest) -> DatabricksExecuteResponse:
    if not payload.sql.strip():
        raise HTTPException(status_code=400, detail="SQL is required.")

    source_execution = _rows_from_source_session(payload.source_engine, payload.source_sql or "", payload.session_id)
    dbx_config = _stored_databricks_config()

    try:
        databricks_execution = service.execute_databricks_sql(payload.sql, dbx_config)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Databricks execution failed: {exc}") from exc

    final_execution = {
        "source": source_execution,
        "databricks": databricks_execution,
    }

    dbx_error = databricks_execution.get("error") if isinstance(databricks_execution, dict) else None
    if dbx_error and not _is_missing_object_error(dbx_error):
        repaired_sql, repair_error = _repair_databricks_sql(
            payload.sql,
            dbx_error,
            payload.provider,
            payload.model,
            payload.api_key,
        )
        if repaired_sql:
            repaired_execution = service.execute_databricks_sql(repaired_sql, dbx_config)
            final_execution["databricks"] = repaired_execution
            final_execution["repaired_sql"] = repaired_sql
            final_execution["repair_message"] = "Databricks returned an error, so the SQL was repaired with LLM and run again."
        elif repair_error:
            final_execution["repair_message"] = repair_error
    elif dbx_error:
        final_execution["repair_message"] = "Databricks object/catalog/schema/table error returned. LLM repair was skipped."

    if source_execution is None:
        final_execution.pop("source")

    return DatabricksExecuteResponse(execution=final_execution)


def _split_sql_queries(text: str) -> list[str]:
    queries: list[str] = []
    current: list[str] = []
    in_single = False
    in_double = False
    in_triple_dq = False
    in_triple_sq = False

    i = 0
    while i < len(text):
        if not in_single and not in_double and not in_triple_sq and text[i : i + 3] == '"""':
            in_triple_dq = not in_triple_dq
            current.append(text[i : i + 3])
            i += 3
            continue
        if not in_single and not in_double and not in_triple_dq and text[i : i + 3] == "'''":
            in_triple_sq = not in_triple_sq
            current.append(text[i : i + 3])
            i += 3
            continue

        ch = text[i]
        if not in_triple_dq and not in_triple_sq:
            if ch == "'" and not in_double:
                in_single = not in_single
            elif ch == '"' and not in_single:
                in_double = not in_double
            elif ch == ";" and not in_single and not in_double:
                stmt = "".join(current).strip()
                if stmt:
                    queries.append(stmt)
                current = []
                i += 1
                continue

        current.append(ch)
        i += 1

    last = "".join(current).strip()
    if last:
        queries.append(last)
    return queries


@router.post("/translate/csv", response_model=CsvTranslateResponse)
async def translate_csv(
    file: UploadFile = File(...),
    provider: str = Form("OpenAI"),
    source_engine: str = Form("bigquery"),
    model: str = Form(""),
    mode: str = Form("Auto (deterministic -> LLM migration -> validation)"),
    api_key: str = Form(""),
    run_in_databricks: bool = Form(False),
    databricks_host: str = Form(""),
    databricks_token: str = Form(""),
    databricks_warehouse_id: str = Form(""),
    databricks_catalog: str = Form(""),
    databricks_schema: str = Form(""),
    databricks_timeout_seconds: int = Form(90),
    databricks_max_rows: int = Form(200),
):
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Please upload a .csv file.")

    raw_bytes = await file.read()
    try:
        text = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw_bytes.decode("latin-1")

    reader = csv.DictReader(io.StringIO(text))
    fieldnames = reader.fieldnames or []

    sql_col = None
    for candidate in ("bq_sql", "sql", "query", "bigquery_sql", "bq_query"):
        for field in fieldnames:
            if field.strip().lower() == candidate:
                sql_col = field
                break
        if sql_col:
            break
    if not sql_col and fieldnames:
        sql_col = fieldnames[0]
    if not sql_col:
        raise HTTPException(status_code=400, detail="CSV must have at least one column, for example bq_sql, sql, or query.")

    use_llm = mode == "Auto (deterministic -> LLM migration -> validation)"
    validator = service.components["validator"]
    databricks_cfg = None
    if run_in_databricks:
        if not databricks_host.strip() or not databricks_token.strip() or not databricks_warehouse_id.strip():
            raise HTTPException(status_code=400, detail="Databricks host, token, and warehouse ID are required when run-in-Databricks is enabled.")
        databricks_cfg = {
            "host": databricks_host,
            "token": databricks_token,
            "warehouse_id": databricks_warehouse_id,
            "catalog": databricks_catalog,
            "schema": databricks_schema,
            "timeout_seconds": databricks_timeout_seconds,
            "max_rows": databricks_max_rows,
        }

    results: list[CsvQueryResult] = []
    translated_rows: list[dict[str, str]] = []

    for row_index, row in enumerate(reader):
        cell_value = (row.get(sql_col) or "").strip()
        translated_row = row.copy()
        if not cell_value:
            translated_rows.append(translated_row)
            continue

        translated_parts: list[str] = []
        for query_index, bq_sql in enumerate(_split_sql_queries(cell_value)):
            try:
                translated_sql, explanation, stats, final_error = service.run_pipeline(
                    bq_sql=bq_sql,
                    model=model,
                    provider=provider,
                    api_key=api_key or None,
                    source_engine=source_engine,
                    force_llm=False,
                    use_llm=use_llm,
                )
                validation = validator.validate(translated_sql)
                suggestions = validator.suggest_fixes(validation) if not validation.is_valid else []
                execution = None
                if databricks_cfg is not None:
                    try:
                        execution = service.execute_databricks_sql(translated_sql, databricks_cfg)
                    except Exception as exc:
                        execution = {"status": "FAILED", "error": str(exc)}

                translated_parts.append(translated_sql)
                results.append(CsvQueryResult(
                    row_index=row_index,
                    query_index=query_index,
                    original_sql=bq_sql,
                    translated_sql=translated_sql,
                    explanation=explanation,
                    stats=stats,
                    final_error=final_error,
                    validation={
                        "is_valid": validation.is_valid,
                        "error_message": validation.error_message,
                        "error_type": validation.error_type,
                        "line_number": validation.line_number,
                    },
                    suggestions=suggestions,
                    execution=execution,
                ))
            except Exception as exc:
                results.append(CsvQueryResult(
                    row_index=row_index,
                    query_index=query_index,
                    original_sql=bq_sql,
                    translated_sql=f"-- ERROR: {exc}",
                    explanation="",
                    stats={},
                    final_error=str(exc),
                    validation={"is_valid": False, "error_message": str(exc), "error_type": "runtime", "line_number": None},
                    suggestions=[],
                ))

        translated_row[sql_col] = ";\n".join(translated_parts)
        translated_rows.append({str(k): "" if v is None else str(v) for k, v in translated_row.items()})

    return CsvTranslateResponse(
        total_queries=len(results),
        results=results,
        headers=[str(h) for h in fieldnames if h is not None],
        translated_rows=translated_rows,
    )
