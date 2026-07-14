"""Day 09 - bộ điều phối Supervisor-Worker bằng Python thuần."""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional, TypedDict

from workers.policy_tool import run as policy_tool_run
from workers.retrieval import run as retrieval_run
from workers.synthesis import run as synthesis_run

ROOT = Path(__file__).resolve().parent


class AgentState(TypedDict):
    task: str
    route_reason: str
    risk_high: bool
    needs_tool: bool
    hitl_triggered: bool
    retrieved_chunks: list
    retrieved_sources: list
    policy_result: dict
    mcp_tools_used: list
    final_answer: str
    sources: list
    confidence: float
    history: list
    workers_called: list
    worker_io_logs: list
    supervisor_route: str
    latency_ms: Optional[int]
    run_id: str
    timestamp: str


def make_initial_state(task: str) -> AgentState:
    now = datetime.now(timezone.utc)
    return {
        "task": task,
        "route_reason": "",
        "risk_high": False,
        "needs_tool": False,
        "hitl_triggered": False,
        "retrieved_chunks": [],
        "retrieved_sources": [],
        "policy_result": {},
        "mcp_tools_used": [],
        "final_answer": "",
        "sources": [],
        "confidence": 0.0,
        "history": [],
        "workers_called": [],
        "worker_io_logs": [],
        "supervisor_route": "",
        "latency_ms": None,
        "run_id": f"run_{now.strftime('%Y%m%d_%H%M%S_%f')}",
        "timestamp": now.isoformat(),
    }


def supervisor_node(state: AgentState) -> AgentState:
    """Phân loại intent/risk; supervisor không tự trả lời domain knowledge."""
    task = state["task"].lower().strip()
    if not task:
        raise ValueError("task không được để trống.")

    access_signals = (
        "cấp quyền", "access", "level 1", "level 2", "level 3", "level 4",
        "contractor", "approval matrix",
    )
    policy_signals = (
        "flash sale", "license", "subscription", "kỹ thuật số", "đã kích hoạt",
        "store credit", "ngoại lệ", "phiên bản 3", "trước 01/02",
    )
    risk_signals = ("khẩn cấp", "emergency", "2am", "err-", "production", "p1")

    matched_access = [signal for signal in access_signals if signal in task]
    matched_policy = [signal for signal in policy_signals if signal in task]
    dates = [
        (int(year), int(month), int(day))
        for day, month, year in re.findall(r"\b(\d{2})/(\d{2})/(\d{4})\b", task)
    ]
    if any(date < (2026, 2, 1) for date in dates) and any(
        marker in task for marker in ("đơn", "order", "đặt")
    ):
        matched_policy.append("order_date_before_refund_v4")
    risk_matches = [signal for signal in risk_signals if signal in task]

    human_signals = (
        "human review", "chuyển cho người duyệt", "cần người thật",
        "duyệt thủ công",
    )
    if any(signal in task for signal in human_signals):
        route = "human_review"
        reason = "task yêu cầu người duyệt trực tiếp"
    elif matched_access or matched_policy:
        route = "policy_tool_worker"
        signals = matched_access + matched_policy
        reason = f"policy/access signals={signals}; cần MCP để kiểm tra capability"
    else:
        route = "retrieval_worker"
        reason = "tra cứu evidence từ knowledge base; không có policy exception signal"

    state["supervisor_route"] = route
    state["route_reason"] = reason + (f"; risk signals={risk_matches}" if risk_matches else "")
    state["needs_tool"] = route == "policy_tool_worker"
    state["risk_high"] = bool(risk_matches) or route == "human_review"
    state["history"].append(
        f"[supervisor] route={route} reason={state['route_reason']}"
    )
    return state


def route_decision(
    state: AgentState,
) -> Literal["retrieval_worker", "policy_tool_worker", "human_review"]:
    route = state.get("supervisor_route", "retrieval_worker")
    if route not in {"retrieval_worker", "policy_tool_worker", "human_review"}:
        raise ValueError(f"Route không hợp lệ: {route}")
    return route  # type: ignore[return-value]


def human_review_node(state: AgentState) -> AgentState:
    """Ghi HITL vào trace; lab không tự thay đổi quyết định hay giả lập phê duyệt."""
    state["hitl_triggered"] = True
    if "human_review" not in state["workers_called"]:
        state["workers_called"].append("human_review")
    state["history"].append(
        f"[human_review] triggered reason={state['route_reason']} confidence={state['confidence']}"
    )
    return state


def retrieval_worker_node(state: AgentState) -> AgentState:
    return retrieval_run(state)  # type: ignore[return-value]


def policy_tool_worker_node(state: AgentState) -> AgentState:
    return policy_tool_run(state)  # type: ignore[return-value]


def synthesis_worker_node(state: AgentState) -> AgentState:
    return synthesis_run(state)  # type: ignore[return-value]


def build_graph():
    """Tạo orchestrator: mọi route đều retrieve trước, policy chỉ chạy khi cần."""

    def run(state: AgentState) -> AgentState:
        started = time.perf_counter()
        state = supervisor_node(state)
        route = route_decision(state)

        if route == "human_review":
            state = human_review_node(state)
        state = retrieval_worker_node(state)
        if route == "policy_tool_worker":
            state = policy_tool_worker_node(state)
        state = synthesis_worker_node(state)

        if state["risk_high"] and state["confidence"] < 0.4:
            state = human_review_node(state)
        state["latency_ms"] = round((time.perf_counter() - started) * 1000)
        state["history"].append(f"[graph] completed latency_ms={state['latency_ms']}")
        return state

    return run


_graph = build_graph()


def run_graph(task: str) -> AgentState:
    return _graph(make_initial_state(task))


def save_trace(state: AgentState, output_dir: str = "artifacts/traces") -> str:
    directory = Path(output_dir)
    if not directory.is_absolute():
        directory = ROOT / directory
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{state['run_id']}.json"
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)


if __name__ == "__main__":
    for query in (
        "SLA xử lý ticket P1 là bao lâu?",
        "Khách hàng Flash Sale yêu cầu hoàn tiền, có được không?",
        "Contractor cần Level 3 access để khắc phục P1 khẩn cấp.",
    ):
        result = run_graph(query)
        print(f"\nQ: {query}")
        print(f"Route: {result['supervisor_route']} ({result['route_reason']})")
        print(f"Workers: {result['workers_called']}")
        print(f"A: {result['final_answer']}")
        print(f"Trace: {save_trace(result)}")
