"""Kiểm tra freshness tại hai mốc source export và pipeline publish."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Tuple


def parse_iso(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        # Cho phép "2026-04-10T08:00:00" không có timezone
        if ts.endswith("Z"):
            return datetime.fromisoformat(ts.replace("Z", "+00:00"))
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def check_manifest_freshness(
    manifest_path: Path,
    *,
    sla_hours: float = 24.0,
    now: datetime | None = None,
) -> Tuple[str, Dict[str, Any]]:
    """
    Trả về ("PASS" | "WARN" | "FAIL", detail dict).

    Source vượt SLA là FAIL; timestamp thiếu/sai hoặc nằm trong tương lai là WARN.
    Tuổi publish được ghi riêng để phân biệt dữ liệu nguồn cũ với pipeline ngừng chạy.
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    if not manifest_path.is_file():
        return "FAIL", {"reason": "manifest_missing", "path": str(manifest_path)}

    try:
        data: Dict[str, Any] = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return "FAIL", {"reason": "manifest_invalid", "error": str(exc)}

    source_raw = data.get("latest_exported_at")
    source_dt = parse_iso(str(source_raw)) if source_raw else None
    publish_raw = data.get("published_at") or data.get("run_timestamp")
    publish_dt = parse_iso(str(publish_raw)) if publish_raw else None
    if source_dt is None:
        return "WARN", {
            "reason": "source_timestamp_missing_or_invalid",
            "latest_exported_at": source_raw,
            "published_at": publish_raw,
        }

    age_hours = (now - source_dt).total_seconds() / 3600.0
    detail = {
        "latest_exported_at": source_raw,
        "age_hours": round(age_hours, 3),
        "source_age_hours": round(age_hours, 3),
        "published_at": publish_raw,
        "publish_age_hours": round((now - publish_dt).total_seconds() / 3600.0, 3)
        if publish_dt else None,
        "sla_hours": sla_hours,
    }
    if age_hours < 0:
        return "WARN", {**detail, "reason": "source_timestamp_in_future"}
    if publish_dt and detail["publish_age_hours"] < 0:
        return "WARN", {**detail, "reason": "publish_timestamp_in_future"}
    if age_hours <= sla_hours:
        return "PASS", detail
    return "FAIL", {**detail, "reason": "freshness_sla_exceeded"}
