"""Expectation suite có phân biệt cảnh báo và điều kiện dừng pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Tuple

from transform.cleaning_rules import ALLOWED_DOC_IDS, MIN_EFFECTIVE_DATES


@dataclass(frozen=True)
class ExpectationResult:
    name: str
    passed: bool
    severity: str
    detail: str


def _is_iso_date(value: str) -> bool:
    try:
        datetime.strptime(value, "%Y-%m-%d")
        return True
    except (TypeError, ValueError):
        return False


def _is_iso_datetime(value: str) -> bool:
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True
    except (AttributeError, TypeError, ValueError):
        return False


def run_expectations(
    cleaned_rows: List[Dict[str, Any]],
) -> Tuple[List[ExpectationResult], bool]:
    results: List[ExpectationResult] = []

    def add(name: str, passed: bool, severity: str, detail: str) -> None:
        results.append(ExpectationResult(name, passed, severity, detail))

    add("min_one_row", bool(cleaned_rows), "halt", f"cleaned_rows={len(cleaned_rows)}")

    required_fields = ("chunk_id", "doc_id", "chunk_text", "effective_date", "exported_at")
    incomplete = [
        row for row in cleaned_rows
        if any(not str(row.get(field, "")).strip() for field in required_fields)
    ]
    add(
        "required_fields_complete",
        not incomplete,
        "halt",
        f"incomplete_rows={len(incomplete)}",
    )

    present_docs = {str(row.get("doc_id") or "") for row in cleaned_rows}
    unknown_docs = sorted(doc for doc in present_docs if doc not in ALLOWED_DOC_IDS)
    missing_docs = sorted(ALLOWED_DOC_IDS - present_docs)
    add(
        "only_registered_doc_ids",
        not unknown_docs,
        "halt",
        f"unknown_doc_ids={unknown_docs}",
    )
    add(
        "all_required_doc_ids_present",
        not missing_docs,
        "halt",
        f"missing_doc_ids={missing_docs}",
    )

    chunk_ids = [row.get("chunk_id") for row in cleaned_rows]
    add(
        "unique_chunk_id",
        len(chunk_ids) == len(set(chunk_ids)),
        "halt",
        f"duplicate_ids={len(chunk_ids) - len(set(chunk_ids))}",
    )
    content_keys = [
        (row.get("doc_id"), " ".join(str(row.get("chunk_text", "")).lower().split()))
        for row in cleaned_rows
    ]
    add(
        "no_duplicate_content_per_doc",
        len(content_keys) == len(set(content_keys)),
        "halt",
        f"duplicates={len(content_keys) - len(set(content_keys))}",
    )

    stale_refund = [
        row for row in cleaned_rows
        if row.get("doc_id") == "policy_refund_v4"
        and "14 ngày làm việc" in str(row.get("chunk_text", ""))
    ]
    add(
        "refund_no_stale_14d_window",
        not stale_refund,
        "halt",
        f"violations={len(stale_refund)}",
    )
    stale_hr = [
        row for row in cleaned_rows
        if row.get("doc_id") == "hr_leave_policy"
        and "10 ngày phép năm" in str(row.get("chunk_text", "")).lower()
    ]
    add(
        "hr_leave_no_stale_10d_annual",
        not stale_hr,
        "halt",
        f"violations={len(stale_hr)}",
    )

    invalid_dates = [
        row for row in cleaned_rows
        if not _is_iso_date(str(row.get("effective_date", "")))
        or str(row.get("effective_date", "")) < MIN_EFFECTIVE_DATES.get(row.get("doc_id"), "")
    ]
    add(
        "effective_date_current_and_iso",
        not invalid_dates,
        "halt",
        f"violations={len(invalid_dates)}",
    )
    invalid_exports = [
        row for row in cleaned_rows if not _is_iso_datetime(str(row.get("exported_at", "")))
    ]
    add(
        "exported_at_iso",
        not invalid_exports,
        "halt",
        f"violations={len(invalid_exports)}",
    )

    ambiguous = [
        row for row in cleaned_rows
        if str(row.get("chunk_text", "")).lower().startswith("nội dung không rõ ràng:")
    ]
    add(
        "no_ambiguous_content_marker",
        not ambiguous,
        "warn",
        f"ambiguous_rows={len(ambiguous)}",
    )
    short = [row for row in cleaned_rows if len(str(row.get("chunk_text", ""))) < 8]
    add("chunk_min_length_8", not short, "warn", f"short_chunks={len(short)}")

    should_halt = any(not result.passed and result.severity == "halt" for result in results)
    return results, should_halt
