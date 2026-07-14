"""Day 08 - tiền xử lý, chia đoạn, embedding và lập chỉ mục ChromaDB."""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Any, Dict, List

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

DOCS_DIR = ROOT / "data" / "docs"
CHROMA_DB_DIR = ROOT / "chroma_db"
COLLECTION_NAME = "rag_lab"
CHUNK_SIZE = 400
CHUNK_OVERLAP = 80
EMBEDDING_MODEL = os.getenv(
    "EMBEDDING_MODEL", "paraphrase-multilingual-MiniLM-L12-v2"
)

_embedding_model = None
_HEADING_RE = re.compile(r"^===\s*(.*?)\s*===\s*$", re.MULTILINE)
_META_KEYS = {
    "source": "source",
    "department": "department",
    "effective date": "effective_date",
    "access": "access",
}


def preprocess_document(raw_text: str, filepath: str) -> Dict[str, Any]:
    """Tách metadata đầu tệp và chuẩn hóa nội dung, không làm mất phần mở đầu."""
    lines = raw_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    metadata = {
        "source": Path(filepath).name,
        "section": "",
        "department": "unknown",
        "effective_date": "unknown",
        "access": "internal",
    }
    content: List[str] = []

    for index, line in enumerate(lines):
        match = re.match(r"^([^:]+):\s*(.*)$", line.strip())
        key = match.group(1).strip().lower() if match else ""
        if key in _META_KEYS:
            metadata[_META_KEYS[key]] = match.group(2).strip()
            continue
        is_title = index == 0 and line.strip() and line.strip() == line.strip().upper()
        if not is_title:
            content.append(line.rstrip())

    cleaned = "\n".join(content).strip()
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return {"text": cleaned, "metadata": metadata}


def _natural_units(text: str, limit: int) -> List[str]:
    """Tách theo đoạn/câu; chỉ cắt cứng khi một câu đơn lẻ vượt giới hạn."""
    units: List[str] = []
    for paragraph in re.split(r"\n\s*\n", text):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if len(paragraph) <= limit:
            units.append(paragraph)
            continue
        sentences = re.split(r"(?<=[.!?])\s+|(?<=:)\n", paragraph)
        for sentence in sentences:
            sentence = sentence.strip()
            while len(sentence) > limit:
                cut = sentence.rfind(" ", 0, limit + 1)
                cut = cut if cut > limit // 2 else limit
                units.append(sentence[:cut].strip())
                sentence = sentence[cut:].strip()
            if sentence:
                units.append(sentence)
    return units


def _overlap_tail(text: str, limit: int) -> str:
    tail = text[-limit:]
    first_space = tail.find(" ")
    return tail[first_space + 1 :].strip() if first_space >= 0 else tail.strip()


def _split_by_size(
    text: str,
    base_metadata: Dict[str, Any],
    section: str,
    chunk_chars: int = CHUNK_SIZE * 4,
    overlap_chars: int = CHUNK_OVERLAP * 4,
) -> List[Dict[str, Any]]:
    """Ghép các đơn vị tự nhiên thành chunk và chèn phần chồng lấn có giới hạn."""
    units = _natural_units(text, chunk_chars)
    if not units:
        return []

    texts: List[str] = []
    current = ""
    for unit in units:
        candidate = f"{current}\n\n{unit}".strip() if current else unit
        if current and len(candidate) > chunk_chars:
            texts.append(current)
            overlap = _overlap_tail(current, overlap_chars)
            current = f"{overlap}\n\n{unit}".strip()
        else:
            current = candidate
    if current:
        texts.append(current)

    return [
        {"text": chunk, "metadata": {**base_metadata, "section": section}}
        for chunk in texts
    ]


def chunk_document(doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Ưu tiên biên section, sau đó mới chia theo kích thước và đoạn văn."""
    text = doc["text"]
    metadata = dict(doc["metadata"])
    matches = list(_HEADING_RE.finditer(text))
    chunks: List[Dict[str, Any]] = []

    if not matches:
        return _split_by_size(text, metadata, "General")

    preamble = text[: matches[0].start()].strip()
    if preamble:
        chunks.extend(_split_by_size(preamble, metadata, "General"))

    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        if body:
            chunks.extend(_split_by_size(body, metadata, match.group(1).strip()))

    return chunks


def get_embedding(text: str) -> List[float]:
    """Tạo embedding local đa ngôn ngữ; model được cache trong suốt một process."""
    global _embedding_model
    if _embedding_model is None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise RuntimeError(
                "Chưa cài sentence-transformers. Hãy cài requirements.txt trước."
            ) from exc
        _embedding_model = SentenceTransformer(EMBEDDING_MODEL)
    return _embedding_model.encode(text, normalize_embeddings=True).tolist()


def _chunk_id(chunk: Dict[str, Any]) -> str:
    meta = chunk["metadata"]
    raw = f"{meta['source']}|{meta['section']}|{chunk['text']}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def build_index(docs_dir: Path = DOCS_DIR, db_dir: Path = CHROMA_DB_DIR) -> int:
    """Lập snapshot index idempotent và xóa vector không còn trong corpus."""
    try:
        import chromadb
    except ImportError as exc:
        raise RuntimeError("Chưa cài chromadb. Hãy cài requirements.txt trước.") from exc

    files = sorted(docs_dir.glob("*.txt"))
    if not files:
        raise FileNotFoundError(f"Không tìm thấy tài liệu trong {docs_dir}")

    all_chunks: List[Dict[str, Any]] = []
    for path in files:
        doc = preprocess_document(path.read_text(encoding="utf-8"), str(path))
        file_chunks = chunk_document(doc)
        print(f"{path.name}: {len(file_chunks)} chunks")
        all_chunks.extend(file_chunks)

    ids = [_chunk_id(chunk) for chunk in all_chunks]
    embeddings = [get_embedding(chunk["text"]) for chunk in all_chunks]
    metadatas = [
        {**chunk["metadata"], "chunk_index": index}
        for index, chunk in enumerate(all_chunks)
    ]

    db_dir.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(db_dir))
    collection = client.get_or_create_collection(
        COLLECTION_NAME, metadata={"hnsw:space": "cosine"}
    )
    previous_ids = set(collection.get(include=[]).get("ids") or [])
    stale_ids = sorted(previous_ids - set(ids))
    if stale_ids:
        collection.delete(ids=stale_ids)
    collection.upsert(
        ids=ids,
        embeddings=embeddings,
        documents=[chunk["text"] for chunk in all_chunks],
        metadatas=metadatas,
    )
    print(f"Đã upsert {len(ids)} chunks; xóa {len(stale_ids)} chunks cũ.")
    return len(ids)


def list_chunks(db_dir: Path = CHROMA_DB_DIR, n: int = 5) -> None:
    import chromadb

    collection = chromadb.PersistentClient(path=str(db_dir)).get_collection(COLLECTION_NAME)
    result = collection.get(limit=n, include=["documents", "metadatas"])
    for index, (text, meta) in enumerate(
        zip(result.get("documents") or [], result.get("metadatas") or []), 1
    ):
        print(
            f"[{index}] {meta.get('source')} | {meta.get('section')} | "
            f"{meta.get('effective_date')}\n{text[:160]}\n"
        )


def inspect_metadata_coverage(db_dir: Path = CHROMA_DB_DIR) -> None:
    import chromadb

    collection = chromadb.PersistentClient(path=str(db_dir)).get_collection(COLLECTION_NAME)
    metadatas = collection.get(include=["metadatas"]).get("metadatas") or []
    departments: Dict[str, int] = {}
    missing = 0
    for meta in metadatas:
        department = meta.get("department", "unknown")
        departments[department] = departments.get(department, 0) + 1
        missing += meta.get("effective_date") in (None, "", "unknown")
    print(f"Tổng chunks: {len(metadatas)}")
    print(f"Theo department: {departments}")
    print(f"Thiếu effective_date: {missing}")


if __name__ == "__main__":
    print("Day 08 - Lập chỉ mục RAG")
    build_index()
    list_chunks()
    inspect_metadata_coverage()
