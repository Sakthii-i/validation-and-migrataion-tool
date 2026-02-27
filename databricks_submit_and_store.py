import time
import json
import requests
from datetime import datetime, timezone

# ---------------------------
# Config (edit these)
# ---------------------------
API_BASE = "https://YOUR-NGROK-OR-DEPLOYED-DOMAIN"  # no trailing slash
API_KEY = "dev-key"
SESSION_ID = "YOUR_SESSION_ID"

# CSV file path (Databricks examples)
# - If you uploaded to DBFS using the UI: "/dbfs/FileStore/validations.csv"
# - If it's in mounted storage, use the corresponding /dbfs/... path
CSV_PATH = "/dbfs/FileStore/validations.csv"

# Delta table that will be UPDATED (upsert) on each run
RESULTS_TABLE = "default.validation_api_results"

HEADERS = {"x-api-key": API_KEY}


def poll_one(validation_id: str, timeout_s=900, interval_s=5):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        rr = requests.get(f"{API_BASE}/validations/{validation_id}", headers=HEADERS, timeout=30)
        rr.raise_for_status()
        j = rr.json()

        status = (j.get("job") or {}).get("status")  # queued/running/completed/failed
        if status in ("completed", "failed"):
            return j

        time.sleep(interval_s)

    raise TimeoutError(f"Timed out polling {validation_id}")


def s(v):
    # Normalize values so Spark schema inference never fails
    if v is None:
        return None
    return str(v)


# ---------------------------
# A) Submit CSV from disk
# ---------------------------
with open(CSV_PATH, "rb") as f:
    files = {"file": ("validations.csv", f, "text/csv")}
    data = {"session_id": SESSION_ID}

    r = requests.post(
        f"{API_BASE}/validations",
        headers=HEADERS,
        data=data,
        files=files,
        timeout=60,
    )

r.raise_for_status()
submit_out = r.json()
print("submit:", submit_out)

validation_ids = submit_out["validation_ids"]

# ---------------------------
# B) Poll results
# ---------------------------
results = [poll_one(v) for v in validation_ids]
print(json.dumps(results, indent=2)[:4000])

# ---------------------------
# C) Upsert results into Delta
# ---------------------------
# Intended to run on Databricks where `spark` is available.
from pyspark.sql.types import StructType, StructField, StringType
from delta.tables import DeltaTable

run_ts = datetime.now(timezone.utc).isoformat()

rows = []
for j in results:
    validation_id = j.get("validation_id")
    job = j.get("job") or {}
    result = j.get("result") or {}

    rows.append(
        {
            "validation_id": s(validation_id),
            "job_status": s(job.get("status")),
            "job_created_at": s(job.get("created_at")),
            "job_updated_at": s(job.get("updated_at")),
            "job_error": s(job.get("error")),
            "overall_status": s(result.get("overall_status")),
            "response_json": json.dumps(j, ensure_ascii=False),
            "run_ts": run_ts,
            "api_base": s(API_BASE),
            "session_id": s(SESSION_ID),
        }
    )

schema = StructType(
    [
        StructField("validation_id", StringType(), True),
        StructField("job_status", StringType(), True),
        StructField("job_created_at", StringType(), True),
        StructField("job_updated_at", StringType(), True),
        StructField("job_error", StringType(), True),
        StructField("overall_status", StringType(), True),
        StructField("response_json", StringType(), True),
        StructField("run_ts", StringType(), True),
        StructField("api_base", StringType(), True),
        StructField("session_id", StringType(), True),
    ]
)

if not rows:
    print("No results to write.")
else:
    df = spark.createDataFrame(rows, schema=schema)

    # Create table if missing (schema-only)
    try:
        DeltaTable.forName(spark, RESULTS_TABLE)
    except Exception:
        df.limit(0).write.format("delta").saveAsTable(RESULTS_TABLE)

    dt = DeltaTable.forName(spark, RESULTS_TABLE)

    # Avoid unresolved expressions if target table has extra columns not present in `df`
    set_map = {c: f"s.{c}" for c in df.columns}

    (
        dt.alias("t")
        .merge(df.alias("s"), "t.validation_id = s.validation_id")
        .whenMatchedUpdate(set=set_map)
        .whenNotMatchedInsert(values=set_map)
        .execute()
    )

    print(f"Upserted {df.count()} row(s) into {RESULTS_TABLE}")
