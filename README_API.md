# Validation API (FastAPI + Redis + RQ)

This repo now includes an async API to submit validation jobs and poll results.

## Services

- `ui` (Streamlit): existing UI
- `api` (FastAPI): session + validation endpoints
- `worker` (RQ): executes validation tasks
- `redis`: session store + queue broker
- `postgres`: local persistence for validation jobs/results (docker-compose)

## Environment variables

- `VALIDATION_API_KEYS`: comma-separated list of API keys (required)
- `VALIDATION_API_BASE_URL`: base URL the Streamlit UI uses to create sessions (optional)
- `REDIS_URL`: Redis connection string (default `redis://localhost:6379/0`)
- `VALIDATION_SESSION_TTL_SECONDS`: session TTL in seconds (default `3600`)

## Run with Docker Compose

From repo root:

- `set VALIDATION_API_KEYS=dev-key` (Windows)
- `docker compose -f validation_tool/docker-compose.yml up --build`

UI: http://localhost:8501
API: http://localhost:8000

Postgres (local): `localhost:5433` (container runs on 5432; db/user/password all `validation`)

Note: `0.0.0.0` is a bind address used by servers. In a browser, use `http://localhost:8000` (or the machine's IP) rather than `http://0.0.0.0:8000`.

## API usage

### Create session

`POST /sessions` with header `x-api-key: <key>`.

Body JSON example:

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

### Submit validations (CSV)

`POST /validations` as `multipart/form-data` with:
- `session_id`: from `/sessions`
- `file`: CSV file

Returns `validation_ids` immediately; worker computes in background.

### Get a validation

`GET /validations/{validation_id}` returns job state and (when available) the stored result.
