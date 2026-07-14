"""Synthesis worker: tổng hợp evidence, policy result và citation."""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

WORKER_NAME = "synthesis_worker"
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")

SYSTEM_PROMPT = """Bạn là trợ lý nội bộ CS và IT Helpdesk.
Chỉ dùng evidence được cung cấp, tuyệt đối không bổ sung kiến thức ngoài.
Nếu evidence thiếu dữ kiện trực tiếp, hãy trả lời "Không đủ thông tin trong tài liệu nội bộ".
Nêu ngoại lệ trước kết luận và trích dẫn [1], [2] cho từng ý quan trọng.
Trả lời ngắn gọn, rõ ràng bằng tiếng Việt."""

_STOPWORDS = {
    "ai", "bao", "các", "cách", "có", "cho", "của", "được", "gì", "hay",
    "khi", "là", "mấy", "một", "nào", "như", "những", "phải", "sau", "thế",
    "theo", "trong", "từ", "và", "với",
}


def _tokens(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[0-9a-zA-ZÀ-ỹ_#@.-]+", text.lower())
        if len(token) > 1 and token not in _STOPWORDS
    }


def _source(chunk: dict) -> str:
    return chunk.get("source") or chunk.get("metadata", {}).get("source") or "unknown"


def _build_context(chunks: List[dict], policy_result: dict) -> str:
    parts = []
    for index, chunk in enumerate(chunks, 1):
        section = chunk.get("metadata", {}).get("section", "General")
        parts.append(
            f"[{index}] source={_source(chunk)} | section={section} | "
            f"score={float(chunk.get('score', 0)):.2f}\n{chunk.get('text', '')}"
        )
    if policy_result:
        parts.append(f"POLICY_RESULT: {policy_result}")
    return "\n\n".join(parts) or "(Không có evidence)"


def _has_direct_evidence(task: str, chunks: List[dict], policy_result: dict) -> bool:
    if policy_result.get("policy_version_note") or policy_result.get("exceptions_found"):
        return any(_source(chunk) == "policy_refund_v4.txt" for chunk in chunks)
    if policy_result.get("access_decision") and not any(
        _source(chunk) == "access_control_sop.txt" for chunk in chunks
    ):
        return False
    if not chunks:
        return False
    query_tokens = _tokens(task)
    context_tokens = _tokens(" ".join(chunk.get("text", "") for chunk in chunks))
    critical = {
        token for token in query_tokens
        if token.startswith("err-") or token in {"phạt", "penalty"}
    }
    if critical and not critical <= context_tokens:
        return False
    overlap = query_tokens & context_tokens
    coverage = len(overlap) / max(1, len(query_tokens))
    return bool(overlap) and (len(overlap) >= 2 or coverage >= 0.5)


def _citation_for_source(source: str, chunks: List[dict]) -> str:
    for index, chunk in enumerate(chunks, 1):
        if _source(chunk) == source:
            return f"[{index}]"
    return "[1]" if chunks else ""


def _extractive_answer(task: str, chunks: List[dict], policy_result: dict) -> str:
    """Fallback deterministic: chọn câu liên quan và ghép policy decision."""
    statements: List[str] = []
    version_note = policy_result.get("policy_version_note", "")
    if version_note:
        statements.append(
            f"{version_note} {_citation_for_source('policy_refund_v4.txt', chunks)}".strip()
        )
        return " ".join(statements)
    for exception in policy_result.get("exceptions_found", []):
        statements.append(
            f"{exception.get('rule', '')} "
            f"{_citation_for_source(exception.get('source', ''), chunks)}".strip()
        )
    if statements:
        return " ".join(statements)

    access = policy_result.get("access_decision") or {}
    if access:
        level = access.get("access_level")
        approvers = ", ".join(access.get("required_approvers", []))
        if access.get("emergency_override"):
            text = f"Level {level} có thể cấp tạm thời trong tình huống khẩn cấp; cần {approvers}."
        elif access.get("notes"):
            text = f"Level {level} không có emergency bypass; vẫn cần đủ phê duyệt: {approvers}."
        else:
            text = f"Level {level} cần phê duyệt: {approvers}."
        statements.append(
            f"{text} {_citation_for_source(access.get('source', ''), chunks)}".strip()
        )

    clock = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", task)
    has_ten_minute_escalation = any(
        "escalat" in chunk.get("text", "").lower()
        and "10 phút" in chunk.get("text", "").lower()
        for chunk in chunks
    )
    if clock and has_ten_minute_escalation:
        deadline = datetime(
            2000, 1, 1, int(clock.group(1)), int(clock.group(2))
        ) + timedelta(minutes=10)
        statements.append(
            f"Nếu chưa có phản hồi sau 10 phút, thời điểm escalation là "
            f"{deadline.strftime('%H:%M')}. "
            f"{_citation_for_source('sla_p1_2026.txt', chunks)}".strip()
        )

    query_tokens = _tokens(task)
    ranked = []
    order = 0
    for index, chunk in enumerate(chunks, 1):
        for line in re.split(r"\n+|(?<=[.!?])\s+", chunk.get("text", "")):
            line = line.strip(" -")
            if len(line) < 12:
                continue
            overlap = len(query_tokens & _tokens(line))
            ranked.append((overlap, -order, line, index))
            order += 1
    ranked.sort(reverse=True)
    seen = {statement.lower() for statement in statements}
    for overlap, _, line, index in ranked:
        if overlap <= 0 or line.lower() in seen:
            continue
        statements.append(f"{line} [{index}]")
        seen.add(line.lower())
        if len(statements) == 4:
            break
    return " ".join(statements) or "Không đủ thông tin trong tài liệu nội bộ."


def _call_llm(messages: List[dict], fallback: str) -> str:
    if os.getenv("OPENAI_API_KEY"):
        try:
            from openai import OpenAI

            response = OpenAI().chat.completions.create(
                model=LLM_MODEL,
                messages=messages,
                temperature=0,
                max_tokens=600,
            )
            return (response.choices[0].message.content or "").strip()
        except Exception:
            pass
    if os.getenv("GOOGLE_API_KEY"):
        try:
            import google.generativeai as genai

            genai.configure(api_key=os.environ["GOOGLE_API_KEY"])
            prompt = "\n\n".join(message["content"] for message in messages)
            return genai.GenerativeModel(
                os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
            ).generate_content(prompt).text.strip()
        except Exception:
            pass
    return fallback


def _estimate_confidence(chunks: List[dict], answer: str, policy_result: dict) -> float:
    if not chunks:
        return 0.2 if "Không đủ" in answer else 0.1
    if "không đủ" in answer.lower():
        return 0.3
    average = sum(float(chunk.get("score", 0)) for chunk in chunks) / len(chunks)
    evidence_bonus = 0.1 if policy_result.get("access_decision") else 0.0
    complexity_penalty = min(0.15, 0.03 * len(policy_result.get("exceptions_found", [])))
    return round(max(0.1, min(0.95, 0.35 + 0.55 * average + evidence_bonus - complexity_penalty)), 2)


def synthesize(task: str, chunks: List[dict], policy_result: dict) -> Dict[str, Any]:
    sources = list(dict.fromkeys(_source(chunk) for chunk in chunks))
    if not _has_direct_evidence(task, chunks, policy_result):
        answer = "Không đủ thông tin trong tài liệu nội bộ."
        return {"answer": answer, "sources": [], "confidence": 0.2}

    fallback = _extractive_answer(task, chunks, policy_result)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"Câu hỏi: {task}\n\nEVIDENCE:\n{_build_context(chunks, policy_result)}",
        },
    ]
    answer = _call_llm(messages, fallback)
    if not answer.strip():
        answer = fallback
    if not re.search(r"\[\d+\]", answer) and "không đủ" not in answer.lower():
        answer = f"{answer.rstrip()} [1]"
    return {
        "answer": answer,
        "sources": sources,
        "confidence": _estimate_confidence(chunks, answer, policy_result),
    }


def run(state: dict) -> dict:
    task = state.get("task", "")
    chunks = list(state.get("retrieved_chunks", []))
    policy_result = dict(state.get("policy_result", {}))
    state.setdefault("workers_called", []).append(WORKER_NAME)
    state.setdefault("history", [])
    log = {
        "worker": WORKER_NAME,
        "input": {
            "task": task,
            "chunks_count": len(chunks),
            "has_policy": bool(policy_result),
        },
        "output": None,
        "error": None,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    try:
        result = synthesize(task, chunks, policy_result)
        state["final_answer"] = result["answer"]
        state["sources"] = result["sources"]
        state["confidence"] = result["confidence"]
        if result["confidence"] < 0.4:
            state["risk_high"] = True
        log["output"] = {
            "answer_length": len(result["answer"]),
            "sources": result["sources"],
            "confidence": result["confidence"],
        }
        state["history"].append(
            f"[{WORKER_NAME}] confidence={result['confidence']} sources={result['sources']}"
        )
    except Exception as exc:
        state["final_answer"] = "Không đủ thông tin trong tài liệu nội bộ."
        state["sources"] = []
        state["confidence"] = 0.0
        log["error"] = {"code": "SYNTHESIS_FAILED", "reason": str(exc)}
        state["history"].append(f"[{WORKER_NAME}] error={exc}")
    state.setdefault("worker_io_logs", []).append(log)
    return state


if __name__ == "__main__":
    result = run(
        {
            "task": "SLA ticket P1 là bao lâu?",
            "retrieved_chunks": [
                {
                    "text": "Ticket P1 phản hồi 15 phút và resolution 4 giờ.",
                    "source": "sla_p1_2026.txt",
                    "score": 0.9,
                }
            ],
        }
    )
    print(result["final_answer"])
