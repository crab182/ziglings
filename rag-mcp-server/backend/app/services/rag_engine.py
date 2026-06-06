import hashlib
import logging
import os
from pathlib import Path

import chromadb
from chromadb.config import Settings as ChromaSettings
from sentence_transformers import SentenceTransformer

from app.config import settings

logger = logging.getLogger(__name__)

_embedding_model: SentenceTransformer | None = None
_chroma_client: chromadb.ClientAPI | None = None


def _resolve_device() -> str:
    """Pick the embedding device. EMBEDDING_DEVICE=auto|cpu|cuda (default auto)."""
    requested = os.environ.get("EMBEDDING_DEVICE", "auto").lower()
    if requested == "cpu":
        return "cpu"
    try:
        import torch
        if requested == "cuda":
            if torch.cuda.is_available():
                return "cuda"
            logger.warning("EMBEDDING_DEVICE=cuda requested but no CUDA device found; falling back to CPU")
            return "cpu"
        # auto
        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:
        return "cpu"


def get_embedding_model() -> SentenceTransformer:
    global _embedding_model
    if _embedding_model is None:
        device = _resolve_device()
        logger.info("Loading embedding model %s on device=%s", settings.embedding_model, device)
        _embedding_model = SentenceTransformer(settings.embedding_model, device=device)
        logger.info("Embedding model loaded on %s", device)
    return _embedding_model


def get_chroma_client() -> chromadb.ClientAPI:
    global _chroma_client
    if _chroma_client is None:
        Path(settings.chroma_persist_dir).mkdir(parents=True, exist_ok=True)
        _chroma_client = chromadb.PersistentClient(
            path=settings.chroma_persist_dir,
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        logger.info(f"ChromaDB initialized at {settings.chroma_persist_dir}")
    return _chroma_client


def get_or_create_collection(name: str = "default"):
    client = get_chroma_client()
    model = get_embedding_model()
    dim = model.get_sentence_embedding_dimension()
    return client.get_or_create_collection(
        name=name,
        metadata={"hnsw:space": "cosine", "dimension": dim},
    )


def chunk_text(text: str, chunk_size: int = 512, overlap: int = 64) -> list[str]:
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


def compute_doc_id(source: str, chunk_idx: int) -> str:
    return hashlib.sha256(f"{source}::{chunk_idx}".encode()).hexdigest()[:16]


def ingest_text(text: str, source: str, collection_name: str = "default", metadata: dict | None = None):
    model = get_embedding_model()
    collection = get_or_create_collection(collection_name)
    chunks = chunk_text(text)
    if not chunks:
        return 0

    ids = [compute_doc_id(source, i) for i in range(len(chunks))]
    embeddings = model.encode(chunks).tolist()
    metadatas = [
        {**(metadata or {}), "source": source, "chunk_index": i}
        for i in range(len(chunks))
    ]

    collection.upsert(ids=ids, documents=chunks, embeddings=embeddings, metadatas=metadatas)
    logger.info(f"Ingested {len(chunks)} chunks from {source} into {collection_name}")
    return len(chunks)


def _bm25_search(query_text: str, collection_name: str, n_results: int) -> list[tuple[str, str, dict]]:
    """BM25 keyword search over all chunks in a collection. Returns (id, doc, meta) tuples."""
    try:
        from rank_bm25 import BM25Okapi
    except ImportError:
        return []
    collection = get_or_create_collection(collection_name)
    if collection.count() == 0:
        return []
    all_data = collection.get(include=["documents", "metadatas"])
    if not all_data["documents"]:
        return []
    tokenized = [doc.lower().split() for doc in all_data["documents"]]
    bm25 = BM25Okapi(tokenized)
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
    """Combine vector + BM25 results via RRF. Returns fused list ranked by combined score."""
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


def query(query_text: str, collection_name: str = "default", n_results: int = 5) -> list[dict]:
    model = get_embedding_model()
    collection = get_or_create_collection(collection_name)

    if collection.count() == 0:
        return []

    # Dense vector search — fetch extra candidates for fusion
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

    # BM25 sparse search + Reciprocal Rank Fusion
    bm25_hits = _bm25_search(query_text, collection_name, fetch_n)
    if bm25_hits:
        fused = _reciprocal_rank_fusion(vector_hits, bm25_hits)
    else:
        fused = vector_hits

    return fused[:n_results]


def delete_document(source: str, collection_name: str = "default"):
    collection = get_or_create_collection(collection_name)
    results = collection.get(where={"source": source})
    if results["ids"]:
        collection.delete(ids=results["ids"])
        logger.info(f"Deleted {len(results['ids'])} chunks for {source}")
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
    logger.info(f"Deleted collection {name}")
