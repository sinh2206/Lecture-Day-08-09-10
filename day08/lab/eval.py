"""Day 08 - scorecard tự động, so sánh A/B và xuất grading log."""

from __future__ import annotations

import argparse
import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from rag_answer import rag_answer

ROOT = Path(__file__).resolve().parent
TEST_QUESTIONS_PATH = ROOT / "data" / "test_questions.json"
RESULTS_DIR = ROOT / "results"
LOGS_DIR = ROOT / "logs"
TUNING_LOG = ROOT / "docs" / "tuning-log.md"
GROUP_REPORT = ROOT / "reports" / "group_report.md"

BASELINE_CONFIG = {
    "retrieval_mode": "dense",
    "top_k_search": 10,
    "top_k_select": 3,
    "use_rerank": False,
    "label": "baseline_dense",
}
VARIANT_CONFIG = {
    "retrieval_mode": "hybrid",
    "top_k_search": 10,
    "top_k_select": 3,
    "use_rerank": False,
    "label": "variant_hybrid",
}

_STOPWORDS = {
    "ai", "bao", "các", "cách", "có", "cho", "của", "được", "gì", "hay",
    "khi", "là", "mấy", "một", "nào", "như", "những", "phải", "sau", "thế",
    "theo", "trong", "từ", "và", "với",
}


def _tokens(text: str) -> set[str]:
    clean = re.sub(r"\[\d+\]", " ", text.lower())
    return {
        token for token in re.findall(r"[0-9a-zA-ZÀ-ỹ_#@.-]+", clean)
        if len(token) > 1 and token not in _STOPWORDS
    }


def _score_from_ratio(ratio: float) -> int:
    return max(1, min(5, 1 + round(4 * max(0.0, min(1.0, ratio)))))


def _is_abstain(answer: str) -> bool:
    lowered = answer.lower()
    return "không đủ dữ liệu" in lowered or "không đủ thông tin" in lowered


def score_faithfulness(
    answer: str, chunks_used: List[Dict[str, Any]]
) -> Dict[str, Any]:
    if answer.startswith(("ERROR:", "PIPELINE_")):
        return {"score": 1, "notes": "Pipeline lỗi, không thể xác minh grounding."}
    if not chunks_used:
        score = 5 if _is_abstain(answer) else 1
        return {"score": score, "notes": "Không có context; kiểm tra hành vi abstain."}

    context_tokens = _tokens(" ".join(chunk.get("text", "") for chunk in chunks_used))
    answer_tokens = _tokens(answer)
    ratio = len(answer_tokens & context_tokens) / max(1, len(answer_tokens))
    return {
        "score": _score_from_ratio(ratio),
        "notes": f"Tỷ lệ token nội dung được context hỗ trợ: {ratio:.1%}.",
    }


def score_answer_relevance(query: str, answer: str) -> Dict[str, Any]:
    if answer.startswith(("ERROR:", "PIPELINE_")):
        return {"score": 1, "notes": "Pipeline lỗi."}
    query_tokens = _tokens(query)
    overlap = len(query_tokens & _tokens(answer)) / max(1, len(query_tokens))
    if _is_abstain(answer):
        overlap = max(overlap, 0.5)
    return {
        "score": _score_from_ratio(overlap),
        "notes": f"Độ phủ từ khóa trọng tâm của câu hỏi: {overlap:.1%}.",
    }


def _source_key(source: str) -> str:
    name = source.replace("\\", "/").split("/")[-1].lower()
    name = re.sub(r"\.(pdf|md|txt)$", "", name)
    return re.sub(r"[^a-z0-9]+", "", name)


def score_context_recall(
    chunks_used: List[Dict[str, Any]], expected_sources: List[str]
) -> Dict[str, Any]:
    if not expected_sources:
        score = 5 if not chunks_used else 3
        return {
            "score": score,
            "recall": 1.0 if not chunks_used else 0.5,
            "notes": "Câu abstain: không kỳ vọng nguồn chứng cứ.",
        }

    retrieved = {
        _source_key(chunk.get("metadata", {}).get("source", ""))
        for chunk in chunks_used
    }
    expected = {_source_key(source) for source in expected_sources}
    found = expected & retrieved
    recall = len(found) / len(expected)
    missing = sorted(expected - retrieved)
    return {
        "score": _score_from_ratio(recall),
        "recall": recall,
        "notes": f"Tìm thấy {len(found)}/{len(expected)} nguồn; thiếu={missing}.",
    }


def score_completeness(
    query: str, answer: str, expected_answer: str
) -> Dict[str, Any]:
    if not expected_answer:
        return {"score": None, "notes": "Không có expected_answer để đối chiếu."}
    if answer.startswith(("ERROR:", "PIPELINE_")):
        return {"score": 1, "notes": "Pipeline lỗi."}
    expected_tokens = _tokens(expected_answer)
    ratio = len(expected_tokens & _tokens(answer)) / max(1, len(expected_tokens))
    return {
        "score": _score_from_ratio(ratio),
        "notes": f"Độ phủ nội dung expected_answer: {ratio:.1%}.",
    }


def run_scorecard(
    config: Dict[str, Any],
    test_questions: Optional[List[Dict[str, Any]]] = None,
    verbose: bool = True,
) -> List[Dict[str, Any]]:
    if test_questions is None:
        test_questions = json.loads(TEST_QUESTIONS_PATH.read_text(encoding="utf-8"))

    rows: List[Dict[str, Any]] = []
    for question in test_questions:
        try:
            result = rag_answer(
                question["question"],
                retrieval_mode=config["retrieval_mode"],
                top_k_search=config["top_k_search"],
                top_k_select=config["top_k_select"],
                use_rerank=config["use_rerank"],
            )
            answer = result["answer"]
            chunks = result["chunks_used"]
            sources = result["sources"]
        except Exception as exc:
            answer, chunks, sources = f"ERROR: {exc}", [], []

        faith = score_faithfulness(answer, chunks)
        relevance = score_answer_relevance(question["question"], answer)
        recall = score_context_recall(chunks, question.get("expected_sources", []))
        complete = score_completeness(
            question["question"], answer, question.get("expected_answer", "")
        )
        row = {
            "id": question["id"],
            "category": question.get("category", ""),
            "query": question["question"],
            "answer": answer,
            "sources": "; ".join(sources),
            "chunks_retrieved": len(chunks),
            "faithfulness": faith["score"],
            "faithfulness_notes": faith["notes"],
            "relevance": relevance["score"],
            "relevance_notes": relevance["notes"],
            "context_recall": recall["score"],
            "context_recall_notes": recall["notes"],
            "completeness": complete["score"],
            "completeness_notes": complete["notes"],
            "config_label": config["label"],
        }
        rows.append(row)
        if verbose:
            print(
                f"{row['id']}: F={row['faithfulness']} R={row['relevance']} "
                f"CR={row['context_recall']} C={row['completeness']}"
            )
    return rows


def _average(rows: List[Dict[str, Any]], metric: str) -> Optional[float]:
    values = [float(row[metric]) for row in rows if row.get(metric) is not None]
    return sum(values) / len(values) if values else None


def compare_ab(
    baseline_results: List[Dict[str, Any]],
    variant_results: List[Dict[str, Any]],
    output_csv: Optional[str] = None,
) -> Dict[str, Dict[str, Optional[float]]]:
    metrics = ("faithfulness", "relevance", "context_recall", "completeness")
    summary: Dict[str, Dict[str, Optional[float]]] = {}
    for metric in metrics:
        baseline = _average(baseline_results, metric)
        variant = _average(variant_results, metric)
        summary[metric] = {
            "baseline": baseline,
            "variant": variant,
            "delta": variant - baseline if baseline is not None and variant is not None else None,
        }
        print(
            f"{metric}: baseline={baseline if baseline is not None else 'N/A'} "
            f"variant={variant if variant is not None else 'N/A'} "
            f"delta={summary[metric]['delta'] if summary[metric]['delta'] is not None else 'N/A'}"
        )

    if output_csv:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        path = RESULTS_DIR / output_csv
        fields = list((baseline_results + variant_results)[0].keys())
        with path.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(baseline_results + variant_results)
    return summary


def generate_scorecard_summary(rows: List[Dict[str, Any]], label: str) -> str:
    metrics = ("faithfulness", "relevance", "context_recall", "completeness")
    lines = [
        f"# Scorecard: {label}",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        "## Summary",
        "",
        "| Metric | Average |",
        "|---|---:|",
    ]
    for metric in metrics:
        average = _average(rows, metric)
        lines.append(f"| {metric} | {average:.2f}/5 |" if average is not None else f"| {metric} | N/A |")
    lines.extend(
        [
            "",
            "## Per-question",
            "",
            "| ID | Faithfulness | Relevance | Context recall | Completeness | Answer |",
            "|---|---:|---:|---:|---:|---|",
        ]
    )
    for row in rows:
        answer = str(row["answer"]).replace("|", "\\|").replace("\n", " ")[:180]
        lines.append(
            f"| {row['id']} | {row['faithfulness']} | {row['relevance']} | "
            f"{row['context_recall']} | {row['completeness']} | {answer} |"
        )
    return "\n".join(lines) + "\n"


def run_grading_log(questions_path: Path) -> Path:
    questions = json.loads(questions_path.read_text(encoding="utf-8"))
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    output = LOGS_DIR / "grading_run.json"
    records = []
    for index, question in enumerate(questions, 1):
        try:
            result = rag_answer(question["question"], retrieval_mode="hybrid")
            answer = result["answer"]
            sources = result["sources"]
            chunks_retrieved = len(result["chunks_used"])
        except Exception as exc:
            answer = f"PIPELINE_ERROR: {exc}"
            sources = []
            chunks_retrieved = 0
        records.append(
            {
                "id": question.get("id", f"gq{index:02d}"),
                "question": question["question"],
                "answer": answer,
                "sources": sources,
                "chunks_retrieved": chunks_retrieved,
                "retrieval_mode": "hybrid",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
    output.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    return output


def update_tuning_log(
    summary: Dict[str, Dict[str, Optional[float]]],
    baseline: List[Dict[str, Any]],
    variant: List[Dict[str, Any]],
) -> None:
    """Thay riêng vùng auto-generated, giữ nguyên phần phân tích thiết kế."""
    if not TUNING_LOG.is_file():
        return
    start, end = "<!-- AUTO_RESULTS_START -->", "<!-- AUTO_RESULTS_END -->"
    content = TUNING_LOG.read_text(encoding="utf-8")
    if start not in content or end not in content:
        return
    lines = [
        start,
        f"Cập nhật: {datetime.now(timezone.utc).isoformat()}",
        "",
        "| Metric | Baseline | Variant | Delta |",
        "|---|---:|---:|---:|",
    ]
    for metric, values in summary.items():
        b_value, v_value, delta = values["baseline"], values["variant"], values["delta"]
        lines.append(
            f"| {metric} | {b_value:.2f} | {v_value:.2f} | {delta:+.2f} |"
            if b_value is not None and v_value is not None and delta is not None
            else f"| {metric} | N/A | N/A | N/A |"
        )
    baseline_by_id = {row["id"]: row for row in baseline}
    improved, regressed = [], []
    for row in variant:
        before = baseline_by_id.get(row["id"], {})
        b_total = sum(float(before.get(metric) or 0) for metric in summary)
        v_total = sum(float(row.get(metric) or 0) for metric in summary)
        if v_total > b_total:
            improved.append(row["id"])
        elif v_total < b_total:
            regressed.append(row["id"])
    lines.extend(
        [
            "",
            f"- Câu cải thiện: {improved or 'Không có'}",
            f"- Câu giảm điểm: {regressed or 'Không có'}",
            "- Nguồn chi tiết: `results/scorecard_baseline.md`, `results/scorecard_variant.md`, `results/ab_comparison.csv`.",
            end,
        ]
    )
    prefix = content.split(start, 1)[0]
    suffix = content.split(end, 1)[1]
    TUNING_LOG.write_text(prefix + "\n".join(lines) + suffix, encoding="utf-8")
    group_start = "<!-- AUTO_DAY08_RESULTS_START -->"
    group_end = "<!-- AUTO_DAY08_RESULTS_END -->"
    if GROUP_REPORT.is_file():
        group = GROUP_REPORT.read_text(encoding="utf-8")
        if group_start in group and group_end in group:
            group_prefix = group.split(group_start, 1)[0]
            group_suffix = group.split(group_end, 1)[1]
            runtime_body = "\n".join(lines[1:-1])
            GROUP_REPORT.write_text(
                f"{group_prefix}{group_start}\n{runtime_body}\n{group_end}{group_suffix}",
                encoding="utf-8",
            )


def main() -> int:
    parser = argparse.ArgumentParser(description="Đánh giá RAG Day 08")
    parser.add_argument("--grading", default="", help="Đường dẫn grading_questions.json")
    args = parser.parse_args()
    if args.grading:
        path = run_grading_log(Path(args.grading))
        print(f"Đã ghi {path}")
        return 0

    questions = json.loads(TEST_QUESTIONS_PATH.read_text(encoding="utf-8"))
    baseline = run_scorecard(BASELINE_CONFIG, questions)
    variant = run_scorecard(VARIANT_CONFIG, questions)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (RESULTS_DIR / "scorecard_baseline.md").write_text(
        generate_scorecard_summary(baseline, BASELINE_CONFIG["label"]), encoding="utf-8"
    )
    (RESULTS_DIR / "scorecard_variant.md").write_text(
        generate_scorecard_summary(variant, VARIANT_CONFIG["label"]), encoding="utf-8"
    )
    summary = compare_ab(baseline, variant, "ab_comparison.csv")
    update_tuning_log(summary, baseline, variant)
    print(f"Đã ghi scorecard và A/B comparison vào {RESULTS_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
