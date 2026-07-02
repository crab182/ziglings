import asyncio
import json
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from app.config import settings
from app.models.schemas import IngestTextRequest, QueryRequest
from app.services import rag_engine
from app.services.audit import append_audit
from app.services.document_parser import can_parse, parse_file
from app.services.security import (
    require_admin_key,
    require_api_key,
    safe_filename,
    safe_join,
    validate_collection_name,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/documents", tags=["documents"])

MAX_UPLOAD_BYTES = 100 * 1024 * 1024  # 100 MB


@router.post("/upload")
async def upload_document(
    file: UploadFile = File(...),
    collection: str = Form("default"),
    _: dict = Depends(require_admin_key),
):
    validate_collection_name(collection)
    filename = safe_filename(file.filename or "")

    if not can_parse(filename):
        raise HTTPException(400, "Unsupported file type")

    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"File exceeds max upload size of {MAX_UPLOAD_BYTES} bytes")
    if not content:
        raise HTTPException(400, "Empty file")

    # Parsing (Docling) and embedding are CPU-heavy — keep them off the event loop.
    text = await asyncio.to_thread(parse_file, content=content, filename=filename)
    if not text.strip():
        raise HTTPException(400, "Could not extract text from file")

    base = Path(settings.documents_dir)
    base.mkdir(parents=True, exist_ok=True)
    save_dir = safe_join(base, collection)
    save_dir.mkdir(parents=True, exist_ok=True)
    save_path = safe_join(save_dir, filename)
    save_path.write_bytes(content)

    chunks = await asyncio.to_thread(
        rag_engine.ingest_text, text, source=filename, collection_name=collection
    )
    return {"filename": filename, "collection": collection, "chunks_created": chunks}


@router.post("/query")
async def query_documents(req: QueryRequest, _: dict = Depends(require_api_key)):
    # query() is CPU-bound (embedding/BM25/rerank) and may do a network call
    # for HyDE — run it off the event loop so SSE/MCP stay responsive.
    results = await asyncio.to_thread(
        rag_engine.query, req.query, collection_name=req.collection, n_results=req.n_results
    )
    return {"results": results, "query": req.query}


@router.get("/list")
async def list_documents(collection: str = "default", _: dict = Depends(require_api_key)):
    validate_collection_name(collection)
    sources = rag_engine.list_documents(collection)
    return {"collection": collection, "documents": sources}


@router.delete("/{filename}")
async def delete_document(
    filename: str,
    collection: str = "default",
    _: dict = Depends(require_admin_key),
):
    validate_collection_name(collection)
    safe_name = safe_filename(filename)
    # Sources are stored as clean basenames at ingest, so a path-like input
    # (e.g. "sub/report.pdf") would silently collapse to a different basename
    # and delete the wrong document. Refuse anything that isn't already its
    # own basename rather than retargeting.
    if safe_name != filename:
        raise HTTPException(400, "Invalid document name")
    deleted = rag_engine.delete_document(safe_name, collection)
    base = Path(settings.documents_dir)
    file_path = safe_join(base, collection, safe_name)
    if file_path.exists():
        file_path.unlink()
    return {"deleted_chunks": deleted, "filename": safe_name}


@router.post("/reindex")
async def reindex_collection(collection: str = "default", _: dict = Depends(require_admin_key)):
    validate_collection_name(collection)
    base = Path(settings.documents_dir)
    doc_dir = safe_join(base, collection)
    if not doc_dir.exists():
        raise HTTPException(404, "No documents directory for this collection")

    try:
        rag_engine.delete_collection(collection)
    except Exception:
        logger.exception("Failed to drop collection before reindex: %s", collection)

    total_chunks = 0
    files_processed = 0
    for file_path in doc_dir.iterdir():
        if not file_path.is_file() or not can_parse(file_path.name):
            continue
        # Re-resolve to make sure we're still within the collection dir
        try:
            safe_join(doc_dir, file_path.name)
        except HTTPException:
            continue
        text = await asyncio.to_thread(parse_file, file_path=str(file_path))
        if text.strip():
            chunks = await asyncio.to_thread(
                rag_engine.ingest_text, text, source=file_path.name, collection_name=collection
            )
            total_chunks += chunks
            files_processed += 1

    return {
        "collection": collection,
        "files_processed": files_processed,
        "total_chunks": total_chunks,
    }


@router.get("/collections")
async def list_collections(_: dict = Depends(require_api_key)):
    return {"collections": rag_engine.list_collections()}


@router.post("/collections/{name}")
async def create_collection(name: str, caller: dict = Depends(require_admin_key)):
    validate_collection_name(name)
    rag_engine.get_or_create_collection(name)
    append_audit(caller.get("name", "?"), "collection.create", name)
    return {"name": name, "created": True}


@router.delete("/collections/{name}")
async def delete_collection(name: str, caller: dict = Depends(require_admin_key)):
    validate_collection_name(name)
    if name == "default":
        raise HTTPException(400, "Cannot delete default collection")
    rag_engine.delete_collection(name)
    append_audit(caller.get("name", "?"), "collection.delete", name)
    return {"name": name, "deleted": True}


@router.get("/content")
async def get_document_content(
    source: str,
    collection: str = "default",
    _: dict = Depends(require_api_key),
):
    """Reconstruct full document content from its chunks, ordered by chunk_index."""
    validate_collection_name(collection)
    if not source or len(source) > 512:
        raise HTTPException(400, "Invalid source parameter")
    col = rag_engine.get_or_create_collection(collection)
    results = col.get(where={"source": source}, include=["documents", "metadatas"])
    if not results["ids"]:
        raise HTTPException(404, "Document not found in this collection")
    pairs = sorted(
        zip(results["documents"], results["metadatas"]),
        key=lambda p: p[1].get("chunk_index", 0),
    )
    content = "\n".join(doc for doc, _ in pairs)
    return {
        "source": source,
        "collection": collection,
        "content": content,
        "chunk_count": len(pairs),
    }


@router.post("/ingest-text")
async def ingest_text(req: IngestTextRequest, _: dict = Depends(require_admin_key)):
    """Ingest raw text (e.g. from an MCP client) into the RAG."""
    validate_collection_name(req.collection)
    safe_source = safe_filename(req.source)
    chunks = rag_engine.ingest_text(req.text, source=safe_source, collection_name=req.collection)
    return {"source": safe_source, "collection": req.collection, "chunks_created": chunks}


def _build_sources(results: list[dict]) -> list[dict]:
    sources = []
    for r in results:
        s = {"source": r["source"], "score": r["score"], "excerpt": r["content"][:200]}
        if r.get("metadata", {}).get("page_number"):
            s["page"] = r["metadata"]["page_number"]
        if r.get("metadata", {}).get("section_header"):
            s["section"] = r["metadata"]["section_header"]
        sources.append(s)
    return sources


def _llm_chunks(results: list[dict]) -> list[dict]:
    """Feed the LLM expanded context when available; UI sources keep the raw chunk."""
    return [{**r, "content": r.get("expanded_content") or r["content"]} for r in results]


@router.post("/ask")
async def ask_documents(req: QueryRequest, _: dict = Depends(require_api_key)):
    """Search + generate an answer using a local LLM (Ollama). Falls back to search-only."""
    validate_collection_name(req.collection)
    results = await asyncio.to_thread(
        rag_engine.query, req.query, collection_name=req.collection, n_results=req.n_results
    )

    try:
        from app.services.llm import generate_answer
        llm_result = await generate_answer(req.query, _llm_chunks(results))
    except Exception:
        logger.exception("LLM answer generation failed")
        llm_result = {"answer": "", "model": ""}

    return {
        "answer": llm_result.get("answer", ""),
        "model": llm_result.get("model", ""),
        "query": req.query,
        "sources": _build_sources(results),
    }


@router.post("/ask/stream")
async def ask_documents_stream(req: QueryRequest, _: dict = Depends(require_api_key)):
    """Streaming variant of /ask. SSE events: sources -> delta* -> done.
    All data payloads are JSON-encoded (raw newlines would break SSE framing)."""
    validate_collection_name(req.collection)

    def sse(event: str, data) -> str:
        return f"event: {event}\ndata: {json.dumps(data)}\n\n"

    async def gen():
        # Retrieval runs inside the generator so response headers flush
        # immediately and the client sees the stream open.
        try:
            results = await asyncio.to_thread(
                rag_engine.query, req.query,
                collection_name=req.collection, n_results=req.n_results,
            )
        except Exception:
            logger.exception("Retrieval failed in /ask/stream")
            yield sse("error", {"detail": "Retrieval failed"})
            return

        yield sse("sources", _build_sources(results))

        streamed = False
        try:
            from app.services.llm import LLM_MODEL, generate_answer_stream
            async for delta in generate_answer_stream(req.query, _llm_chunks(results)):
                streamed = True
                yield sse("delta", {"text": delta})
            yield sse("done", {"model": LLM_MODEL if streamed else ""})
        except Exception:
            logger.exception("LLM stream failed in /ask/stream")
            yield sse("done", {"model": ""})

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
