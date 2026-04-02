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


def get_embedding_model() -> SentenceTransformer:
    global _embedding_model
    if _embedding_model is None:
        logger.info(f"Loading embedding model: {settings.embedding_model}")
        _embedding_model = SentenceTransformer(settings.embedding_model)
        logger.info("Embedding model loaded")
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


def query(query_text: str, collection_name: str = "default", n_results: int = 5) -> list[dict]:
    model = get_embedding_model()
    collection = get_or_create_collection(collection_name)

    if collection.count() == 0:
        return []

    query_embedding = model.encode([query_text]).tolist()
    results = collection.query(
        query_embeddings=query_embedding,
        n_results=min(n_results, collection.count()),
        include=["documents", "metadatas", "distances"],
    )

    output = []
    for doc, meta, dist in zip(
        results["documents"][0],
        results["metadatas"][0],
        results["distances"][0],
    ):
        output.append({
            "content": doc,
            "source": meta.get("source", "unknown"),
            "score": round(1 - dist, 4),
            "metadata": meta,
        })
    return output


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
    collections = client.list_collections()
    return [{"name": c.name, "document_count": c.count()} for c in collections]


def delete_collection(name: str):
    client = get_chroma_client()
    client.delete_collection(name)
    logger.info(f"Deleted collection {name}")
