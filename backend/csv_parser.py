from __future__ import annotations

import csv
import io


def _parse_bool(val: str | None) -> bool:
    if val is None:
        return False
    s = str(val).strip().lower()
    return s in {"1", "true", "yes", "y"}


def _parse_metrics(val: str | None) -> list[str]:
    if not val:
        return []
    raw = str(val).strip()
    if not raw:
        return []

    parts = [p.strip().lower() for p in raw.replace(";", ",").split(",") if p.strip()]
    if "all" in parts:
        return ["row_count", "schema", "numeric", "hash"]
    return parts


def parse_validations_csv(csv_bytes: bytes) -> list[dict]:
    text = csv_bytes.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))

    required = {
        "source_table",
        "target_table",
        "validation_type",
        "metrics",
        "case_sensitive",
        "include_timestamp",
    }

    if not reader.fieldnames:
        raise ValueError("CSV is missing header row")

    header = {h.strip() for h in reader.fieldnames}
    missing = required - header
    if missing:
        raise ValueError(f"CSV missing required columns: {sorted(missing)}")

    rows: list[dict] = []
    for i, row in enumerate(reader, start=2):
        src = (row.get("source_table") or "").strip()
        tgt = (row.get("target_table") or "").strip()
        vtype = (row.get("validation_type") or "").strip().lower()

        if vtype not in {"shallow", "deep"}:
            raise ValueError(f"Row {i}: validation_type must be shallow or deep")

        metrics = _parse_metrics(row.get("metrics"))
        case_sensitive = _parse_bool(row.get("case_sensitive"))
        include_timestamp = _parse_bool(row.get("include_timestamp"))

        rows.append(
            {
                "source_table": src,
                "target_table": tgt,
                "validation_type": vtype,
                "metrics": metrics,
                "case_sensitive": case_sensitive,
                "include_timestamp": include_timestamp,
            }
        )

    return rows
