# Validation API (FastAPI + Redis + RQ)

This repo now includes an async API to submit validation jobs and poll results.

## Services

- `frontend` (React): UI for validation tool
- `api` (FastAPI): session + validation endpoints
- `worker` (RQ): executes validation tasks
- `redis`: session store + queue broker
- `postgres`: local persistence for validation jobs/results (docker-compose)

## Environment variables

- `VALIDATION_API_KEYS`: comma-separated list of API keys (required)
- `REDIS_URL`: Redis connection string (default `redis://localhost:6379/0`)
- `VALIDATION_SESSION_TTL_SECONDS`: session TTL in seconds (default `3600`)
- `VALIDATION_MAX_CONCURRENT_JOBS_PER_SESSION`: max simultaneous RUNNING jobs per session (default `2`)
- `VALIDATION_SESSION_CONCURRENCY_SLOT_TTL_SECONDS`: safety TTL for session concurrency slots (default `1800`)
- `WORKER_PROCESSES`: number of worker processes to run in `python -m validation_tool.worker.run_worker` (default `1`)
- `SUPABASE_URL`: your Supabase project URL (required for Supabase result storage)
- `SUPABASE_PUBLISHABLE_KEY` or `SUPABASE_ANON_KEY`: Supabase publishable/anon API key
- `SUPABASE_RESULTS_TABLE`: table name used by API result persistence (default `validation_results`)

## Supabase result storage

When `SUPABASE_URL` and key are configured, React `/api/validate/run` results are upserted to Supabase and `/api/results` + `/api/results/{validation_id}` read from Supabase.

Expected Supabase table columns:

- `validation_id` (text, primary key)
- `validation_ts` (timestamptz)
- `validation_type` (text)
- `src_table_name` (text)
- `tgt_table_name` (text)
- `row_count` (text)
- `schema_check` (text)
- `numeric_check` (text)
- `hash_validation` (text)
- `details` (jsonb)

Quick setup (from scratch):

1. Create a Supabase project.
2. In Supabase SQL Editor, run `validation_tool/tools/supabase/setup_validation_results.sql`.
3. Copy `.env.example` to `.env` and set:
  - `SUPABASE_URL=https://<your-project-ref>.supabase.co`
  - `SUPABASE_PUBLISHABLE_KEY=sb_publishable_HzIXmmpx_rJP4K8cNMLhRg_oQY3Yt_b`
4. Restart API container/service.
5. Run a new validation and verify `/api/results` returns records.

## Run with Docker Compose

From repo root:

- `set VALIDATION_API_KEYS=dev-key` (Windows)
- `docker compose -f validation_tool/docker-compose.yml up --build`

UI: http://localhost:3000
API: http://localhost:8000

On Windows, you can also run `validation_tool/start_tool.ps1`. It starts the Docker stack in the background and prints the UI/API links after the services are ready, so the localhost links remain visible at the end.

Postgres (local): `localhost:5433` (container runs on 5432; db/user/password all `validation`)

Note: `0.0.0.0` is a bind address used by servers. In a browser, use `http://localhost:8000` (or the machine's IP) rather than `http://0.0.0.0:8000`.

## API usage

### Create session

`POST /sessions` with header `x-api-key: <key>`.

**For BigQuery (`source_engine = "bigquery"`):**
- Provide full BigQuery credentials in `source` field
- Provide full Databricks credentials in `target` field
- `credential_password` can be empty (not used)

**For Snowflake (`source_engine = "snowflake"`):**
- `credential_password` is required to unlock `api/credential.txt`
- Both Snowflake and Databricks credentials are loaded from the locked file

Body JSON example (BigQuery):

```json
{
  "source_engine": "bigquery",
  "source": {
    "project_id": "my-project",
    "dataset_location": "US",
    "service_account_key_path": "C:/keys/sa.json"
  },
  "target": {
    "server_hostname": "adb-...azuredatabricks.net",
    "http_path": "/sql/1.0/warehouses/...",
    "access_token": "..."
  }
}
```

`api/credential.txt` format (`key=value`):

**IMPORTANT:** The `credential.txt` file must be encrypted. To encrypt it:

1. Create plain text credential file with this format:
```txt
snowflake.account=...
snowflake.user=...
snowflake.password=...
snowflake.warehouse=...
snowflake.role=...
databricks.server_hostname=...
databricks.http_path=...
databricks.access_token=...
```

2. Run the encryption utility:
```bash
cd api
python encrypt_credentials.py
```

3. Enter your credential password when prompted (must match the hash in `api/auth.py`)

The file will be encrypted in-place. The encrypted file can only be decrypted with the correct password.

### Submit validations (CSV)

`POST /validations` as `multipart/form-data` with:
- `session_id`: from `/sessions`
- `file`: CSV file

Returns `validation_ids` immediately; worker computes in background.

### Get a validation

`GET /validations/{validation_id}` returns job state and (when available) the stored result.
