"""Retrieval worker: tự lập chỉ mục corpus và trả evidence từ ChromaDB."""

from __future__ import annotations

import hashlib
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

WORKER_NAME = "retrieval_worker"
DEFAULT_TOP_K = int(os.getenv("RETRIEVAL_TOP_K", "5"))
DOCS_DIR = ROOT / "data" / "docs"
_db_value = os.getenv("DAY09_CHROMA_DB_PATH", os.getenv("CHROMA_DB_PATH", "chroma_db"))
DB_DIR = Path(_db_value) if Path(_db_value).is_absolute() else ROOT / _db_value
COLLECTION_NAME = os.getenv(
    "DAY09_CHROMA_COLLECTION", os.getenv("CHROMA_COLLECTION", "day09_docs")
)
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2")

_model = None
_index_ready = False
_HEADING_RE = re.compile(r"^===\s*(.*?)\s*===\s*$", re.MULTILINE)
_DOMAIN_SOURCES = (
    (
        "policy_refund_v4.txt",
        (
            "hoàn tiền", "refund", "flash sale", "store credit", "license",
            "subscription", "kỹ thuật số", "kích hoạt",
        ),
    ),
    ("sla_p1_2026.txt", ("p1", "sla", "ticket", "escalat", "incident")),
    ("access_control_sop.txt", ("access", "cấp quyền", "level ", "contractor")),
    ("hr_leave_policy.txt", ("phép", "nghỉ", "remote", "probation", "thử việc")),
    ("it_helpdesk_faq.txt", ("mật khẩu", "vpn", "đăng nhập", "hộp thư", "tài khoản")),
)


def _get_embedding_fn():
    """Trả về hàm embedding local ổn định và dùng chung cho index/query."""
    global _model
    if _model is None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise RuntimeError(
                "Chưa cài sentence-transformers. Hãy cài requirements.txt trước."
            ) from exc
        _model = SentenceTransformer(EMBEDDING_MODEL)

    def embed(text: str) -> List[float]:
        return _model.encode(text, normalize_embeddings=True).tolist()

    return embed


def _document_chunks(path: Path) -> List[Dict[str, Any]]:
    """Chia theo section để giữ nguyên một điều khoản trong mỗi evidence chunk."""
    raw = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    canonical_match = re.search(r"(?m)^Source:\s*(.+)$", raw)
    canonical_source = canonical_match.group(1).strip() if canonical_match else path.name
    matches = list(_HEADING_RE.finditer(raw))
    chunks: List[Dict[str, Any]] = []

    preamble_end = matches[0].start() if matches else len(raw)
    preamble_lines = [
        line for line in raw[:preamble_end].splitlines()
        if line.strip() and not re.match(r"^(Source|Department|Effective Date|Access):", line)
    ]
    if preamble_lines:
        chunks.append(
            {
                "text": "\n".join(preamble_lines),
                "source": path.name,
                "metadata": {
                    "source": path.name,
                    "canonical_source": canonical_source,
                    "section": "General",
                },
            }
        )

    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(raw)
        body = raw[match.end() : end].strip()
        if body:
            chunks.append(
                {
                    "text": body,
                    "source": path.name,
                    "metadata": {
                        "source": path.name,
                        "canonical_source": canonical_source,
                        "section": match.group(1).strip(),
                    },
                }
            )
    return chunks


def _chunk_id(chunk: Dict[str, Any]) -> str:
    raw = f"{chunk['source']}|{chunk['metadata']['section']}|{chunk['text']}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _get_collection():
    import chromadb

    DB_DIR.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(DB_DIR))
    return client.get_or_create_collection(
        COLLECTION_NAME, metadata={"hnsw:space": "cosine"}
    )


def _ensure_index() -> Any:
    """Upsert snapshot corpus đúng một lần trong mỗi process và prune id cũ."""
    global _index_ready
    collection = _get_collection()
    if _index_ready:
        return collection

    files = sorted(DOCS_DIR.glob("*.txt"))
    if not files:
        raise FileNotFoundError(f"Không tìm thấy tài liệu trong {DOCS_DIR}")
    chunks = [chunk for path in files for chunk in _document_chunks(path)]
    ids = [_chunk_id(chunk) for chunk in chunks]
    embed = _get_embedding_fn()
    embeddings = [embed(chunk["text"]) for chunk in chunks]

    previous_ids = set(collection.get(include=[]).get("ids") or [])
    stale_ids = sorted(previous_ids - set(ids))
    if stale_ids:
        collection.delete(ids=stale_ids)
    collection.upsert(
        ids=ids,
        embeddings=embeddings,
        documents=[chunk["text"] for chunk in chunks],
        metadatas=[chunk["metadata"] for chunk in chunks],
    )
    _index_ready = True
    return collection


def retrieve_dense(query: str, top_k: int = DEFAULT_TOP_K) -> List[Dict[str, Any]]:
    if not query.strip():
        return []
    top_k = max(1, top_k)
    collection = _ensure_index()
    count = collection.count()
    if count == 0:
        return []
    result = collection.query(
        query_embeddings=[_get_embedding_fn()(query)],
        n_results=count,
        include=["documents", "distances", "metadatas"],
    )
    ranked = [
        {
            "text": text,
            "source": (metadata or {}).get("source", "unknown"),
            "score": round(max(0.0, min(1.0, 1.0 - float(distance))), 4),
            "metadata": metadata or {},
        }
        for text, distance, metadata in zip(
            (result.get("documents") or [[]])[0],
            (result.get("distances") or [[]])[0],
            (result.get("metadatas") or [[]])[0],
        )
    ]
    required_sources = [
        source
        for source, markers in _DOMAIN_SOURCES
        if any(marker in query.lower() for marker in markers)
    ]
    selected: List[Dict[str, Any]] = []
    for source in required_sources:
        match = next((chunk for chunk in ranked if chunk["source"] == source), None)
        if match and match not in selected:
            selected.append(match)
    selected.extend(chunk for chunk in ranked if chunk not in selected)
    return selected[:top_k]


def run(state: dict) -> dict:
    """Cập nhật state theo worker contract và luôn ghi worker_io_logs."""
    task = state.get("task", "")
    top_k = int(state.get("retrieval_top_k", DEFAULT_TOP_K))
    state.setdefault("workers_called", []).append(WORKER_NAME)
    state.setdefault("history", [])
    log = {
        "worker": WORKER_NAME,
        "input": {"task": task, "top_k": top_k},
        "output": None,
        "error": None,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    try:
        chunks = retrieve_dense(task, top_k)
        sources = list(dict.fromkeys(chunk["source"] for chunk in chunks))
        state["retrieved_chunks"] = chunks
        state["retrieved_sources"] = sources
        log["output"] = {"chunks_count": len(chunks), "sources": sources}
        state["history"].append(
            f"[{WORKER_NAME}] retrieved={len(chunks)} sources={sources}"
        )
    except Exception as exc:
        state["retrieved_chunks"] = []
        state["retrieved_sources"] = []
        log["error"] = {"code": "RETRIEVAL_FAILED", "reason": str(exc)}
        state["history"].append(f"[{WORKER_NAME}] error={exc}")
    state.setdefault("worker_io_logs", []).append(log)
    return state


if __name__ == "__main__":
    for question in (
        "SLA ticket P1 là bao lâu?",
        "Ai phê duyệt cấp quyền Level 3?",
    ):
        result = run({"task": question})
        print(question, result.get("retrieved_sources"))
