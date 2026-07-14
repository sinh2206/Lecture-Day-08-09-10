"""Quy tắc làm sạch raw export thành cleaned snapshot và quarantine có lý do."""

from __future__ import annotations

import csv
import hashlib
import os
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

ALLOWED_DOC_IDS = frozenset(
    {
        "policy_refund_v4",
        "sla_p1_2026",
        "it_helpdesk_faq",
        "hr_leave_policy",
        "access_control_sop",
    }
)

# Có thể đổi cutoff qua môi trường để kiểm thử versioning mà không sửa code.
MIN_EFFECTIVE_DATES = {
    "policy_refund_v4": os.getenv("REFUND_POLICY_MIN_DATE", "2026-02-01"),
    "sla_p1_2026": os.getenv("SLA_POLICY_MIN_DATE", "2026-01-15"),
    "it_helpdesk_faq": os.getenv("HELPDESK_FAQ_MIN_DATE", "2026-01-20"),
    "hr_leave_policy": os.getenv("HR_POLICY_MIN_DATE", "2026-01-01"),
    "access_control_sop": os.getenv("ACCESS_SOP_MIN_DATE", "2026-01-01"),
}

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DMY_DATE = re.compile(r"^(\d{2})/(\d{2})/(\d{4})$")
_STALE_HR_ANNUAL = re.compile(r"dưới 3 năm.*10 ngày(?: làm việc)? phép năm", re.IGNORECASE)


def _norm_text(value: str) -> str:
    value = unicodedata.normalize("NFC", value or "")
    return " ".join(value.strip().split()).lower()


def _stable_chunk_id(doc_id: str, chunk_text: str) -> str:
    digest = hashlib.sha256(f"{doc_id}|{_norm_text(chunk_text)}".encode("utf-8")).hexdigest()[:20]
    return f"{doc_id}_{digest}"


def _valid_date(value: str) -> bool:
    try:
        datetime.strptime(value, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def _normalize_effective_date(raw: str) -> Tuple[str, str]:
    value = (raw or "").strip()
    if not value:
        return "", "missing_effective_date"
    if _ISO_DATE.fullmatch(value) and _valid_date(value):
        return value, ""
    match = _DMY_DATE.fullmatch(value)
    if match:
        day, month, year = match.groups()
        normalized = f"{year}-{month}-{day}"
        if _valid_date(normalized):
            return normalized, ""
    return "", "invalid_effective_date"


def _normalize_exported_at(raw: str) -> Tuple[str, str]:
    value = (raw or "").strip()
    if not value:
        return "", "missing_exported_at"
    try:
        parsed = datetime.fromisoformat(value.replace("/", "-").replace("Z", "+00:00"))
        return parsed.isoformat(), ""
    except ValueError:
        return "", "invalid_exported_at"


def _normalize_content(text: str) -> str:
    text = unicodedata.normalize("NFC", text or "").strip()
    text = re.sub(r"^!+\s*", "", text)
    text = re.sub(r"\b(làm việc)(?:\s+làm việc)+\b", r"\1", text, flags=re.IGNORECASE)
    return " ".join(text.split())


def load_raw_csv(path: Path) -> List[Dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return [
            {key: (value or "").strip() for key, value in row.items()}
            for row in csv.DictReader(handle)
        ]


def clean_rows(
    rows: List[Dict[str, str]],
    *,
    apply_refund_window_fix: bool = True,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Áp dụng tuần tự các rule có tác động đo được:
    1. Allowlist đủ 5 nguồn, gồm access_control_sop.
    2. Parse ngày thật và chuẩn hóa exported_at.
    3. Quarantine version cũ theo cutoff cấu hình.
    4. Quarantine marker nội dung mơ hồ và semantic HR 2025 bị gắn ngày mới.
    5. Sửa stale refund 14 -> 7 ngày và lỗi lặp "làm việc".
    6. Dedupe sau normalize/fix để không publish hai vector tương đương.
    """
    cleaned: List[Dict[str, Any]] = []
    quarantine: List[Dict[str, Any]] = []
    seen: set[Tuple[str, str]] = set()

    def reject(raw: Dict[str, str], reason: str, **extra: Any) -> None:
        quarantine.append({**raw, **extra, "reason": reason})

    for raw in rows:
        doc_id = (raw.get("doc_id") or "").strip().lower()
        if doc_id not in ALLOWED_DOC_IDS:
            reject(raw, "unknown_doc_id")
            continue

        effective_date, date_error = _normalize_effective_date(raw.get("effective_date", ""))
        if date_error:
            reject(raw, date_error, effective_date_raw=raw.get("effective_date", ""))
            continue
        exported_at, export_error = _normalize_exported_at(raw.get("exported_at", ""))
        if export_error:
            reject(raw, export_error, exported_at_raw=raw.get("exported_at", ""))
            continue

        text = _normalize_content(raw.get("chunk_text", ""))
        if not text:
            reject(raw, "missing_chunk_text")
            continue
        if text.lower().startswith("nội dung không rõ ràng:"):
            reject(raw, "ambiguous_content_marker")
            continue
        if effective_date < MIN_EFFECTIVE_DATES[doc_id]:
            reject(
                raw,
                "stale_effective_date",
                effective_date_normalized=effective_date,
                minimum_effective_date=MIN_EFFECTIVE_DATES[doc_id],
            )
            continue
        if doc_id == "hr_leave_policy" and _STALE_HR_ANNUAL.search(text):
            reject(raw, "stale_hr_2025_semantic_conflict")
            continue

        if doc_id == "policy_refund_v4" and "14 ngày làm việc" in text:
            if apply_refund_window_fix:
                text = text.replace("14 ngày làm việc", "7 ngày làm việc")
            # Khi tắt fix, giữ corruption để tạo before evidence.

        duplicate_key = (doc_id, _norm_text(text))
        if duplicate_key in seen:
            reject(raw, "duplicate_after_normalization")
            continue
        seen.add(duplicate_key)
        cleaned.append(
            {
                "chunk_id": _stable_chunk_id(doc_id, text),
                "doc_id": doc_id,
                "chunk_text": text,
                "effective_date": effective_date,
                "exported_at": exported_at,
            }
        )
    return cleaned, quarantine


def write_cleaned_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["chunk_id", "doc_id", "chunk_text", "effective_date", "exported_at"]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in fields} for row in rows)


def write_quarantine_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    default_fields = ["chunk_id", "doc_id", "chunk_text", "effective_date", "exported_at", "reason"]
    fields = list(dict.fromkeys(default_fields + [key for row in rows for key in row]))
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore", restval="")
        writer.writeheader()
        writer.writerows(rows)
