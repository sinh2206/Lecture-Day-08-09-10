#!/usr/bin/env python3
"""Đánh giá retrieval Day 10 bằng hybrid semantic + lexical rerank."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

QUALITY_REPORT = ROOT / "docs" / "quality_report_template.md"
GROUP_REPORT = ROOT / "reports" / "group_report.md"

_STOPWORDS = {
    "ai", "bao", "các", "cách", "có", "cho", "của", "được", "gì", "hay",
    "khi", "là", "mấy", "một", "nào", "như", "những", "phải", "sau", "thế",
    "theo", "trong", "từ", "và", "với",
}


def _tokens(text: str) -> set[str]:
    return {
        token for token in re.findall(r"[0-9a-zA-ZÀ-ỹ_#@.-]+", text.lower())
        if len(token) > 1 and token not in _STOPWORDS
    }


def _expected_domain(question: str) -> str:
    lowered = question.lower()
    rules = (
        ("policy_refund_v4", ("hoàn tiền", "refund", "store credit", "finance team")),
        ("sla_p1_2026", ("p1", "sla", "escalat", "stakeholder", "incident-p1")),
        ("access_control_sop", ("access", "cấp quyền", "level ", "ciso", "it manager")),
        ("hr_leave_policy", ("phép", "nghỉ", "kinh nghiệm", "remote", "ốm")),
        ("it_helpdesk_faq", ("mật khẩu", "vpn", "đăng nhập", "hộp thư", "laptop")),
    )
    for doc_id, markers in rules:
        if any(marker in lowered for marker in markers):
            return doc_id
    return ""


def connect_collection():
    try:
        import chromadb
        from chromadb.utils import embedding_functions
    except ImportError as exc:
        raise RuntimeError("Hãy cài chromadb và sentence-transformers.") from exc

    db_value = os.environ.get("CHROMA_DB_PATH", "chroma_db")
    db_path = Path(db_value) if Path(db_value).is_absolute() else ROOT / db_value
    collection_name = os.environ.get("CHROMA_COLLECTION", "day10_kb")
    model_name = os.environ.get("EMBEDDING_MODEL", "all-MiniLM-L6-v2")
    client = chromadb.PersistentClient(path=str(db_path))
    embedding = embedding_functions.SentenceTransformerEmbeddingFunction(model_name=model_name)
    return client.get_collection(name=collection_name, embedding_function=embedding)


def retrieve_ranked(collection, question: str, top_k: int) -> List[Dict[str, Any]]:
    """Search rộng toàn snapshot nhỏ, rồi rerank bằng lexical coverage và domain signal."""
    count = collection.count()
    if count == 0:
        return []
    result = collection.query(
        query_texts=[question],
        n_results=count,
        include=["documents", "metadatas", "distances"],
    )
    question_tokens = _tokens(question)
    expected_domain = _expected_domain(question)
    candidates = []
    for text, metadata, distance in zip(
        (result.get("documents") or [[]])[0],
        (result.get("metadatas") or [[]])[0],
        (result.get("distances") or [[]])[0],
    ):
        metadata = metadata or {}
        semantic = max(0.0, min(1.0, 1.0 - float(distance)))
        lexical = len(question_tokens & _tokens(text)) / max(1, len(question_tokens))
        domain_bonus = 0.3 if expected_domain and metadata.get("doc_id") == expected_domain else 0.0
        candidates.append(
            {
                "text": text,
                "metadata": metadata,
                "score": round(0.5 * semantic + 0.5 * lexical + domain_bonus, 4),
            }
        )
    return sorted(candidates, key=lambda item: item["score"], reverse=True)[:top_k]


def evaluate_question(collection, question: Dict[str, Any], top_k: int) -> Dict[str, Any]:
    ranked = retrieve_ranked(collection, question["question"], top_k)
    docs = [item["text"] for item in ranked]
    metas = [item["metadata"] for item in ranked]
    blob = " ".join(docs).lower()
    expected = [value.lower() for value in question.get("must_contain_any", [])]
    forbidden = [value.lower() for value in question.get("must_not_contain", [])]
    top_doc = metas[0].get("doc_id", "") if metas else ""
    wanted = (question.get("expect_top1_doc_id") or "").strip()
    return {
        "question_id": question.get("id", ""),
        "question": question["question"],
        "top1_doc_id": top_doc,
        "top1_preview": docs[0][:180].replace("\n", " ") if docs else "",
        "contains_expected": any(value in blob for value in expected) if expected else True,
        "hits_forbidden": any(value in blob for value in forbidden) if forbidden else False,
        "top1_doc_matches": top_doc == wanted if wanted else None,
        "top_k_used": top_k,
        "retrieval_scores": [item["score"] for item in ranked],
        "top_doc_ids": [metadata.get("doc_id", "") for metadata in metas],
        "run_id": metas[0].get("run_id", "") if metas else "",
    }


def _truthy(value: str) -> bool:
    return str(value).strip().lower() in {"true", "yes", "1"}


def update_quality_report() -> None:
    """Tổng hợp mọi manifest/eval hiện có; không tạo số liệu nếu chưa có artifact."""
    if not QUALITY_REPORT.is_file():
        return
    manifests = []
    for path in sorted((ROOT / "artifacts" / "manifests").glob("manifest_*.json")):
        try:
            manifests.append((path.name, json.loads(path.read_text(encoding="utf-8"))))
        except (OSError, json.JSONDecodeError):
            continue
    evals = []
    for path in sorted((ROOT / "artifacts" / "eval").glob("*.csv")):
        try:
            with path.open(encoding="utf-8-sig", newline="") as handle:
                evals.append((path.name, list(csv.DictReader(handle))))
        except OSError:
            continue

    lines = ["## Runtime summary", ""]
    if manifests:
        lines.extend(
            [
                "### Pipeline runs",
                "",
                "| Manifest | run_id | Raw | Cleaned | Quarantine | Expectation halt fail | Freshness |",
                "|---|---|---:|---:|---:|---:|---|",
            ]
        )
        for name, manifest in manifests:
            failed = sum(
                not item.get("passed") and item.get("severity") == "halt"
                for item in manifest.get("expectations", [])
            )
            lines.append(
                f"| `{name}` | `{manifest.get('run_id')}` | {manifest.get('raw_records')} | "
                f"{manifest.get('cleaned_records')} | {manifest.get('quarantine_records')} | "
                f"{failed} | {manifest.get('freshness', {}).get('status', 'N/A')} |"
            )
    else:
        lines.append("Chưa có manifest.")

    if evals:
        lines.extend(
            [
                "",
                "### Retrieval evidence",
                "",
                "| CSV | Câu | contains_expected pass | forbidden sạch | top-1 đúng | run_id |",
                "|---|---:|---:|---:|---:|---|",
            ]
        )
        for name, rows in evals:
            expected_pass = sum(_truthy(row.get("contains_expected", "")) for row in rows)
            forbidden_clean = sum(not _truthy(row.get("hits_forbidden", "")) for row in rows)
            top1_rows = [row for row in rows if row.get("top1_doc_matches", "") not in ("", "None")]
            top1_pass = sum(_truthy(row.get("top1_doc_matches", "")) for row in top1_rows)
            run_id = rows[0].get("run_id", "") if rows else ""
            lines.append(
                f"| `{name}` | {len(rows)} | {expected_pass}/{len(rows)} | "
                f"{forbidden_clean}/{len(rows)} | {top1_pass}/{len(top1_rows)} | `{run_id}` |"
            )
        lines.extend(["", "### Refund window before/after", ""])
        for name, rows in evals:
            refund = next((row for row in rows if row.get("question_id") == "q_refund_window"), None)
            if refund:
                lines.append(
                    f"- `{name}`: contains_expected={refund.get('contains_expected')}, "
                    f"hits_forbidden={refund.get('hits_forbidden')}, top1={refund.get('top1_doc_id')}."
                )
    else:
        lines.extend(["", "Chưa có CSV eval."])

    body = "\n".join(lines)
    for path, start, end in (
        (QUALITY_REPORT, "<!-- AUTO_QUALITY_START -->", "<!-- AUTO_QUALITY_END -->"),
        (GROUP_REPORT, "<!-- AUTO_GROUP_EVIDENCE_START -->", "<!-- AUTO_GROUP_EVIDENCE_END -->"),
    ):
        if not path.is_file():
            continue
        content = path.read_text(encoding="utf-8")
        if start in content and end in content:
            prefix = content.split(start, 1)[0]
            suffix = content.split(end, 1)[1]
            path.write_text(f"{prefix}{start}\n{body}\n{end}{suffix}", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Đánh giá retrieval Day 10")
    parser.add_argument("--questions", default=str(ROOT / "data" / "test_questions.json"))
    parser.add_argument(
        "--out", default=str(ROOT / "artifacts" / "eval" / "before_after_eval.csv")
    )
    parser.add_argument("--top-k", type=int, default=3)
    args = parser.parse_args()
    if args.top_k < 1:
        print("--top-k phải >= 1", file=sys.stderr)
        return 1

    question_path = Path(args.questions)
    if not question_path.is_file():
        print(f"Không tìm thấy questions: {question_path}", file=sys.stderr)
        return 1
    try:
        collection = connect_collection()
    except Exception as exc:
        print(f"Collection error: {exc}", file=sys.stderr)
        return 2

    questions = json.loads(question_path.read_text(encoding="utf-8"))
    rows = [evaluate_question(collection, question, args.top_k) for question in questions]
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "question_id", "question", "top1_doc_id", "top1_preview",
        "contains_expected", "hits_forbidden", "top1_doc_matches", "top_k_used",
        "retrieval_scores", "top_doc_ids", "run_id",
    ]
    with output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    update_quality_report()
    print(f"Đã ghi {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
