from __future__ import annotations

import csv
import base64
import io
import os
import re
import shutil
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlparse

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
import httpx

from migration.schemas import (
    CacheClearResponse,
    ConfigResponse,
    CsvQueryResult,
    CsvTranslateResponse,
    DatabricksExecuteRequest,
    DatabricksExecuteResponse,
    GitBranchesRequest,
    GitBranchesResponse,
    GitCollaboratorsRequest,
    GitCollaboratorsResponse,
    GitFileRequest,
    GitFileResponse,
    GitFilesRequest,
    GitFilesResponse,
    GitUploadRequest,
    GitUploadResponse,
    NormalizeRequest,
    NormalizeResponse,
    StoredExecuteRequest,
    QueryStatsResponse,
    TranslateRequest,
    TranslateResponse,
)
from migration.complexity_analyzer import QueryComplexityAnalyzer
from migration.sql_processor import SQLPreprocessor
from migration.translator_service import PROVIDER_MODEL_OPTIONS, TranslatorService
from api.auth import load_locked_credentials
from backend import supabase_store
from backend.session_store import get_query_stats, update_query_stats

router = APIRouter(prefix="/api/migration")
service = TranslatorService()
complexity_analyzer = QueryComplexityAnalyzer()
SQL_FILE_SUFFIXES = {".sql", ".bql", ".ddl", ".dml", ".txt"}


def _backend_credential_password() -> str:
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
        "cannot be found",
        "missing_table_or_view",
        "TABLE_OR_VIEW_NOT_FOUND",
    ]
    return any(pattern in text for pattern in patterns)


def _should_forward_databricks_error_to_llm(message: str) -> bool:
    return bool(message and not _is_missing_object_error(message))


def _normalize_query(sql: str) -> str:
    return (sql or "").strip().rstrip(";")


def _enforce_source_engine_match(source_engine: str, sql: str) -> None:
    normalized_engine = (source_engine or "").strip().lower()
    if normalized_engine not in ("bigquery", "snowflake", "trino"):
        raise HTTPException(status_code=400, detail="source_engine must be bigquery, snowflake, or trino")

    detected = SQLPreprocessor.detect_source_engine(sql)
    expected = {"bigquery": "BigQuery", "snowflake": "Snowflake", "trino": "Trino"}[normalized_engine]
    if detected in ("unknown", "ambiguous"):
        return

    if detected != normalized_engine:
        actual = {"bigquery": "BigQuery", "snowflake": "Snowflake", "trino": "Trino"}.get(detected, detected)
        raise HTTPException(
            status_code=400,
            detail=(
                f"Selected source engine is {expected}, but the SQL looks like {actual}. "
                f"Switch Source Engine to {actual} or paste {expected} SQL."
            ),
        )


def _rows_from_source_session(source_engine: str, source_sql: str, session_id: str | None) -> dict | None:
    normalized_engine = (source_engine or "").strip().lower()
    if normalized_engine not in {"snowflake", "trino"} or not (source_sql or "").strip():
        return None

    cursor = None
    started = time.perf_counter()
    try:
        from api.react_routes import _get_session
        from validation_engine import normalize_result

        session = _get_session(session_id)
        conn = session["source_conn"]

        cursor = conn.cursor()
        if normalized_engine == "snowflake":
            try:
                cursor.execute("ALTER SESSION SET USE_CACHED_RESULT = FALSE")
            except Exception:
                pass
        cursor.execute(_normalize_query(source_sql))
        statement_id = getattr(cursor, "sfqid", None)
        columns = [col[0] for col in cursor.description] if cursor.description else []
        rows_raw = cursor.fetchmany(5)
        rows = [normalize_result(dict(zip(columns, row))) for row in rows_raw]
        columns = list(rows[0].keys()) if rows else []

        execution_time_ms = int((time.perf_counter() - started) * 1000)

        return {
            "status": "SUCCEEDED",
            "statement_id": statement_id,
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
            "truncated": True,
            "execution_time_ms": execution_time_ms,
        }
    except Exception as exc:
        return {
            "status": "FAILED",
            "error": str(exc),
            "columns": [],
            "rows": [],
            "row_count": 0,
            "execution_time_ms": int((time.perf_counter() - started) * 1000),
        }
    finally:
        if cursor is not None:
            cursor.close()


def _repair_databricks_sql(sql: str, error: str, provider: str, model: str, api_key: str | None) -> tuple[str | None, str | None]:
    client = service.get_llm_client(provider, api_key)
    if client is None:
        return None, f"Databricks failed and {provider} API key is not available for repair."

    error_l = (error or "").lower()
    extra_guidance = ""
    if "scalar_subquery_is_in_group_by_or_aggregate_function" in error_l or "scalar subquery" in error_l:
        extra_guidance = (
            "\nThe failure is caused by a correlated scalar subquery inside a grouped query. "
            "Rewrite it as a LEFT JOIN, INNER JOIN, or grouped CTE keyed by the outer columns. "
            "Do not leave any scalar subquery inside SELECT, GROUP BY, or aggregate arguments. "
            "If a lookup returns one row per key, precompute it in a CTE and join it back. "
            "Use ANY_VALUE, FIRST, or MAX_BY only when they preserve the intended single-value semantics.\n"
        )

    prompt = (
        "You are a Databricks SQL expert. Fix the SQL so it runs in Databricks SQL.\n"
        "Return only SQL, no markdown, no explanation.\n"
        f"{extra_guidance}\n"
        f"Databricks error:\n{error}\n\n"
        f"SQL:\n{sql}"
    )
    fixed_sql, llm_error = service._llm_fix_with_prompt(prompt=prompt, client=client, model=model, provider=provider)
    if llm_error:
        return None, llm_error
    return fixed_sql.strip(), None


def _parse_github_repo(repo_url: str) -> tuple[str, str] | None:
    parsed = urlparse((repo_url or "").strip())
    if parsed.netloc.lower() not in {"github.com", "www.github.com"}:
        return None

    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(parts) < 2:
        return None

    owner = parts[0]
    repo = parts[1]
    if repo.endswith(".git"):
        repo = repo[:-4]
    return owner, repo


def _github_headers(token: str | None) -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "validation-tool-query-converter",
    }
    resolved_token = (token or os.getenv("GITHUB_TOKEN") or "").strip()
    if resolved_token:
        headers["Authorization"] = f"Bearer {resolved_token}"
    return headers


def _github_request(url: str, token: str | None = None) -> dict:
    try:
        response = httpx.get(
            url,
            headers=_github_headers(token),
            timeout=30,
        )
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text
        try:
            detail = exc.response.json().get("message", detail)
        except Exception:
            pass
        if exc.response.status_code == 404 and "/repos/" in str(exc.request.url):
            detail = (
                "Repository or ref was not found. Check the owner/repo spelling, "
                "or enter a Git access token for a private repository."
            )
        raise HTTPException(status_code=400, detail=f"GitHub request failed: {detail}") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"GitHub request failed: {exc}") from exc


def _github_send(method: str, url: str, token: str | None = None, json_payload: dict | None = None) -> dict:
    try:
        response = httpx.request(
            method,
            url,
            headers=_github_headers(token),
            json=json_payload,
            timeout=30,
        )
        response.raise_for_status()
        return response.json() if response.text else {}
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text
        try:
            detail = exc.response.json().get("message", detail)
        except Exception:
            pass
        raise HTTPException(status_code=400, detail=f"GitHub request failed: {detail}") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"GitHub request failed: {exc}") from exc


def _github_default_ref(owner: str, repo: str, token: str | None = None) -> str:
    data = _github_request(f"https://api.github.com/repos/{owner}/{repo}", token)
    return data.get("default_branch") or "main"


def _list_github_branches(repo_url: str, token: str | None = None) -> tuple[list[str], str] | None:
    parsed = _parse_github_repo(repo_url)
    if parsed is None:
        return None

    owner, repo = parsed
    default_ref = _github_default_ref(owner, repo, token)
    branches: list[str] = []
    page = 1
    while page <= 10:
        data = _github_request(f"https://api.github.com/repos/{owner}/{repo}/branches?per_page=100&page={page}", token)
        if not isinstance(data, list) or not data:
            break
        branches.extend(str(item.get("name", "")) for item in data if item.get("name"))
        if len(data) < 100:
            break
        page += 1

    return branches, default_ref


def _list_github_files(repo_url: str, ref: str | None, token: str | None = None) -> tuple[list[str], str] | None:
    parsed = _parse_github_repo(repo_url)
    if parsed is None:
        return None

    owner, repo = parsed
    requested_ref = (ref or "").strip()
    default_ref = _github_default_ref(owner, repo, token)
    resolved_ref = requested_ref or default_ref
    try:
        tree = _github_request(f"https://api.github.com/repos/{owner}/{repo}/git/trees/{resolved_ref}?recursive=1", token)
    except HTTPException as exc:
        if not requested_ref or requested_ref == default_ref:
            raise
        resolved_ref = default_ref
        try:
            tree = _github_request(f"https://api.github.com/repos/{owner}/{repo}/git/trees/{resolved_ref}?recursive=1", token)
        except HTTPException:
            raise exc
    files = [
        item.get("path", "")
        for item in tree.get("tree", [])
        if item.get("type") == "blob" and item.get("path")
    ]
    sql_files = [file for file in files if Path(file).suffix.lower() in SQL_FILE_SUFFIXES]
    return sorted(sql_files or files), resolved_ref


def _read_github_file(repo_url: str, ref: str | None, path: str, token: str | None = None) -> tuple[str, str] | None:
    parsed = _parse_github_repo(repo_url)
    if parsed is None:
        return None

    owner, repo = parsed
    requested_ref = (ref or "").strip()
    default_ref = _github_default_ref(owner, repo, token)
    resolved_ref = requested_ref or default_ref
    git_path = _safe_git_path(path)
    encoded_path = "/".join(quote(part, safe="") for part in git_path.split("/"))
    raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{resolved_ref}/{encoded_path}"

    try:
        response = httpx.get(raw_url, headers=_github_headers(token), timeout=30, follow_redirects=True)
        response.raise_for_status()
        return response.text, resolved_ref
    except httpx.HTTPStatusError as exc:
        if requested_ref and requested_ref != default_ref:
            resolved_ref = default_ref
            raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{resolved_ref}/{encoded_path}"
            response = httpx.get(raw_url, headers=_github_headers(token), timeout=30, follow_redirects=True)
            response.raise_for_status()
            return response.text, resolved_ref
        raise HTTPException(status_code=400, detail=f"GitHub file request failed: {exc.response.text}") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"GitHub file request failed: {exc}") from exc


def _github_branch_commit_sha(owner: str, repo: str, branch: str, token: str | None) -> str:
    encoded_branch = quote(branch, safe="")
    data = _github_request(f"https://api.github.com/repos/{owner}/{repo}/git/ref/heads/{encoded_branch}", token)
    sha = ((data.get("object") or {}).get("sha") or "").strip()
    if not sha:
        raise HTTPException(status_code=400, detail=f"Could not resolve branch '{branch}'.")
    return sha


def _create_github_branch(owner: str, repo: str, base_branch: str, new_branch: str, token: str | None) -> str:
    branch_name = (new_branch or "").strip()
    if not branch_name or branch_name.startswith("/") or branch_name.endswith("/") or ".." in branch_name:
        raise HTTPException(status_code=400, detail="Invalid new branch name.")

    base_sha = _github_branch_commit_sha(owner, repo, base_branch, token)
    payload = {"ref": f"refs/heads/{branch_name}", "sha": base_sha}
    _github_send("POST", f"https://api.github.com/repos/{owner}/{repo}/git/refs", token, payload)
    return branch_name


def _github_file_sha(owner: str, repo: str, branch: str, path: str, token: str | None) -> str | None:
    encoded_path = "/".join(quote(part, safe="") for part in path.split("/"))
    encoded_branch = quote(branch, safe="")
    try:
        data = _github_request(f"https://api.github.com/repos/{owner}/{repo}/contents/{encoded_path}?ref={encoded_branch}", token)
        return data.get("sha")
    except HTTPException as exc:
        if "not found" in str(exc.detail).lower():
            return None
        raise


import uuid

def _upload_github_file(payload: GitUploadRequest) -> GitUploadResponse:
    parsed = _parse_github_repo(payload.repo_url)
    if parsed is None:
        raise HTTPException(status_code=400, detail="Upload is supported for GitHub repository URLs only.")

    token = (payload.token or os.getenv("GITHUB_TOKEN") or "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="Git access token is required to upload to GitHub.")

    owner, repo = parsed
    mode = (payload.mode or "existing").strip().lower()
    
    # We must NEVER commit directly to the target branch if we want a PR workflow.
    # We create a temporary branch, commit there, and PR back to the target branch.
    requested_target = (payload.branch or "").strip()
    
    if mode == "create":
        # They want to create a brand new branch, and PR it into base_branch
        base_branch = (payload.base_branch or requested_target or "").strip()
        if not base_branch:
            base_branch = _github_default_ref(owner, repo, token)
        # target_branch here is the branch we commit to
        target_branch = _create_github_branch(owner, repo, base_branch, payload.new_branch or "", token)
        pr_target_branch = base_branch
    else:
        # They selected an existing branch. We don't commit directly!
        if not requested_target:
            raise HTTPException(status_code=400, detail="Target branch is required.")
        
        pr_target_branch = requested_target
        temp_branch_name = f"pr-update-{uuid.uuid4().hex[:8]}"
        target_branch = _create_github_branch(owner, repo, pr_target_branch, temp_branch_name, token)

    target_path = _safe_git_path(payload.path)
    encoded_path = "/".join(quote(part, safe="") for part in target_path.split("/"))
    existing_sha = _github_file_sha(owner, repo, target_branch, target_path, token)
    content_b64 = base64.b64encode(payload.content.encode("utf-8")).decode("ascii")

    put_payload = {
        "message": (payload.message or f"Upload translated SQL to {target_path}").strip(),
        "content": content_b64,
        "branch": target_branch,
    }
    if existing_sha:
        put_payload["sha"] = existing_sha

    data = _github_send("PUT", f"https://api.github.com/repos/{owner}/{repo}/contents/{encoded_path}", token, put_payload)
    commit = data.get("commit") or {}
    content = data.get("content") or {}
    
    pr_url = None
    reviewers_assigned = None
    
    if target_branch != pr_target_branch:
        pr_payload = {
            "title": put_payload["message"],
            "head": target_branch,
            "base": pr_target_branch,
            "body": "Automated migration using the Query Converter Tool.",
        }
        try:
            # First check if an open PR already exists for this branch combo
            existing_prs = _github_request(
                f"https://api.github.com/repos/{owner}/{repo}/pulls?head={owner}:{target_branch}&base={pr_target_branch}&state=open",
                token
            )
            
            pr_data = existing_prs[0] if isinstance(existing_prs, list) and len(existing_prs) > 0 else None
            
            if not pr_data:
                # If no PR exists, create it
                pr_data = _github_send("POST", f"https://api.github.com/repos/{owner}/{repo}/pulls", token, pr_payload)
                
            pr_url = pr_data.get("html_url")
            pr_number = pr_data.get("number")
            
            if payload.reviewers and pr_number:
                reviewers_payload = {
                    "reviewers": payload.reviewers
                }
                _github_send("POST", f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/requested_reviewers", token, reviewers_payload)
                reviewers_assigned = payload.reviewers

        except HTTPException as http_exc:
            # We want to show github API errors to the user
            raise HTTPException(
                status_code=400,
                detail=f"Committed successfully, but failed to create Pull Request or assign reviewers: {http_exc.detail}"
            )
        except Exception as e:
            raise HTTPException(
                status_code=400,
                detail=f"Committed successfully, but failed to create Pull Request: {str(e)}"
            )

    return GitUploadResponse(
        branch=target_branch,
        path=target_path,
        commit_sha=commit.get("sha"),
        html_url=content.get("html_url"),
        pr_url=pr_url,
        reviewers_assigned=reviewers_assigned,
    )


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


def _list_repo_files(repo_url: str, ref: str | None, token: str | None = None) -> tuple[list[str], str]:
    github_files = _list_github_files(repo_url, ref, token)
    if github_files is not None:
        return github_files

    repo_dir, resolved_ref = _clone_repo(repo_url, ref)
    try:
        raw = _run_git(["ls-tree", "-r", "--name-only", "HEAD"], cwd=repo_dir)
        files = [line.strip() for line in raw.splitlines() if line.strip()]
        sql_files = [file for file in files if Path(file).suffix.lower() in SQL_FILE_SUFFIXES]
        return sorted(sql_files or files), resolved_ref
    finally:
        shutil.rmtree(repo_dir, ignore_errors=True)


def _read_repo_file(repo_url: str, ref: str | None, path: str, token: str | None = None) -> tuple[str, str]:
    github_file = _read_github_file(repo_url, ref, path, token)
    if github_file is not None:
        return github_file

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
    files, resolved_ref = _list_repo_files(payload.repo_url, payload.ref, payload.token)
    return GitFilesResponse(files=files, ref=resolved_ref)


@router.post("/git/branches", response_model=GitBranchesResponse)
def git_branches(payload: GitBranchesRequest) -> GitBranchesResponse:
    result = _list_github_branches(payload.repo_url, payload.token)
    if result is None:
        raise HTTPException(status_code=400, detail="Branch dropdown is supported for GitHub repository URLs only.")
    branches, default_branch = result
    return GitBranchesResponse(branches=branches, default_branch=default_branch)


@router.post("/git/file", response_model=GitFileResponse)
def git_file(payload: GitFileRequest) -> GitFileResponse:
    content, resolved_ref = _read_repo_file(payload.repo_url, payload.ref, payload.path, payload.token)
    return GitFileResponse(path=payload.path, content=content, ref=resolved_ref)


def _list_github_collaborators(repo_url: str, token: str | None = None) -> list[str]:
    parsed = _parse_github_repo(repo_url)
    if parsed is None:
        return []

    owner, repo = parsed
    collaborators: list[str] = []
    page = 1
    while page <= 10:
        data = _github_request(f"https://api.github.com/repos/{owner}/{repo}/collaborators?per_page=100&page={page}", token)
        if not isinstance(data, list) or not data:
            break
        collaborators.extend(str(item.get("login", "")) for item in data if item.get("login"))
        if len(data) < 100:
            break
        page += 1

    return collaborators


@router.post("/git/collaborators", response_model=GitCollaboratorsResponse)
def git_collaborators(payload: GitCollaboratorsRequest) -> GitCollaboratorsResponse:
    collaborators = _list_github_collaborators(payload.repo_url, payload.token)
    return GitCollaboratorsResponse(collaborators=collaborators)


@router.post("/git/upload", response_model=GitUploadResponse)
def git_upload(payload: GitUploadRequest) -> GitUploadResponse:
    return _upload_github_file(payload)


@router.get("/cache/stats")
def cache_stats() -> dict:
    components = service.components
    return {
        "persistent": components["cache"].stats(),
        "expression": components["expr_cache"].stats(),
    }


@router.get("/session-stats", response_model=QueryStatsResponse)
def session_stats(session_id: str | None = None, source_engine: str | None = None) -> QueryStatsResponse:
    return QueryStatsResponse(session_id=session_id, stats=get_query_stats(session_id or "", source_engine=source_engine))


@router.get("/query-history")
def query_history(source_engine: str = "bigquery") -> dict:
    return {"queries": supabase_store.list_query_history(source_engine=source_engine)}


@router.patch("/query-history/{query_id}")
def update_query_history(query_id: str, payload: dict) -> dict:
    source_engine = payload.pop("source_engine", None)
    supabase_store.update_query_history(query_id, source_engine, payload)
    return {"status": "ok"}


@router.post("/cache/clear", response_model=CacheClearResponse)
def clear_cache() -> CacheClearResponse:
    components = service.components
    cleared = components["cache"].clear_all()
    components["expr_cache"].clear_all()
    return CacheClearResponse(cleared_persistent_entries=cleared, expression_cache_cleared=True)


@router.post("/translate", response_model=TranslateResponse)
def translate(payload: TranslateRequest) -> TranslateResponse:
    if not payload.bq_sql.strip():
        source_label = {"snowflake": "Snowflake", "trino": "Trino"}.get(payload.source_engine.lower(), "BigQuery")
        raise HTTPException(status_code=400, detail=f"Please enter a {source_label} SQL query.")

    _enforce_source_engine_match(payload.source_engine, payload.bq_sql)

    use_llm = payload.mode == "Auto (deterministic -> LLM migration -> validation)"
    complexity = complexity_analyzer.analyze(payload.bq_sql)
    translated_sql, explanation, stats, final_error = service.run_pipeline(
        bq_sql=payload.bq_sql,
        model=payload.model,
        provider=payload.provider,
        api_key=payload.api_key,
        source_engine=payload.source_engine,
        force_llm=False,
        use_llm=use_llm,
    )
    stats["complexity"] = complexity

    validator = service.components["validator"]
    validation = validator.validate(translated_sql)
    suggestions = validator.suggest_fixes(validation) if not validation.is_valid else []
    execution = None

    # Measure source latency by running source SQL on the active source session
    source_latency_ms = None
    if payload.session_id and payload.bq_sql.strip():
        try:
            source_started = time.perf_counter()
            _rows_from_source_session(payload.source_engine, payload.bq_sql, payload.session_id)
            source_latency_ms = int((time.perf_counter() - source_started) * 1000)
        except Exception:
            pass

    # Measure target latency by running original SQL on Databricks
    target_latency_ms = None
    if payload.run_in_databricks:
        if payload.databricks is None:
            raise HTTPException(status_code=400, detail="Databricks config is required when run_in_databricks is true.")
        try:
            target_started = time.perf_counter()
            execution = service.execute_databricks_sql(translated_sql, payload.databricks.model_dump())
            target_latency_ms = int((time.perf_counter() - target_started) * 1000)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Databricks execution failed: {exc}") from exc
    final_query_id = payload.query_id or (supabase_store.make_query_id() if translated_sql and not final_error else None)
    final_query_name = (payload.query_name or "").strip() or "Untitled Query"

    update_query_stats(
        payload.session_id or "",
        migrated=bool(translated_sql and not final_error),
        validated=False,
        complexity_level=complexity.get("complexity_level"),
        source_engine=payload.source_engine,
    )

    if final_query_id:
        supabase_store.upsert_query_history({
            "query_id": final_query_id,
            "query_name": final_query_name,
            "source_engine": payload.source_engine,
            "run_by": payload.run_by,
            "last_ran_ts": datetime.utcnow(),
            "source_latency_ms": source_latency_ms,
            "target_latency_ms": target_latency_ms,
            "migration_mode": payload.mode,
            "validation_status": "NOT RUN",
            "pushed_to_git": False,
            "source_sql": payload.bq_sql,
            "translated_sql": translated_sql,
            "details": {
                "provider": payload.provider,
                "model": payload.model,
                "input_mode": payload.input_mode or "manual",
                "validation": {
                    "is_valid": validation.is_valid,
                    "error_message": validation.error_message,
                    "error_type": validation.error_type,
                    "line_number": validation.line_number,
                },
                "complexity": complexity,
                "explanation": explanation,
                "suggestions": suggestions,
                "final_error": final_error,
            },
        })

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
        query_id=final_query_id,
        query_name=final_query_name,
        source_latency_ms=source_latency_ms,
        target_latency_ms=target_latency_ms,
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

    def _timed_databricks_execute(sql: str) -> dict:
        started = time.perf_counter()
        execution = service.execute_databricks_sql(sql, dbx_config)
        if isinstance(execution, dict):
            execution["execution_time_ms"] = int((time.perf_counter() - started) * 1000)
        return execution

    try:
        databricks_execution = _timed_databricks_execute(payload.sql)
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
        repair_messages: list[str] = []
        current_sql = payload.sql.strip()
        current_error = dbx_error
        max_repair_attempts = 3

        for attempt in range(1, max_repair_attempts + 1):
            if not _should_forward_databricks_error_to_llm(current_error):
                break

            repaired_sql_candidate, repair_error = _repair_databricks_sql(
                current_sql,
                current_error,
                payload.provider,
                payload.model,
                payload.api_key,
            )
            if repair_error:
                repair_messages.append(repair_error)
                break

            if not repaired_sql_candidate:
                repair_messages.append("LLM repair did not return SQL.")
                break

            repaired_sql_candidate = repaired_sql_candidate.strip()
            if repaired_sql_candidate == current_sql:
                repair_messages.append("LLM repair returned the same SQL, so no further retry was made.")
                break

            current_sql = repaired_sql_candidate
            repaired_execution = _timed_databricks_execute(current_sql)
            final_execution["databricks"] = repaired_execution
            final_execution["repaired_sql"] = current_sql

            repaired_error = repaired_execution.get("error") if isinstance(repaired_execution, dict) else None
            if not repaired_error:
                repair_messages.append("Databricks returned an error, so the SQL was repaired with LLM and run again.")
                break

            repair_messages.append(f"LLM repair attempt {attempt} still returned a Databricks error: {repaired_error}")
            current_error = repaired_error

        if repair_messages:
            final_execution["repair_message"] = " ".join(repair_messages)
    elif dbx_error:
        final_execution["repair_message"] = "Databricks object/catalog/schema/table error returned. LLM repair was skipped."

    if source_execution is None:
        final_execution.pop("source")

    # Update database with execution latencies if available
    if getattr(payload, "query_id", None):
        source_lat = source_execution.get("execution_time_ms") if source_execution else None
        target_lat = final_execution["databricks"].get("execution_time_ms") if final_execution.get("databricks") else None
        
        updates = {}
        if source_lat is not None:
            updates["source_latency_ms"] = source_lat
        if target_lat is not None:
            updates["target_latency_ms"] = target_lat
        updates["last_ran_ts"] = datetime.utcnow()
            
        if updates:
            engines: list[str] = []
            if payload.source_engine:
                engines.append(payload.source_engine)
            for engine in ("bigquery", "snowflake", "trino"):
                if engine not in engines:
                    engines.append(engine)
            for engine in engines:
                supabase_store.update_query_history(
                    payload.query_id,
                    engine,
                    updates,
                )

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
    query_name: str = Form(""),
    run_by: str = Form(""),
    run_in_databricks: bool = Form(False),
    databricks_host: str = Form(""),
    databricks_token: str = Form(""),
    databricks_warehouse_id: str = Form(""),
    databricks_catalog: str = Form(""),
    databricks_schema: str = Form(""),
    databricks_timeout_seconds: int = Form(90),
    databricks_max_rows: int = Form(200),
    session_id: str = Form(""),
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
        split_queries = _split_sql_queries(cell_value)
        for query_index, bq_sql in enumerate(split_queries):
            try:
                _enforce_source_engine_match(source_engine, bq_sql)
                started = time.perf_counter()
                complexity = complexity_analyzer.analyze(bq_sql)
                translated_sql, explanation, stats, final_error = service.run_pipeline(
                    bq_sql=bq_sql,
                    model=model,
                    provider=provider,
                    api_key=api_key or None,
                    source_engine=source_engine,
                    force_llm=False,
                    use_llm=use_llm,
                )
                stats["complexity"] = complexity
                validation = validator.validate(translated_sql)
                suggestions = validator.suggest_fixes(validation) if not validation.is_valid else []
                execution = None
                final_query_id = supabase_store.make_query_id() if translated_sql and not final_error else None
                base_query_name = (query_name or "").strip() or (Path(file.filename).stem if file.filename else "CSV Query")
                final_query_name = f"{base_query_name} - Row {row_index + 1}"
                if len(split_queries) > 1:
                    final_query_name = f"{final_query_name} Query {query_index + 1}"

                if databricks_cfg is not None:
                    try:
                        execution = service.execute_databricks_sql(translated_sql, databricks_cfg)
                    except Exception as exc:
                        execution = {"status": "FAILED", "error": str(exc)}

                target_latency_ms = int((time.perf_counter() - started) * 1000)
                source_latency_ms = None
                if session_id and bq_sql.strip():
                    try:
                        source_started = time.perf_counter()
                        _rows_from_source_session(source_engine, bq_sql, session_id)
                        source_latency_ms = int((time.perf_counter() - source_started) * 1000)
                    except Exception:
                        pass

                update_query_stats(
                    session_id.strip(),
                    migrated=bool(translated_sql and not final_error),
                    validated=False,
                    complexity_level=complexity.get("complexity_level"),
                    source_engine=source_engine,
                )

                if final_query_id:
                    supabase_store.upsert_query_history({
                        "query_id": final_query_id,
                        "query_name": final_query_name,
                        "source_engine": source_engine,
                        "run_by": run_by,
                        "last_ran_ts": datetime.utcnow(),
                        "source_latency_ms": source_latency_ms,
                        "target_latency_ms": target_latency_ms,
                        "migration_mode": mode,
                        "validation_status": "NOT RUN",
                        "pushed_to_git": False,
                        "source_sql": bq_sql,
                        "translated_sql": translated_sql,
                        "details": {
                            "provider": provider,
                            "model": model,
                            "input_mode": "csv",
                            "csv_file": file.filename,
                            "csv_row": row_index + 1,
                            "csv_query": query_index + 1,
                            "validation": {
                                "is_valid": validation.is_valid,
                                "error_message": validation.error_message,
                                "error_type": validation.error_type,
                                "line_number": validation.line_number,
                            },
                            "complexity": complexity,
                            "explanation": explanation,
                            "suggestions": suggestions,
                            "final_error": final_error,
                        },
                    })

                translated_parts.append(translated_sql)
                results.append(CsvQueryResult(
                    row_index=row_index,
                    query_index=query_index,
                    query_id=final_query_id,
                    query_name=final_query_name,
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
