#!/usr/bin/env python3
"""Chạy bộ grading Day 10 và ghi một JSON object trên mỗi dòng."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from eval_retrieval import ROOT, connect_collection, evaluate_question


def main() -> int:
    parser = argparse.ArgumentParser(description="Grading retrieval Day 10")
    parser.add_argument("--questions", default=str(ROOT / "data" / "grading_questions.json"))
    parser.add_argument(
        "--out", default=str(ROOT / "artifacts" / "eval" / "grading_run.jsonl")
    )
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()

    question_path = Path(args.questions)
    if not question_path.is_file():
        print(f"Không tìm thấy grading questions: {question_path}", file=sys.stderr)
        return 1
    if args.top_k < 1:
        print("--top-k phải >= 1", file=sys.stderr)
        return 1
    try:
        collection = connect_collection()
    except Exception as exc:
        print(f"Collection error: {exc}", file=sys.stderr)
        return 2

    questions = json.loads(question_path.read_text(encoding="utf-8"))
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for question in questions:
            try:
                result = evaluate_question(collection, question, args.top_k)
                record = {
                    "id": question.get("id"),
                    "question": question["question"],
                    "top1_doc_id": result["top1_doc_id"],
                    "top_doc_ids": result["top_doc_ids"],
                    "top1_preview": result["top1_preview"],
                    "contains_expected": result["contains_expected"],
                    "hits_forbidden": result["hits_forbidden"],
                    "top1_doc_matches": result["top1_doc_matches"],
                    "top_k_used": args.top_k,
                    "run_id": result["run_id"],
                    "grading_criteria": question.get("grading_criteria", []),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            except Exception as exc:
                record = {
                    "id": question.get("id"),
                    "question": question.get("question", ""),
                    "top1_doc_id": "",
                    "top_doc_ids": [],
                    "top1_preview": "",
                    "contains_expected": False,
                    "hits_forbidden": False,
                    "top1_doc_matches": False,
                    "top_k_used": args.top_k,
                    "run_id": "",
                    "grading_criteria": question.get("grading_criteria", []),
                    "error": f"PIPELINE_ERROR: {exc}",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"Đã ghi {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
