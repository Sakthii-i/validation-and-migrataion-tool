from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage
from typing import Any

from validation_tool.backend.auth_config import ADMIN_EMAIL, ADMIN_USERNAME
from validation_tool.backend.supabase_auth_store import get_user

logger = logging.getLogger(__name__)


CHECK_LABELS = {
    "row_count": "Row Count Validation",
    "schema_check": "Schema Validation",
    "numeric_check": "Numeric Statistics Validation",
    "hash_validation": "Row Hash Validation",
}

def _selected_checks(result: dict[str, Any]) -> list[tuple[str, str]]:
    checks = []
    for key, label in CHECK_LABELS.items():
        status = result.get(key)
        if status is None or str(status).strip().upper() in {"", "N/A", "NONE"}:
            continue
        checks.append((label, str(status).strip().upper()))
    return checks


def _failed_checks(result: dict[str, Any]) -> list[str]:
    return [label for label, status in _selected_checks(result) if status == "FAIL"]


def validation_failed(result: dict[str, Any]) -> bool:
    return bool(_failed_checks(result))


def _details_summary(result: dict[str, Any]) -> list[str]:
    details = result.get("details") or {}
    lines = []

    row_count = details.get("row_count") or {}
    if isinstance(row_count, dict) and row_count:
        lines.append(
            "Row count: "
            f"source={row_count.get('source_count', 'N/A')}, "
            f"target={row_count.get('target_count', 'N/A')}, "
            f"difference={row_count.get('difference', 'N/A')}"
        )

    schema = details.get("schema") or {}
    if isinstance(schema, dict) and schema:
        lines.append(
            "Schema: "
            f"total_columns={schema.get('total_columns', 'N/A')}, "
            f"mismatch_count={schema.get('mismatch_count', 'N/A')}"
        )

    numeric = details.get("numeric") or {}
    if isinstance(numeric, dict) and numeric.get("error"):
        lines.append(f"Numeric error: {numeric.get('error')}")

    row_hash = details.get("row_hash") or {}
    if isinstance(row_hash, dict) and row_hash:
        if row_hash.get("error"):
            lines.append(f"Row hash error: {row_hash.get('error')}")
        elif any(k in row_hash for k in ("source_not_in_target_count", "target_not_in_source_count")):
            lines.append(
                "Row hash: "
                f"source_not_in_target={row_hash.get('source_not_in_target_count', 'N/A')}, "
                f"target_not_in_source={row_hash.get('target_not_in_source_count', 'N/A')}, "
                f"matched={row_hash.get('matched_hash_count', 'N/A')}"
            )

    return lines


def send_validation_failure_email(result: dict[str, Any]) -> dict[str, Any]:
    if not validation_failed(result):
        return {"attempted": False, "sent": False, "recipient": None, "message": "Validation did not fail."}

    username = str(result.get("run_by") or "").strip()
    if not username:
        logger.info("Skipping validation failure email: run_by is empty")
        return {"attempted": False, "sent": False, "recipient": None, "message": "No logged-in user was attached to this validation run."}

    if username == ADMIN_USERNAME:
        recipient = ADMIN_EMAIL
        smtp_password = str(os.getenv("ADMIN_SMTP_PASSWORD") or "")
    else:
        user = get_user(None, username)
        if not user:
            logger.info("Skipping validation failure email: user %s not found", username)
            return {"attempted": False, "sent": False, "recipient": None, "message": f"User '{username}' was not found in auth_credentials."}

        recipient = str(user.get("email") or "").strip()
        smtp_password = str(user.get("smtp_password") or "")

    if not all([recipient, smtp_password]):
        logger.info("Skipping validation failure email for %s: email or SMTP app password missing", username)
        return {
            "attempted": False,
            "sent": False,
            "recipient": recipient or None,
            "message": "User email or SMTP app password is missing.",
        }

    source_engine = str(result.get("source_engine") or "source").strip().upper()
    source_table = result.get("source_table_name") or result.get("src_table") or result.get("src_table_name")
    target_table = result.get("target_table_name") or result.get("tgt_table") or result.get("tgt_table_name")
    failed = _failed_checks(result)
    selected = _selected_checks(result)

    body_lines = [
        "Validation failed.",
        "",
        f"Validation ID: {result.get('validation_id', 'N/A')}",
        f"Validation environment: {source_engine} -> DATABRICKS",
        f"Source table: {source_table or 'N/A'}",
        f"Target table: {target_table or 'N/A'}",
        f"Validation type: {result.get('validation_type', 'N/A')}",
        "",
        "Selected validations:",
        *[f"- {label}: {status}" for label, status in selected],
        "",
        "Failed validations:",
        *[f"- {label}" for label in failed],
    ]

    detail_lines = _details_summary(result)
    if detail_lines:
        body_lines.extend(["", "Failure details:", *[f"- {line}" for line in detail_lines]])

    message = EmailMessage()
    message["Subject"] = f"Validation failed: {source_table or result.get('validation_id', '')}"
    message["From"] = recipient
    message["To"] = recipient
    message.set_content("\n".join(body_lines))

    host = (os.getenv("SMTP_HOST") or "smtp.gmail.com").strip()
    port = int(os.getenv("SMTP_PORT") or "587")
    use_tls = (os.getenv("SMTP_USE_TLS") or "true").strip().lower() not in {"0", "false", "no"}

    try:
        with smtplib.SMTP(host, port, timeout=30) as smtp:
            if use_tls:
                smtp.starttls()
            smtp.login(recipient, smtp_password)
            smtp.send_message(message)
        logger.info("Sent validation failure email to %s for %s", recipient, result.get("validation_id"))
        return {"attempted": True, "sent": True, "recipient": recipient, "message": "Validation failed. Validation report has been sent to the respective mail."}
    except Exception as e:
        logger.error("Failed to send validation failure email for %s: %s", result.get("validation_id"), e)
        return {"attempted": True, "sent": False, "recipient": recipient, "message": f"Validation failed, but email sending failed: {e}"}
