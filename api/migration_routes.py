from __future__ import annotations

import csv
import io

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from validation_tool.migration.schemas import (
    CacheClearResponse,
    ConfigResponse,
    CsvQueryResult,
    CsvTranslateResponse,
    DatabricksExecuteRequest,
    DatabricksExecuteResponse,
    NormalizeRequest,
    NormalizeResponse,
    TranslateRequest,
    TranslateResponse,
)
from validation_tool.migration.sql_processor import SQLPreprocessor
from validation_tool.migration.translator_service import PROVIDER_MODEL_OPTIONS, TranslatorService

router = APIRouter(prefix="/api/migration")
service = TranslatorService()


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
