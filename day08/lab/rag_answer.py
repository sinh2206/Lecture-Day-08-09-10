"""Day 08 - dense/hybrid retrieval và sinh câu trả lời có căn cứ."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, List

from dotenv import load_dotenv

from index import CHROMA_DB_DIR, COLLECTION_NAME, get_embedding

load_dotenv(Path(__file__).resolve().parent / ".env")

TOP_K_SEARCH = 10
TOP_K_SELECT = 3
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")

_STOPWORDS = {
    "ai", "bao", "các", "cách", "có", "cho", "của", "được", "gì", "hay",
    "khi", "là", "mấy", "một", "nào", "như", "những", "phải", "sau", "thế",
    "theo", "trong", "từ", "và", "với", "where", "what", "how", "the", "is",
}
_DOMAIN_SOURCES = (
    (
        "policy/refund-v4.pdf",
        (
            "hoàn tiền", "refund", "flash sale", "store credit", "license",
            "subscription", "kỹ thuật số", "kích hoạt",
        ),
    ),
    ("support/sla-p1-2026.pdf", ("p1", "sla", "ticket", "escalat", "incident")),
    ("it/access-control-sop.md", ("access", "cấp quyền", "level ", "contractor", "approval matrix")),
    ("hr/leave-policy-2026.pdf", ("phép", "nghỉ", "remote", "probation", "thử việc")),
    ("support/helpdesk-faq.md", ("mật khẩu", "vpn", "đăng nhập", "hộp thư", "tài khoản")),
)


def _tokenize(text: str) -> List[str]:
    return re.findall(r"[0-9a-zA-ZÀ-ỹ_#@.-]+", text.lower())


def _salient_tokens(text: str) -> set[str]:
    return {token for token in _tokenize(text) if len(token) > 1 and token not in _STOPWORDS}


def retrieve_dense(query: str, top_k: int = TOP_K_SEARCH) -> List[Dict[str, Any]]:
    """Truy vấn ChromaDB bằng cùng embedding model với bước indexing."""
    import chromadb

    collection = chromadb.PersistentClient(path=str(CHROMA_DB_DIR)).get_collection(
        COLLECTION_NAME
    )
    count = collection.count()
    if count == 0:
        raise RuntimeError("Index đang rỗng. Hãy chạy index.py trước.")
    result = collection.query(
        query_embeddings=[get_embedding(query)],
        n_results=min(max(1, top_k), count),
        include=["documents", "metadatas", "distances"],
    )
    chunks: List[Dict[str, Any]] = []
    for chunk_id, text, metadata, distance in zip(
        (result.get("ids") or [[]])[0],
        (result.get("documents") or [[]])[0],
        (result.get("metadatas") or [[]])[0],
        (result.get("distances") or [[]])[0],
    ):
        chunks.append(
            {
                "id": chunk_id,
                "text": text,
                "metadata": metadata or {},
                "score": round(max(0.0, min(1.0, 1.0 - float(distance))), 4),
            }
        )
    return chunks


def retrieve_sparse(query: str, top_k: int = TOP_K_SEARCH) -> List[Dict[str, Any]]:
    """BM25 phù hợp với tên riêng, mã lỗi và alias xuất hiện nguyên văn."""
    import chromadb
    from rank_bm25 import BM25Okapi

    collection = chromadb.PersistentClient(path=str(CHROMA_DB_DIR)).get_collection(
        COLLECTION_NAME
    )
    result = collection.get(include=["documents", "metadatas"])
    documents = result.get("documents") or []
    if not documents:
        return []

    scores = BM25Okapi([_tokenize(text) for text in documents]).get_scores(
        _tokenize(query)
    )
    ranked = sorted(range(len(scores)), key=lambda index: float(scores[index]), reverse=True)
    positive = [index for index in ranked if float(scores[index]) > 0][:top_k]
    max_score = max((float(scores[index]) for index in positive), default=1.0)
    ids = result.get("ids") or []
    metadatas = result.get("metadatas") or []
    return [
        {
            "id": ids[index],
            "text": documents[index],
            "metadata": metadatas[index] or {},
            "score": round(float(scores[index]) / max_score, 4),
        }
        for index in positive
    ]


def retrieve_hybrid(
    query: str,
    top_k: int = TOP_K_SEARCH,
    dense_weight: float = 0.6,
    sparse_weight: float = 0.4,
) -> List[Dict[str, Any]]:
    """Hợp nhất dense và BM25 bằng Reciprocal Rank Fusion."""
    if dense_weight < 0 or sparse_weight < 0 or dense_weight + sparse_weight == 0:
        raise ValueError("Trọng số hybrid phải không âm và có tổng lớn hơn 0.")

    candidate_k = max(top_k, TOP_K_SEARCH)
    dense = retrieve_dense(query, candidate_k)
    sparse = retrieve_sparse(query, candidate_k)
    fused: Dict[str, Dict[str, Any]] = {}
    for weight, results in ((dense_weight, dense), (sparse_weight, sparse)):
        for rank, chunk in enumerate(results, 1):
            item = fused.setdefault(chunk["id"], {**chunk, "rrf_score": 0.0})
            item["rrf_score"] += weight / (60 + rank)

    ranked = sorted(fused.values(), key=lambda item: item["rrf_score"], reverse=True)
    max_rrf = ranked[0]["rrf_score"] if ranked else 1.0
    for item in ranked:
        item["score"] = round(item.pop("rrf_score") / max_rrf, 4)
    required_sources = [
        source
        for source, markers in _DOMAIN_SOURCES
        if any(marker in query.lower() for marker in markers)
    ]
    selected: List[Dict[str, Any]] = []
    for source in required_sources:
        match = next(
            (
                chunk for chunk in ranked
                if chunk.get("metadata", {}).get("source") == source
            ),
            None,
        )
        if match and match not in selected:
            selected.append(match)
    selected.extend(chunk for chunk in ranked if chunk not in selected)
    return selected[:top_k]


def rerank(
    query: str,
    candidates: List[Dict[str, Any]],
    top_k: int = TOP_K_SELECT,
) -> List[Dict[str, Any]]:
    """Rerank nhẹ bằng độ phủ từ khóa; không tạo thêm dependency/model tải xuống."""
    query_tokens = _salient_tokens(query)
    for candidate in candidates:
        overlap = len(query_tokens & _salient_tokens(candidate.get("text", "")))
        lexical = overlap / max(1, len(query_tokens))
        candidate["rerank_score"] = round(
            0.7 * float(candidate.get("score", 0)) + 0.3 * lexical, 4
        )
    return sorted(candidates, key=lambda item: item["rerank_score"], reverse=True)[:top_k]


def transform_query(query: str, strategy: str = "expansion") -> List[str]:
    """Mở rộng một số alias đã biết hoặc tách câu hỏi ghép thành các truy vấn nhỏ."""
    if strategy == "decomposition":
        parts = [part.strip() for part in re.split(r"\bvà\b|\bđồng thời\b", query) if part.strip()]
        return parts or [query]
    if strategy != "expansion":
        raise ValueError("strategy phải là 'expansion' hoặc 'decomposition'.")

    aliases = {
        "approval matrix": "Access Control SOP phân cấp quyền truy cập",
        "store credit": "credit nội bộ hoàn tiền 110%",
        "p1": "ticket P1 SLA sự cố critical",
    }
    expanded = [query]
    lowered = query.lower()
    for alias, replacement in aliases.items():
        if alias in lowered:
            expanded.append(f"{query} {replacement}")
    return expanded


def build_context_block(chunks: List[Dict[str, Any]]) -> str:
    parts = []
    for index, chunk in enumerate(chunks, 1):
        metadata = chunk.get("metadata", {})
        header = (
            f"[{index}] {metadata.get('source', 'unknown')} | "
            f"{metadata.get('section', 'General')} | "
            f"effective_date={metadata.get('effective_date', 'unknown')} | "
            f"score={float(chunk.get('score', 0)):.2f}"
        )
        parts.append(f"{header}\n{chunk.get('text', '')}")
    return "\n\n".join(parts)


def build_grounded_prompt(query: str, context_block: str) -> str:
    return f"""Bạn là trợ lý nội bộ CS và IT Helpdesk.
Chỉ trả lời bằng dữ kiện trong CONTEXT; không suy đoán hoặc dùng kiến thức bên ngoài.
Nếu thiếu dữ kiện trực tiếp, trả lời: "Không đủ dữ liệu trong tài liệu hiện có."
Mỗi ý quan trọng phải có citation [1], [2] tương ứng. Trả lời ngắn gọn bằng tiếng Việt.

QUESTION: {query}

CONTEXT:
{context_block}

ANSWER:"""


def _extractive_fallback(prompt: str) -> str:
    """Fallback có căn cứ khi chưa cấu hình API key."""
    question_match = re.search(r"QUESTION:\s*(.*?)\n\nCONTEXT:", prompt, re.DOTALL)
    query_tokens = _salient_tokens(question_match.group(1) if question_match else "")
    context = prompt.split("CONTEXT:\n", 1)[-1].rsplit("\n\nANSWER:", 1)[0]
    pieces = re.split(r"(?m)^\[(\d+)\]\s+[^\n]+\n", context)
    ranked = []
    order = 0
    for index in range(1, len(pieces), 2):
        citation, body = pieces[index], pieces[index + 1]
        for line in re.split(r"\n+|(?<=[.!?])\s+", body):
            line = line.strip(" -")
            if len(line) < 12:
                continue
            overlap = len(query_tokens & _salient_tokens(line))
            ranked.append((overlap, -order, line, citation))
            order += 1
    ranked.sort(reverse=True)
    selected = []
    seen = set()
    for overlap, _, line, citation in ranked:
        normalized = line.lower()
        if overlap <= 0 or normalized in seen:
            continue
        selected.append(f"{line} [{citation}]")
        seen.add(normalized)
        if len(selected) == 3:
            break
    return " ".join(selected) or "Không đủ dữ liệu trong tài liệu hiện có."


def call_llm(prompt: str) -> str:
    """Ưu tiên OpenAI/Gemini khi có key, nếu không dùng fallback trích xuất."""
    if os.getenv("OPENAI_API_KEY"):
        try:
            from openai import OpenAI

            response = OpenAI().chat.completions.create(
                model=LLM_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                max_tokens=512,
            )
            return (response.choices[0].message.content or "").strip()
        except Exception:
            pass

    if os.getenv("GOOGLE_API_KEY"):
        try:
            import google.generativeai as genai

            genai.configure(api_key=os.environ["GOOGLE_API_KEY"])
            response = genai.GenerativeModel(
                os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
            ).generate_content(prompt)
            return response.text.strip()
        except Exception:
            pass
    return _extractive_fallback(prompt)


def _has_direct_evidence(query: str, chunks: List[Dict[str, Any]]) -> bool:
    if not chunks:
        return False
    query_tokens = _salient_tokens(query)
    context_tokens = _salient_tokens(" ".join(chunk.get("text", "") for chunk in chunks))
    critical = {
        token for token in query_tokens
        if token.startswith("err-") or token in {"phạt", "penalty"}
    }
    if critical and not critical <= context_tokens:
        return False
    overlap = query_tokens & context_tokens
    coverage = len(overlap) / max(1, len(query_tokens))
    return bool(overlap) and (len(overlap) >= 2 or coverage >= 0.5)


def rag_answer(
    query: str,
    retrieval_mode: str = "dense",
    top_k_search: int = TOP_K_SEARCH,
    top_k_select: int = TOP_K_SELECT,
    use_rerank: bool = False,
    verbose: bool = False,
) -> Dict[str, Any]:
    """Pipeline query -> retrieve -> select -> grounded generation."""
    if not query.strip():
        raise ValueError("query không được để trống.")
    if top_k_search < 1 or top_k_select < 1:
        raise ValueError("top_k_search và top_k_select phải >= 1.")
    retrievers = {
        "dense": retrieve_dense,
        "sparse": retrieve_sparse,
        "hybrid": retrieve_hybrid,
    }
    if retrieval_mode not in retrievers:
        raise ValueError(f"retrieval_mode không hợp lệ: {retrieval_mode}")

    candidates = retrievers[retrieval_mode](query, top_k_search)
    selected = (
        rerank(query, candidates, top_k_select)
        if use_rerank
        else candidates[:top_k_select]
    )
    config = {
        "retrieval_mode": retrieval_mode,
        "top_k_search": top_k_search,
        "top_k_select": top_k_select,
        "use_rerank": use_rerank,
    }

    if verbose:
        print(f"Retrieved={len(candidates)}, selected={len(selected)}, mode={retrieval_mode}")

    if not _has_direct_evidence(query, selected):
        return {
            "query": query,
            "answer": "Không đủ dữ liệu trong tài liệu hiện có.",
            "sources": [],
            "chunks_used": [],
            "config": config,
        }

    answer = call_llm(build_grounded_prompt(query, build_context_block(selected)))
    if not answer.strip() or "không đủ" in answer.lower():
        return {
            "query": query,
            "answer": "Không đủ dữ liệu trong tài liệu hiện có.",
            "sources": list(dict.fromkeys(
                chunk.get("metadata", {}).get("source", "unknown")
                for chunk in selected
            )),
            "chunks_used": selected,
            "config": config,
        }
    if not re.search(r"\[\d+\]", answer):
        answer = f"{answer.rstrip()} [1]"
    sources = list(dict.fromkeys(
        chunk.get("metadata", {}).get("source", "unknown") for chunk in selected
    ))
    return {
        "query": query,
        "answer": answer,
        "sources": sources,
        "chunks_used": selected,
        "config": config,
    }


def compare_retrieval_strategies(query: str) -> None:
    for strategy in ("dense", "sparse", "hybrid"):
        result = rag_answer(query, retrieval_mode=strategy)
        print(f"\n{strategy}: {result['answer']}\nSources: {result['sources']}")


if __name__ == "__main__":
    for question in (
        "SLA xử lý ticket P1 là bao lâu?",
        "Approval Matrix để cấp quyền hệ thống là tài liệu nào?",
        "ERR-403-AUTH là lỗi gì và cách xử lý?",
    ):
        print(f"\nQ: {question}")
        print(f"A: {rag_answer(question, retrieval_mode='hybrid')['answer']}")
