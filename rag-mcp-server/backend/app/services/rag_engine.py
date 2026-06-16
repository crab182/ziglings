import hashlib
import logging
import os
import re
import time
from pathlib import Path

import chromadb
from chromadb.config import Settings as ChromaSettings
from sentence_transformers import SentenceTransformer

from app.config import settings, atomic_update
from app.services.sanitize import sanitize_chunk

RERANKER_MODEL = os.environ.get("RERANKER_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2")

logger = logging.getLogger(__name__)

_embedding_model: SentenceTransformer | None = None
_chroma_client: chromadb.ClientAPI | None = None
_reranker = None

# Cached BM25 index per collection: {collection_name: (doc_count, BM25Okapi, all_data)}
_bm25_cache: dict[str, tuple] = {}

_metrics = {"query_count": 0, "ingest_count": 0, "total_retrieve_ms": 0, "total_rerank_ms": 0}


# ---------------------------------------------------------------------------
# Model / DB initialization
# ---------------------------------------------------------------------------

def _resolve_device() -> str:
    requested = os.environ.get("EMBEDDING_DEVICE", "auto").lower()
    if requested == "cpu":
        return "cpu"
    try:
        import torch
        if requested == "cuda":
            return "cuda" if torch.cuda.is_available() else "cpu"
        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:
        return "cpu"


def get_embedding_model() -> SentenceTransformer:
    global _embedding_model
    if _embedding_model is None:
        device = _resolve_device()
        logger.info("Loading embedding model %s on device=%s", settings.embedding_model, device)
        _embedding_model = SentenceTransformer(settings.embedding_model, device=device)
        logger.info("Embedding model loaded (%d-dim)", _embedding_model.get_sentence_embedding_dimension())
    return _embedding_model


def get_chroma_client() -> chromadb.ClientAPI:
    global _chroma_client
    if _chroma_client is None:
        Path(settings.chroma_persist_dir).mkdir(parents=True, exist_ok=True)
        _chroma_client = chromadb.PersistentClient(
            path=settings.chroma_persist_dir,
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        logger.info("ChromaDB initialized at %s", settings.chroma_persist_dir)
    return _chroma_client


def get_or_create_collection(name: str = "default"):
    client = get_chroma_client()
    model = get_embedding_model()
    dim = model.get_sentence_embedding_dimension()
    return client.get_or_create_collection(
        name=name,
        metadata={"hnsw:space": "cosine", "dimension": dim},
    )


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

def chunk_text(text: str, chunk_size: int = 512, overlap: int = 64) -> list[str]:
    """Simple word-based chunking for plain text."""
    words = text.split()
    chunks = []
    start = 0
    while start < len(words):
        end = start + chunk_size
        chunk = " ".join(words[start:end])
        if chunk.strip():
            chunks.append(chunk)
        start = end - overlap
    return chunks if chunks else [text]


def _is_table_line(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("|") and "|" in stripped[1:]


def _extract_page_number(text: str) -> int | None:
    m = re.search(r"<!--\s*page\s+(\d+)\s*-->", text)
    return int(m.group(1)) if m else None


def chunk_text_structured(text: str, max_words: int = 512) -> list[dict]:
    """Markdown-aware chunker. Returns list of {text, section_header, page_number}."""
    sections: list[tuple[str, str]] = []
    current_header = ""
    current_lines: list[str] = []

    for line in text.split("\n"):
        if re.match(r"^#{1,3}\s+", line):
            if current_lines:
                sections.append((current_header, "\n".join(current_lines)))
                current_lines = []
            current_header = line.lstrip("#").strip()
        else:
            current_lines.append(line)
    if current_lines:
        sections.append((current_header, "\n".join(current_lines)))

    result: list[dict] = []
    for header, body in sections:
        paragraphs: list[str] = []
        buf: list[str] = []
        in_table = False

        for line in body.split("\n"):
            is_tbl = _is_table_line(line)
            if is_tbl:
                if not in_table and buf:
                    paragraphs.append("\n".join(buf))
                    buf = []
                in_table = True
                buf.append(line)
            else:
                if in_table:
                    paragraphs.append("\n".join(buf))
                    buf = []
                    in_table = False
                if line.strip() == "" and buf:
                    paragraphs.append("\n".join(buf))
                    buf = []
                elif line.strip():
                    buf.append(line)
        if buf:
            paragraphs.append("\n".join(buf))

        merged = ""
        for para in paragraphs:
            candidate = (merged + "\n\n" + para).strip() if merged else para
            if len(candidate.split()) <= max_words:
                merged = candidate
            else:
                if merged:
                    result.append({"text": merged, "section_header": header, "page_number": _extract_page_number(merged)})
                merged = para
        if merged.strip():
            result.append({"text": merged, "section_header": header, "page_number": _extract_page_number(merged)})

    return result if result else [{"text": text, "section_header": "", "page_number": None}]


def _looks_like_markdown(text: str) -> bool:
    if re.search(r"^#{1,3}\s+", text, re.MULTILINE):
        return True
    lines = text.split("\n")
    table_lines = sum(1 for l in lines[:50] if _is_table_line(l))
    if table_lines >= 2:
        return True
    if re.search(r"<!--\s*page\s+\d+\s*-->", text):
        return True
    return False


# ---------------------------------------------------------------------------
# Reranker
# ---------------------------------------------------------------------------

def _get_reranker():
    global _reranker
    if _reranker is not None:
        return _reranker
    try:
        from sentence_transformers import CrossEncoder
        _reranker = CrossEncoder(RERANKER_MODEL)
        logger.info("Loaded reranker: %s", RERANKER_MODEL)
        return _reranker
    except Exception as e:
        logger.warning("Reranker unavailable (%s): %s", RERANKER_MODEL, e)
        return None


def _rerank(query: str, results: list[dict], top_k: int = 8) -> list[dict]:
    reranker = _get_reranker()
    if not reranker or not results:
        return results[:top_k]
    pairs = [(query, r["content"]) for r in results]
    scores = reranker.predict(pairs)
    for r, s in zip(results, scores):
        r["rerank_score"] = round(float(s), 4)
    results.sort(key=lambda r: r.get("rerank_score", 0), reverse=True)
    return results[:top_k]


# ---------------------------------------------------------------------------
# Ingestion (with content-hash dedup under file lock)
# ---------------------------------------------------------------------------

def compute_doc_id(source: str, chunk_idx: int) -> str:
    return hashlib.sha256(f"{source}::{chunk_idx}".encode()).hexdigest()[:16]


def _compute_content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _check_content_hash(source: str, collection_name: str, content_hash: str) -> bool:
    """Atomic check-and-update under file lock. Returns True if unchanged."""
    def _update(config):
        hashes = config.setdefault("content_hashes", {}).setdefault(collection_name, {})
        if hashes.get(source) == content_hash:
            return True
        hashes[source] = content_hash
        return False
    return atomic_update(_update)


def _remove_content_hash(source: str, collection_name: str):
    def _update(config):
        hashes = config.get("content_hashes", {}).get(collection_name, {})
        hashes.pop(source, None)
    atomic_update(_update)


def ingest_text(text: str, source: str, collection_name: str = "default", metadata: dict | None = None):
    content_hash = _compute_content_hash(text)
    if _check_content_hash(source, collection_name, content_hash):
        logger.info("Skipping %s in %s (content unchanged)", source, collection_name)
        return 0

    model = get_embedding_model()
    collection = get_or_create_collection(collection_name)

    if _looks_like_markdown(text):
        structured = chunk_text_structured(text)
        chunk_texts = [c["text"] for c in structured]
        extra_meta = [
            {"section_header": c.get("section_header", ""), "page_number": c.get("page_number")}
            for c in structured
        ]
    else:
        chunk_texts = chunk_text(text)
        extra_meta = [{} for _ in chunk_texts]

    if not chunk_texts:
        return 0

    ids = [compute_doc_id(source, i) for i in range(len(chunk_texts))]
    embeddings = model.encode(chunk_texts).tolist()
    metadatas = []
    for i, em in enumerate(extra_meta):
        m = {**(metadata or {}), "source": source, "chunk_index": i}
        if em.get("section_header"):
            m["section_header"] = em["section_header"]
        if em.get("page_number") is not None:
            m["page_number"] = em["page_number"]
        if i > 0:
            m["prev_chunk_id"] = ids[i - 1]
        if i < len(chunk_texts) - 1:
            m["next_chunk_id"] = ids[i + 1]
        metadatas.append(m)

    collection.upsert(ids=ids, documents=chunk_texts, embeddings=embeddings, metadatas=metadatas)
    _bm25_cache.pop(collection_name, None)  # invalidate BM25 cache
    _metrics["ingest_count"] += 1
    logger.info("Ingested %d chunks from %s into %s", len(chunk_texts), source, collection_name)
    return len(chunk_texts)


# ---------------------------------------------------------------------------
# BM25 search (cached index per collection)
# ---------------------------------------------------------------------------

def _bm25_search(query_text: str, collection_name: str, n_results: int) -> list[tuple[str, str, dict]]:
    try:
        from rank_bm25 import BM25Okapi
    except ImportError:
        return []
    collection = get_or_create_collection(collection_name)
    count = collection.count()
    if count == 0:
        return []

    cached = _bm25_cache.get(collection_name)
    if cached and cached[0] == count:
        _, bm25, all_data = cached
    else:
        all_data = collection.get(include=["documents", "metadatas"])
        if not all_data["documents"]:
            return []
        tokenized = [doc.lower().split() for doc in all_data["documents"]]
        bm25 = BM25Okapi(tokenized)
        _bm25_cache[collection_name] = (count, bm25, all_data)

    scores = bm25.get_scores(query_text.lower().split())
    top_idx = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:n_results]
    return [
        (all_data["ids"][i], all_data["documents"][i], all_data["metadatas"][i])
        for i in top_idx if scores[i] > 0
    ]


def _reciprocal_rank_fusion(
    vector_results: list[dict],
    bm25_results: list[tuple[str, str, dict]],
    k: int = 60,
) -> list[dict]:
    scores: dict[str, float] = {}
    docs: dict[str, dict] = {}

    for rank, item in enumerate(vector_results):
        doc_id = item["metadata"].get("source", "") + "::" + str(item["metadata"].get("chunk_index", rank))
        scores[doc_id] = scores.get(doc_id, 0) + 1.0 / (k + rank + 1)
        docs[doc_id] = item

    for rank, (chunk_id, doc_text, meta) in enumerate(bm25_results):
        doc_id = meta.get("source", "") + "::" + str(meta.get("chunk_index", rank))
        scores[doc_id] = scores.get(doc_id, 0) + 1.0 / (k + rank + 1)
        if doc_id not in docs:
            docs[doc_id] = {
                "content": doc_text,
                "source": meta.get("source", "unknown"),
                "score": 0.0,
                "metadata": meta,
            }

    ranked = sorted(docs.keys(), key=lambda did: scores[did], reverse=True)
    return [{**docs[did], "score": round(scores[did], 4)} for did in ranked]


# ---------------------------------------------------------------------------
# Query (vector + BM25 + rerank + sanitize + observability)
# ---------------------------------------------------------------------------

def get_metrics() -> dict:
    return {**_metrics}


def query(query_text: str, collection_name: str = "default", n_results: int = 5) -> list[dict]:
    t0 = time.monotonic()
    model = get_embedding_model()
    collection = get_or_create_collection(collection_name)

    if collection.count() == 0:
        return []

    fetch_n = min(max(n_results * 5, 30), collection.count())
    query_embedding = model.encode([query_text]).tolist()
    results = collection.query(
        query_embeddings=query_embedding,
        n_results=fetch_n,
        include=["documents", "metadatas", "distances"],
    )

    vector_hits = []
    for doc, meta, dist in zip(
        results["documents"][0],
        results["metadatas"][0],
        results["distances"][0],
    ):
        vector_hits.append({
            "content": doc,
            "source": meta.get("source", "unknown"),
            "score": round(1 - dist, 4),
            "metadata": meta,
        })

    t_retrieve = time.monotonic()

    bm25_hits = _bm25_search(query_text, collection_name, fetch_n)
    if bm25_hits:
        fused = _reciprocal_rank_fusion(vector_hits, bm25_hits)
    else:
        fused = vector_hits

    t_rerank_start = time.monotonic()
    if len(fused) > n_results:
        fused = _rerank(query_text, fused, top_k=n_results)
    t_rerank = time.monotonic()

    top = fused[:n_results]
    for item in top:
        item["content"] = sanitize_chunk(item["content"])

    total_ms = round((time.monotonic() - t0) * 1000, 1)
    retrieve_ms = round((t_retrieve - t0) * 1000, 1)
    rerank_ms = round((t_rerank - t_rerank_start) * 1000, 1)
    logger.info("Query [%s] %d results in %.0fms (retrieve=%.0f, rerank=%.0f)",
                collection_name, len(top), total_ms, retrieve_ms, rerank_ms)
    _metrics["query_count"] += 1
    _metrics["total_retrieve_ms"] += retrieve_ms
    _metrics["total_rerank_ms"] += rerank_ms

    return top


# ---------------------------------------------------------------------------
# Document management
# ---------------------------------------------------------------------------

def delete_document(source: str, collection_name: str = "default"):
    collection = get_or_create_collection(collection_name)
    results = collection.get(where={"source": source})
    if results["ids"]:
        collection.delete(ids=results["ids"])
        _remove_content_hash(source, collection_name)
        _bm25_cache.pop(collection_name, None)
        logger.info("Deleted %d chunks for %s", len(results["ids"]), source)
        return len(results["ids"])
    return 0


def list_documents(collection_name: str = "default") -> list[str]:
    collection = get_or_create_collection(collection_name)
    if collection.count() == 0:
        return []
    results = collection.get(include=["metadatas"])
    sources = set()
    for meta in results["metadatas"]:
        if "source" in meta:
            sources.add(meta["source"])
    return sorted(sources)


def list_collections() -> list[dict]:
    client = get_chroma_client()
    collection_names = client.list_collections()
    result = []
    for name in collection_names:
        try:
            col = client.get_collection(name)
            result.append({"name": name, "document_count": col.count()})
        except Exception:
            result.append({"name": name, "document_count": 0})
    return result


def delete_collection(name: str):
    client = get_chroma_client()
    client.delete_collection(name)
    _bm25_cache.pop(name, None)
    logger.info("Deleted collection %s", name)
