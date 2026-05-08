import io
import json
import logging
import pandas as pd
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Depends

# Correct relative import: go up one level to import validation_core
from ..validation_core import (
    parse_table_path,
    validate_row_count,
    validate_schema,
    validate_numeric,
    validate_row_hash,
    generate_validation_record,
    insert_validation_result,
    bool_to_status,
    create_bigquery_connection,
    create_snowflake_connection,
    create_databricks_connection,
)

# Use your existing auth function (same directory)
from .auth import require_api_key

router = APIRouter()
logger = logging.getLogger(__name__)
@router.get("/results/{validation_id}")
async def get_validation_result(validation_id: str, _ = Depends(require_api_key)):
    from validation_tool.backend import supabase_store
    try:
        row = supabase_store.get_result_by_id(validation_id)
        if not row:
            raise HTTPException(status_code=404, detail="Validation ID not found")
        return row
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get validation result: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve validation result")
@router.post("/validate")
async def validate(
    credentials: str = Form(...),
    file: UploadFile = File(...),
    _ = Depends(require_api_key)  # authentication only
):
    """
    Accepts credentials JSON and a CSV file, runs validations for each row,
    and returns a list of Postgres validation_ids.
    """
    # 1. Parse credentials
    try:
        creds = json.loads(credentials)
        source_engine = creds["source_engine"].lower()
        source_config = creds["source"]
        target_config = creds["target"]
    except (KeyError, json.JSONDecodeError) as e:
        raise HTTPException(status_code=400, detail=f"Invalid credentials JSON: {e}")

    # 2. Establish connections
    try:
        if source_engine == "bigquery":
            if "service_account_key" not in source_config:
                raise ValueError("Missing service_account_key for BigQuery")
            source_conn = create_bigquery_connection(source_config["service_account_key"])
        elif source_engine == "snowflake":
            required = {"account", "user", "password", "warehouse"}
            if not required.issubset(source_config):
                raise ValueError(f"Missing Snowflake fields: {required - set(source_config)}")
            source_conn = create_snowflake_connection(**source_config)
        else:
            raise ValueError(f"Unsupported source engine: {source_engine}")

        target_required = {"server_hostname", "http_path", "access_token"}
        if not target_required.issubset(target_config):
            raise ValueError(f"Missing Databricks fields: {target_required - set(target_config)}")
        target_conn = create_databricks_connection(**target_config)

    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Connection failed: {e}")

    # 3. Read CSV
    contents = await file.read()
    try:
        df = pd.read_csv(io.BytesIO(contents))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not parse CSV: {e}")

    # 4. Validate CSV columns
    required_cols = {"source_table", "target_table", "validation_type", "metrics", "case_sensitive", "include_timestamp"}
    if not required_cols.issubset(df.columns):
        missing = required_cols - set(df.columns)
        raise HTTPException(status_code=400, detail=f"Missing columns: {missing}")

    validation_ids = []
    errors = []

    # 5. Process each row
    for idx, row in df.iterrows():
        try:
            src_cat, src_sch, src_tbl = parse_table_path(row["source_table"])
            tgt_cat, tgt_sch, tgt_tbl = parse_table_path(row["target_table"])
            if not all([src_cat, src_sch, src_tbl, tgt_cat, tgt_sch, tgt_tbl]):
                raise ValueError(f"Invalid table path at row {idx}")

            src = {"catalog": src_cat, "schema": src_sch, "table": src_tbl}
            tgt = {"catalog": tgt_cat, "schema": tgt_sch, "table": tgt_tbl}

            validation_type = str(row["validation_type"]).strip().lower()
            metrics_str = str(row["metrics"]) if pd.notna(row["metrics"]) else ""
            metrics_list = [m.strip().lower() for m in metrics_str.split(",") if m.strip()]
            case_sensitive = str(row["case_sensitive"]).strip().lower() in ["yes", "true", "1"]
            include_timestamp = str(row["include_timestamp"]).strip().lower() in ["yes", "true", "1"]

            if validation_type == "shallow":
                selected = {"row_count": True, "schema": True, "numeric": False, "hash": False}
            else:
                selected = {
                    "row_count": "row_count" in metrics_list,
                    "schema": "schema" in metrics_list,
                    "numeric": "numeric" in metrics_list,
                    "hash": "hash" in metrics_list,
                }
                if "all" in metrics_list:
                    selected = {k: True for k in selected}

            results = {}
            if selected["row_count"]:
                results["row_count"] = validate_row_count(source_engine, source_conn, target_conn, src, tgt)
            if selected["schema"]:
                results["schema"] = validate_schema(source_engine, source_conn, target_conn, src, tgt, case_sensitive)
            if selected["numeric"]:
                results["numeric"] = validate_numeric(source_engine, source_conn, target_conn, src, tgt)
            if selected["hash"]:
                results["hash"] = validate_row_hash(source_engine, source_conn, target_conn, src, tgt, include_timestamp)

            record = generate_validation_record(
                validation_type,
                src,
                tgt,
                bool_to_status(results.get("row_count")),
                bool_to_status(results.get("schema")),
                bool_to_status(results.get("numeric")),
                bool_to_status(results.get("hash")),
            )
            vid = insert_validation_result(record)
            validation_ids.append(vid)

        except Exception as e:
            logger.error(f"Row {idx} failed: {e}")
            errors.append({"row": idx, "error": str(e)})

    # 6. Close connections
    try:
        source_conn.close()
        target_conn.close()
    except:
        pass

    return {"validation_ids": validation_ids, "errors": errors}