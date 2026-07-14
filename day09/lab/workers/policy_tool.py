"""Policy/tool worker: phân tích ngoại lệ và gọi capability qua MCP dispatcher."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, List

WORKER_NAME = "policy_tool_worker"


def _call_mcp_tool(tool_name: str, tool_input: dict) -> dict:
    """Bao mọi MCP call trong envelope thống nhất để trace được input/output/error."""
    try:
        from mcp_server import dispatch_tool

        output = dispatch_tool(tool_name, tool_input)
        error = output if isinstance(output, dict) and output.get("error") else None
        return {
            "tool": tool_name,
            "input": tool_input,
            "output": None if error else output,
            "error": error,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as exc:
        return {
            "tool": tool_name,
            "input": tool_input,
            "output": None,
            "error": {"code": "MCP_CALL_FAILED", "reason": str(exc)},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


def _sources(chunks: List[dict]) -> List[str]:
    return list(
        dict.fromkeys(
            chunk.get("source")
            or chunk.get("metadata", {}).get("source")
            or "unknown"
            for chunk in chunks
        )
    )


def _append_exception(items: List[dict], item: dict) -> None:
    if item["type"] not in {existing["type"] for existing in items}:
        items.append(item)


def analyze_policy(task: str, chunks: List[dict]) -> Dict[str, Any]:
    """Phân tích rule-based chỉ với các quy tắc có trong corpus của bài lab."""
    lowered = task.lower()
    exceptions: List[dict] = []

    rules = (
        (
            "flash_sale_exception",
            "flash sale",
            "Đơn hàng Flash Sale không được hoàn tiền theo Điều 3.",
        ),
        (
            "digital_product_exception",
            ("license key", "subscription", "kỹ thuật số"),
            "Sản phẩm kỹ thuật số (license key, subscription) không được hoàn tiền.",
        ),
        (
            "activated_product_exception",
            ("đã kích hoạt", "đã đăng ký", "đã sử dụng"),
            "Sản phẩm đã kích hoạt hoặc đăng ký tài khoản không được hoàn tiền.",
        ),
    )
    for exception_type, markers, rule in rules:
        markers = (markers,) if isinstance(markers, str) else markers
        if any(marker in lowered for marker in markers):
            _append_exception(
                exceptions,
                {
                    "type": exception_type,
                    "rule": rule,
                    "source": "policy_refund_v4.txt",
                },
            )

    temporal_note = ""
    dates = [
        (int(year), int(month), int(day))
        for day, month, year in re.findall(r"\b(\d{2})/(\d{2})/(\d{4})\b", lowered)
    ]
    if dates and any(marker in lowered for marker in ("đặt", "đơn", "order")):
        if any(date < (2026, 2, 1) for date in dates):
            temporal_note = (
                "Đơn đặt trước 01/02/2026 thuộc chính sách v3; corpus hiện không có v3 "
                "nên cần CS xác nhận thay vì áp dụng v4."
            )
    elif "trước 01/02/2026" in lowered:
        temporal_note = (
            "Đơn đặt trước 01/02/2026 thuộc chính sách v3; corpus hiện không có v3."
        )

    is_access = any(
        marker in lowered
        for marker in ("cấp quyền", "access", "level 1", "level 2", "level 3", "level 4")
    )
    is_refund = any(marker in lowered for marker in ("hoàn tiền", "refund", "store credit"))
    policy_name = "access_control_sop" if is_access else (
        "refund_policy_v4" if is_refund else "internal_policy"
    )
    applies = not exceptions and not temporal_note
    return {
        "policy_applies": applies,
        "policy_name": policy_name,
        "exceptions_found": exceptions,
        "source": _sources(chunks),
        "policy_version_note": temporal_note,
        "explanation": "Kết quả được suy ra từ evidence và rule đã công bố trong corpus.",
    }


def _merge_chunks(current: List[dict], incoming: List[dict]) -> List[dict]:
    merged: List[dict] = []
    seen = set()
    for chunk in current + incoming:
        key = (chunk.get("source"), chunk.get("text", "").strip())
        if key not in seen:
            seen.add(key)
            merged.append(chunk)
    return merged


def run(state: dict) -> dict:
    task = state.get("task", "")
    chunks = list(state.get("retrieved_chunks", []))
    needs_tool = bool(state.get("needs_tool", False))
    state.setdefault("workers_called", []).append(WORKER_NAME)
    state.setdefault("history", [])
    state.setdefault("mcp_tools_used", [])
    log = {
        "worker": WORKER_NAME,
        "input": {
            "task": task,
            "chunks_count": len(chunks),
            "needs_tool": needs_tool,
        },
        "output": None,
        "error": None,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    try:
        if needs_tool:
            search_call = _call_mcp_tool("search_kb", {"query": task, "top_k": 5})
            state["mcp_tools_used"].append(search_call)
            if search_call.get("output"):
                chunks = _merge_chunks(chunks, search_call["output"].get("chunks", []))

        policy_result = analyze_policy(task, chunks)
        lowered = task.lower()
        level_match = re.search(r"level\s*([1-3])", lowered)
        if needs_tool and level_match:
            access_call = _call_mcp_tool(
                "check_access_permission",
                {
                    "access_level": int(level_match.group(1)),
                    "requester_role": "contractor" if "contractor" in lowered else "employee",
                    "is_emergency": any(
                        marker in lowered for marker in ("emergency", "khẩn cấp", "p1", "2am")
                    ),
                },
            )
            state["mcp_tools_used"].append(access_call)
            if access_call.get("output"):
                policy_result["access_decision"] = access_call["output"]

        if needs_tool and (re.search(r"\b\d{1,2}:\d{2}\b", lowered) or "p1-latest" in lowered):
            state["mcp_tools_used"].append(
                _call_mcp_tool("get_ticket_info", {"ticket_id": "P1-LATEST"})
            )

        state["retrieved_chunks"] = chunks
        state["retrieved_sources"] = _sources(chunks)
        state["policy_result"] = policy_result
        log["output"] = {
            "policy_applies": policy_result["policy_applies"],
            "exceptions_count": len(policy_result["exceptions_found"]),
            "mcp_calls": len(state["mcp_tools_used"]),
        }
        state["history"].append(
            f"[{WORKER_NAME}] applies={policy_result['policy_applies']} "
            f"exceptions={len(policy_result['exceptions_found'])} "
            f"mcp_calls={len(state['mcp_tools_used'])}"
        )
    except Exception as exc:
        state["policy_result"] = {"error": str(exc)}
        log["error"] = {"code": "POLICY_CHECK_FAILED", "reason": str(exc)}
        state["history"].append(f"[{WORKER_NAME}] error={exc}")
    state.setdefault("worker_io_logs", []).append(log)
    return state


if __name__ == "__main__":
    result = run(
        {
            "task": "Contractor cần Level 3 access để sửa P1 khẩn cấp.",
            "needs_tool": True,
        }
    )
    print(result.get("policy_result"))
