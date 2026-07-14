#!/usr/bin/env python3
"""
Lab Day 10 — ETL entrypoint: ingest → clean → validate → embed.

Tiếp nối Day 09: cùng corpus docs trong data/docs/; pipeline này xử lý *export* raw (CSV)
đại diện cho lớp ingestion từ DB/API trước khi embed lại vector store.

Chạy nhanh:
  pip install -r requirements.txt
  cp .env.example .env
  python etl_pipeline.py run

Chế độ inject (Sprint 3 — bỏ fix refund để expectation fail / eval xấu):
  python etl_pipeline.py run --no-refund-fix --skip-validate
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from monitoring.freshness_check import check_manifest_freshness
from quality.expectations import run_expectations
from transform.cleaning_rules import clean_rows, load_raw_csv, write_cleaned_csv, write_quarantine_csv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

RAW_DEFAULT = ROOT / "data" / "raw" / "policy_export_dirty.csv"
ART = ROOT / "artifacts"
LOG_DIR = ART / "logs"
MAN_DIR = ART / "manifests"
QUAR_DIR = ART / "quarantine"
CLEAN_DIR = ART / "cleaned"


def _log(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def _safe_run_id(value: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z_.-]+", "-", value).strip("-.")
    if not cleaned:
        raise ValueError("run_id không hợp lệ")
    return cleaned[:96]


def _display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path.resolve())


def _resolve_from_root(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def cmd_run(args: argparse.Namespace) -> int:
    run_id = _safe_run_id(
        args.run_id or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    )
    raw_path = Path(args.raw).resolve()
    if not raw_path.is_file():
        print(f"ERROR: raw file not found: {raw_path}", file=sys.stderr)
        return 1

    log_path = LOG_DIR / f"run_{run_id}.log"
    for p in (LOG_DIR, MAN_DIR, QUAR_DIR, CLEAN_DIR):
        p.mkdir(parents=True, exist_ok=True)

    def log(msg: str) -> None:
        print(msg)
        _log(log_path, msg)

    rows = load_raw_csv(raw_path)
    raw_count = len(rows)
    log(f"run_id={run_id}")
    log(f"raw_records={raw_count}")

    cleaned, quarantine = clean_rows(
        rows,
        apply_refund_window_fix=not args.no_refund_fix,
    )
    cleaned_path = CLEAN_DIR / f"cleaned_{run_id}.csv"
    quar_path = QUAR_DIR / f"quarantine_{run_id}.csv"
    write_cleaned_csv(cleaned_path, cleaned)
    write_quarantine_csv(quar_path, quarantine)

    log(f"cleaned_records={len(cleaned)}")
    log(f"quarantine_records={len(quarantine)}")
    log(f"cleaned_doc_counts={json.dumps(Counter(row['doc_id'] for row in cleaned), ensure_ascii=False, sort_keys=True)}")
    log(f"quarantine_reason_counts={json.dumps(Counter(row['reason'] for row in quarantine), ensure_ascii=False, sort_keys=True)}")
    log(f"cleaned_csv={_display_path(cleaned_path)}")
    log(f"quarantine_csv={_display_path(quar_path)}")

    results, halt = run_expectations(cleaned)
    for r in results:
        sym = "OK" if r.passed else "FAIL"
        log(f"expectation[{r.name}] {sym} ({r.severity}) :: {r.detail}")
    log(f"expectations_passed={sum(result.passed for result in results)}/{len(results)}")
    if halt and not args.skip_validate:
        log("PIPELINE_HALT: expectation suite failed (halt).")
        return 2
    if halt and args.skip_validate:
        log("WARN: expectation failed but --skip-validate → tiếp tục embed (chỉ dùng cho demo Sprint 3).")

    # Embed
    embed_ok = cmd_embed_internal(
        cleaned_path,
        run_id=run_id,
        log=log,
    )
    if not embed_ok:
        return 3

    latest_exported = ""
    if cleaned:
        latest_exported = max((r.get("exported_at") or "" for r in cleaned), default="")

    published_at = datetime.now(timezone.utc).isoformat()
    manifest = {
        "run_id": run_id,
        "run_timestamp": published_at,
        "published_at": published_at,
        "raw_path": _display_path(raw_path),
        "raw_records": raw_count,
        "cleaned_records": len(cleaned),
        "quarantine_records": len(quarantine),
        "latest_exported_at": latest_exported,
        "no_refund_fix": bool(args.no_refund_fix),
        "skipped_validate": bool(args.skip_validate and halt),
        "cleaned_csv": _display_path(cleaned_path),
        "quarantine_csv": _display_path(quar_path),
        "quarantine_reason_counts": dict(Counter(row["reason"] for row in quarantine)),
        "expectations": [
            {
                "name": result.name,
                "passed": result.passed,
                "severity": result.severity,
                "detail": result.detail,
            }
            for result in results
        ],
        "chroma_path": str(
            _resolve_from_root(os.environ.get("CHROMA_DB_PATH", "chroma_db")).resolve()
        ),
        "chroma_collection": os.environ.get("CHROMA_COLLECTION", "day10_kb"),
        "embedding_model": os.environ.get("EMBEDDING_MODEL", "all-MiniLM-L6-v2"),
    }
    man_path = MAN_DIR / f"manifest_{run_id}.json"
    man_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"manifest_written={_display_path(man_path)}")

    status, fdetail = check_manifest_freshness(man_path, sla_hours=float(os.environ.get("FRESHNESS_SLA_HOURS", "24")))
    manifest["freshness"] = {"status": status, "detail": fdetail}
    man_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"freshness_check={status} {json.dumps(fdetail, ensure_ascii=False)}")

    log("PIPELINE_OK")
    return 0


def cmd_embed_internal(cleaned_csv: Path, *, run_id: str, log) -> bool:
    try:
        import chromadb
        from chromadb.utils import embedding_functions
    except ImportError:
        log("ERROR: chromadb chưa cài. pip install -r requirements.txt")
        return False

    db_path = _resolve_from_root(os.environ.get("CHROMA_DB_PATH", "chroma_db"))
    collection_name = os.environ.get("CHROMA_COLLECTION", "day10_kb")
    model_name = os.environ.get("EMBEDDING_MODEL", "all-MiniLM-L6-v2")
    log(f"embedding_model={model_name}")

    from transform.cleaning_rules import load_raw_csv as load_csv  # same loader

    rows = load_csv(cleaned_csv)
    if not rows:
        log("WARN: cleaned CSV rỗng — không embed.")
        return True

    client = chromadb.PersistentClient(path=str(db_path))
    emb = embedding_functions.SentenceTransformerEmbeddingFunction(model_name=model_name)
    col = client.get_or_create_collection(
        name=collection_name,
        embedding_function=emb,
        metadata={"hnsw:space": "cosine"},
    )

    ids = [r["chunk_id"] for r in rows]
    # Tránh “mồi cũ” trong top-k: xóa id không còn trong cleaned run này (index = snapshot publish).
    try:
        prev = col.get(include=[])
        prev_ids = set(prev.get("ids") or [])
        drop = sorted(prev_ids - set(ids))
        if drop:
            col.delete(ids=drop)
            log(f"embed_prune_removed={len(drop)}")
    except Exception as e:
        log(f"WARN: embed prune skip: {e}")
    documents = [r["chunk_text"] for r in rows]
    metadatas = [
        {
            "doc_id": r.get("doc_id", ""),
            "effective_date": r.get("effective_date", ""),
            "run_id": run_id,
        }
        for r in rows
    ]
    # Idempotent: upsert theo chunk_id
    col.upsert(ids=ids, documents=documents, metadatas=metadatas)
    log(f"embed_upsert count={len(ids)} collection={collection_name}")
    return True


def cmd_freshness(args: argparse.Namespace) -> int:
    p = Path(args.manifest)
    if not p.is_file():
        print(f"manifest not found: {p}", file=sys.stderr)
        return 1
    sla = float(os.environ.get("FRESHNESS_SLA_HOURS", "24"))
    status, detail = check_manifest_freshness(p, sla_hours=sla)
    print(status, json.dumps(detail, ensure_ascii=False))
    return 0 if status != "FAIL" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Day 10 ETL pipeline")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="ingest → clean → validate → embed")
    p_run.add_argument("--raw", default=str(RAW_DEFAULT), help="Đường dẫn CSV raw export")
    p_run.add_argument("--run-id", default="", help="ID run (mặc định: UTC timestamp)")
    p_run.add_argument(
        "--no-refund-fix",
        action="store_true",
        help="Không áp dụng rule fix cửa sổ 14→7 ngày (dùng cho inject corruption / before).",
    )
    p_run.add_argument(
        "--skip-validate",
        action="store_true",
        help="Vẫn embed khi expectation halt (chỉ phục vụ demo có chủ đích).",
    )
    p_run.set_defaults(func=cmd_run)

    p_fr = sub.add_parser("freshness", help="Đọc manifest và kiểm tra SLA freshness")
    p_fr.add_argument("--manifest", required=True)
    p_fr.set_defaults(func=cmd_freshness)

    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
