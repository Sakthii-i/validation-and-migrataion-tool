import time
import requests
import json

API_BASE = "http://localhost:8000"   # open API at http://localhost:8000/docs
API_KEY = "dev-key"                 # must match VALIDATION_API_KEYS

SESSION_ID = "ThLTLPOptfYtbIGz5pqObDLE8iT3upGw"
CSV_PATH = "D:\\project\\ReconciliationFramework-main\\validation_tool\\validation_template.csv"        # path on your machine

HEADERS = {"x-api-key": API_KEY}

def submit_csv(session_id: str, csv_path: str) -> list[str]:
    with open(csv_path, "rb") as f:
        files = {"file": (csv_path.split("\\")[-1], f, "text/csv")}
        data = {"session_id": session_id}
        r = requests.post(f"{API_BASE}/validations", headers=HEADERS, data=data, files=files, timeout=60)
    r.raise_for_status()
    out = r.json()
    print("submit response:", out)
    return out.get("validation_ids", []) or []

def poll(validation_id: str, timeout_s: int = 900, interval_s: int = 5) -> dict:
    deadline = time.time() + timeout_s
    last = None
    while time.time() < deadline:
        r = requests.get(f"{API_BASE}/validations/{validation_id}", headers=HEADERS, timeout=30)
        r.raise_for_status()
        last = r.json()

        job = last.get("job") or {}
        status = job.get("status")
        print(f"{validation_id}: {status}")

        if status in ("completed", "failed"):
            return last

        time.sleep(interval_s)

    raise TimeoutError(f"Timed out waiting for {validation_id}. Last response: {last}")

if __name__ == "__main__":
    validation_ids = submit_csv(SESSION_ID, CSV_PATH)
    if not validation_ids:
        raise RuntimeError("No validation_ids returned. Check CSV format and session_id.")

    for vid in validation_ids:
        out = poll(vid)
        print(json.dumps(out, indent=2, default=str))