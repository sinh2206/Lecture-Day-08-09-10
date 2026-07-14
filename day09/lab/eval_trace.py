"""Day 09 - chạy bộ câu hỏi, lưu trace và tính metric orchestration."""

from __future__ import annotations

import argparse
import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from graph import run_graph, save_trace

ROOT = Path(__file__).resolve().parent
ARTIFACTS_DIR = ROOT / "artifacts"
TRACES_DIR = ARTIFACTS_DIR / "traces"
TEST_QUESTIONS = ROOT / "data" / "test_questions.json"
GRADING_QUESTIONS = ROOT / "data" / "grading_questions.json"
ROUTING_DOC = ROOT / "docs" / "routing_decisions.md"
COMPARISON_DOC = ROOT / "docs" / "single_vs_multi_comparison.md"
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


def _answer_match(answer: str, expected: str) -> bool:
    expected_tokens = _tokens(expected)
    coverage = len(expected_tokens & _tokens(answer)) / max(1, len(expected_tokens))
    return coverage >= 0.35


def _source_recall(actual: List[str], expected: List[str]) -> float:
    if not expected:
        return 1.0 if not actual else 0.5
    actual_names = {Path(source).name.lower() for source in actual}
    expected_names = {Path(source).name.lower() for source in expected}
    return len(actual_names & expected_names) / len(expected_names)


def run_test_questions(questions_file: str = str(TEST_QUESTIONS)) -> List[dict]:
    questions = json.loads(Path(questions_file).read_text(encoding="utf-8"))
    results = []
    for index, question in enumerate(questions, 1):
        question_id = question.get("id", f"q{index:02d}")
        try:
            result = run_graph(question["question"])
            expected_sources = question.get("expected_sources", [])
            evaluation = {
                "expected_route": question.get("expected_route"),
                "route_matches": result.get("supervisor_route") == question.get("expected_route"),
                "expected_sources": expected_sources,
                "source_recall": _source_recall(result.get("retrieved_sources", []), expected_sources),
                "answer_matches": _answer_match(
                    result.get("final_answer", ""), question.get("expected_answer", "")
                ),
                "test_type": question.get("test_type", "unknown"),
            }
            result["question_id"] = question_id
            result["evaluation"] = evaluation
            trace_file = save_trace(result, str(TRACES_DIR))
            results.append({"id": question_id, "result": result, "trace_file": trace_file})
            print(
                f"{question_id}: route={result['supervisor_route']} "
                f"confidence={result['confidence']:.2f} match={evaluation['answer_matches']}"
            )
        except Exception as exc:
            results.append({"id": question_id, "result": None, "error": str(exc)})
            print(f"{question_id}: ERROR {exc}")
    return results


def run_grading_questions(questions_file: str = str(GRADING_QUESTIONS)) -> str:
    path = Path(questions_file)
    if not path.is_file():
        print(f"Chưa có grading questions: {path}")
        return ""
    questions = json.loads(path.read_text(encoding="utf-8"))
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    output = ARTIFACTS_DIR / "grading_run.jsonl"
    with output.open("w", encoding="utf-8") as handle:
        for index, question in enumerate(questions, 1):
            question_id = question.get("id", f"gq{index:02d}")
            try:
                result = run_graph(question["question"])
                record = {
                    "id": question_id,
                    "question": question["question"],
                    "answer": result["final_answer"],
                    "sources": result.get("sources", []),
                    "supervisor_route": result["supervisor_route"],
                    "route_reason": result["route_reason"],
                    "workers_called": result["workers_called"],
                    "mcp_tools_used": [
                        call.get("tool") for call in result.get("mcp_tools_used", [])
                    ],
                    "mcp_trace": result.get("mcp_tools_used", []),
                    "confidence": result["confidence"],
                    "hitl_triggered": result["hitl_triggered"],
                    "latency_ms": result["latency_ms"],
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            except Exception as exc:
                record = {
                    "id": question_id,
                    "question": question["question"],
                    "answer": f"PIPELINE_ERROR: {exc}",
                    "sources": [],
                    "supervisor_route": "error",
                    "route_reason": str(exc),
                    "workers_called": [],
                    "mcp_tools_used": [],
                    "mcp_trace": [],
                    "confidence": 0.0,
                    "hitl_triggered": False,
                    "latency_ms": None,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return str(output)


def _load_traces(traces_dir: str) -> List[Dict[str, Any]]:
    directory = Path(traces_dir)
    if not directory.is_dir():
        return []
    traces = []
    for path in sorted(directory.glob("*.json")):
        try:
            traces.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return traces


def analyze_traces(traces_dir: str = str(TRACES_DIR)) -> Dict[str, Any]:
    traces = _load_traces(traces_dir)
    if not traces:
        return {}
    total = len(traces)
    routes: Dict[str, int] = {}
    sources: Dict[str, int] = {}
    route_matches: List[bool] = []
    source_recalls: List[float] = []
    answer_matches: List[bool] = []
    multi_matches: List[bool] = []
    errors = 0

    for trace in traces:
        route = trace.get("supervisor_route", "unknown")
        routes[route] = routes.get(route, 0) + 1
        for source in trace.get("retrieved_sources", []):
            sources[source] = sources.get(source, 0) + 1
        evaluation = trace.get("evaluation", {})
        if evaluation:
            route_matches.append(bool(evaluation.get("route_matches")))
            source_recalls.append(float(evaluation.get("source_recall", 0)))
            answer_matches.append(bool(evaluation.get("answer_matches")))
            if "multi" in evaluation.get("test_type", ""):
                multi_matches.append(bool(evaluation.get("answer_matches")))
        errors += any(log.get("error") for log in trace.get("worker_io_logs", []))

    confidences = [float(trace.get("confidence", 0)) for trace in traces]
    latencies = [float(trace["latency_ms"]) for trace in traces if trace.get("latency_ms") is not None]
    mcp_runs = sum(bool(trace.get("mcp_tools_used")) for trace in traces)
    hitl_runs = sum(bool(trace.get("hitl_triggered")) for trace in traces)
    abstains = sum("không đủ" in trace.get("final_answer", "").lower() for trace in traces)
    return {
        "total_traces": total,
        "routing_distribution": {
            route: {"count": count, "rate": round(count / total, 3)}
            for route, count in sorted(routes.items())
        },
        "routing_accuracy": round(sum(route_matches) / len(route_matches), 3) if route_matches else None,
        "answer_match_rate": round(sum(answer_matches) / len(answer_matches), 3) if answer_matches else None,
        "multi_hop_accuracy": round(sum(multi_matches) / len(multi_matches), 3) if multi_matches else None,
        "avg_source_recall": round(sum(source_recalls) / len(source_recalls), 3) if source_recalls else None,
        "avg_confidence": round(sum(confidences) / len(confidences), 3),
        "avg_latency_ms": round(sum(latencies) / len(latencies)) if latencies else None,
        "mcp_usage_rate": round(mcp_runs / total, 3),
        "hitl_rate": round(hitl_runs / total, 3),
        "abstain_rate": round(abstains / total, 3),
        "worker_error_runs": errors,
        "top_sources": sorted(sources.items(), key=lambda item: (-item[1], item[0]))[:5],
    }


def _day08_metrics(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {"status": "N/A", "reason": f"Không tìm thấy {path}"}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = [row for row in csv.DictReader(handle) if row.get("config_label") == "baseline_dense"]
    metrics = ("faithfulness", "relevance", "context_recall", "completeness")
    averages = {}
    for metric in metrics:
        values = [float(row[metric]) for row in rows if row.get(metric)]
        averages[metric] = round(sum(values) / len(values), 3) if values else None
    abstains = sum("không đủ" in row.get("answer", "").lower() for row in rows)
    return {
        "total_questions": len(rows),
        "quality_scores": averages,
        "abstain_rate": round(abstains / len(rows), 3) if rows else None,
        "avg_latency_ms": None,
        "routing_visibility": False,
    }


def compare_single_vs_multi(
    multi_traces_dir: str = str(TRACES_DIR),
    day08_results_file: Optional[str] = None,
) -> Dict[str, Any]:
    default_day08 = ROOT.parents[1] / "day08" / "lab" / "results" / "ab_comparison.csv"
    day08_path = Path(day08_results_file) if day08_results_file else default_day08
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "day08_single_agent": _day08_metrics(day08_path),
        "day09_multi_agent": analyze_traces(multi_traces_dir),
        "analysis": {
            "routing_visibility": "Day 09 ghi route_reason, workers_called và worker_io_logs; Day 08 không có routing.",
            "latency_note": "Chỉ tính delta khi cả hai pipeline đã lưu latency trên cùng bộ câu hỏi.",
            "quality_note": "answer_match_rate là heuristic token coverage, cần đối chiếu thủ công với grading criteria.",
            "mcp_benefit": "Capability bên ngoài được gọi qua dispatch_tool và có envelope trace thống nhất.",
        },
    }


def save_eval_report(comparison: Dict[str, Any]) -> str:
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    path = ARTIFACTS_DIR / "eval_report.json"
    path.write_text(json.dumps(comparison, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)


def _replace_auto_region(path: Path, start: str, end: str, body: str) -> None:
    if not path.is_file():
        return
    content = path.read_text(encoding="utf-8")
    if start not in content or end not in content:
        return
    prefix = content.split(start, 1)[0]
    suffix = content.split(end, 1)[1]
    path.write_text(f"{prefix}{start}\n{body.rstrip()}\n{end}{suffix}", encoding="utf-8")


def update_routing_doc(traces_dir: str = str(TRACES_DIR)) -> None:
    traces = _load_traces(traces_dir)
    if not traces:
        return
    selected, seen_routes = [], set()
    for trace in traces:
        route = trace.get("supervisor_route", "unknown")
        if route not in seen_routes:
            selected.append(trace)
            seen_routes.add(route)
    for trace in traces:
        if trace not in selected:
            selected.append(trace)
        if len(selected) >= 4:
            break
    lines = [f"Cập nhật từ {len(traces)} trace lúc {datetime.now(timezone.utc).isoformat()}."]
    for index, trace in enumerate(selected[:4], 1):
        answer = trace.get("final_answer", "").replace("\n", " ")[:240]
        tools = [call.get("tool") for call in trace.get("mcp_tools_used", [])]
        evaluation = trace.get("evaluation", {})
        lines.extend(
            [
                "",
                f"## Quyết định {index}",
                "",
                f"- Task: {trace.get('task', '')}",
                f"- Route: `{trace.get('supervisor_route', '')}`",
                f"- Route reason: `{trace.get('route_reason', '')}`",
                f"- Workers: `{trace.get('workers_called', [])}`",
                f"- MCP tools: `{tools}`",
                f"- Confidence: `{trace.get('confidence')}`; route khớp expected: `{evaluation.get('route_matches')}`",
                f"- Kết quả: {answer}",
            ]
        )
    metrics = analyze_traces(traces_dir)
    lines.extend(
        [
            "",
            "## Tổng hợp",
            "",
            f"- Routing distribution: `{metrics.get('routing_distribution')}`",
            f"- Routing accuracy: `{metrics.get('routing_accuracy')}`",
            f"- HITL rate: `{metrics.get('hitl_rate')}`",
            f"- MCP usage rate: `{metrics.get('mcp_usage_rate')}`",
        ]
    )
    _replace_auto_region(
        ROUTING_DOC,
        "<!-- AUTO_ROUTING_START -->",
        "<!-- AUTO_ROUTING_END -->",
        "\n".join(lines),
    )


def update_comparison_doc(comparison: Dict[str, Any]) -> None:
    single = comparison.get("day08_single_agent", {})
    multi = comparison.get("day09_multi_agent", {})
    quality = single.get("quality_scores", {}) if isinstance(single, dict) else {}
    day08_recall = quality.get("context_recall")
    day08_recall = round(day08_recall / 5, 3) if day08_recall is not None else None
    body = "\n".join(
        [
            f"Cập nhật: {comparison.get('generated_at')}",
            "",
            "| Metric | Day 08 single | Day 09 multi | Ghi chú |",
            "|---|---:|---:|---|",
            f"| Source/context recall | {day08_recall} | {multi.get('avg_source_recall')} | Chuẩn hóa Day 08 từ thang 1-5 về 0-1 |",
            f"| Abstain rate | {single.get('abstain_rate')} | {multi.get('abstain_rate')} | Tỷ lệ câu trả lời không đủ dữ liệu |",
            f"| Avg latency (ms) | {single.get('avg_latency_ms')} | {multi.get('avg_latency_ms')} | Day 08 là N/A nếu chưa instrument |",
            f"| Routing accuracy | N/A | {multi.get('routing_accuracy')} | Day 08 không có supervisor |",
            f"| Multi-hop accuracy | N/A | {multi.get('multi_hop_accuracy')} | Heuristic trên test_type multi |",
            f"| MCP usage rate | N/A | {multi.get('mcp_usage_rate')} | Tool run / tổng trace |",
            "",
            f"- Routing visibility: Day 08=`False`, Day 09=`True`.",
            f"- Worker error runs Day 09: `{multi.get('worker_error_runs')}`.",
            "- Chỉ kết luận chênh lệch chất lượng khi hai phía có metric cùng định nghĩa và cùng bộ câu hỏi.",
        ]
    )
    _replace_auto_region(
        COMPARISON_DOC,
        "<!-- AUTO_COMPARISON_START -->",
        "<!-- AUTO_COMPARISON_END -->",
        body,
    )
    _replace_auto_region(
        GROUP_REPORT,
        "<!-- AUTO_DAY09_RESULTS_START -->",
        "<!-- AUTO_DAY09_RESULTS_END -->",
        body,
    )


def print_metrics(metrics: Dict[str, Any]) -> None:
    print(json.dumps(metrics, ensure_ascii=False, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description="Đánh giá trace Day 09")
    parser.add_argument("--grading", action="store_true")
    parser.add_argument("--analyze", action="store_true")
    parser.add_argument("--compare", action="store_true")
    parser.add_argument("--test-file", default=str(TEST_QUESTIONS))
    parser.add_argument("--day08-results", default="")
    args = parser.parse_args()

    if args.grading:
        output = run_grading_questions()
        return 0 if output else 1
    if args.analyze:
        print_metrics(analyze_traces())
        return 0
    if not args.compare:
        run_test_questions(args.test_file)
        update_routing_doc()
    comparison = compare_single_vs_multi(
        day08_results_file=args.day08_results or None
    )
    update_comparison_doc(comparison)
    print_metrics(comparison)
    print(f"Đã ghi {save_eval_report(comparison)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
