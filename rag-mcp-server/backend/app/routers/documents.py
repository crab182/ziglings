import logging
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from app.config import settings
from app.models.schemas import QueryRequest, QueryResponse, QueryResult
from app.services import rag_engine
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

    text = parse_file(content=content, filename=filename)
    if not text.strip():
        raise HTTPException(400, "Could not extract text from file")

    base = Path(settings.documents_dir)
    base.mkdir(parents=True, exist_ok=True)
    save_dir = safe_join(base, collection)
    save_dir.mkdir(parents=True, exist_ok=True)
    save_path = safe_join(save_dir, filename)
    save_path.write_bytes(content)

    chunks = rag_engine.ingest_text(text, source=filename, collection_name=collection)
    return {"filename": filename, "collection": collection, "chunks_created": chunks}


@router.post("/query", response_model=QueryResponse)
async def query_documents(req: QueryRequest, _: dict = Depends(require_api_key)):
    results = rag_engine.query(req.query, collection_name=req.collection, n_results=req.n_results)
    return QueryResponse(
        results=[QueryResult(**r) for r in results],
        query=req.query,
    )


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
        text = parse_file(file_path=str(file_path))
        if text.strip():
            chunks = rag_engine.ingest_text(text, source=file_path.name, collection_name=collection)
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
async def create_collection(name: str, _: dict = Depends(require_admin_key)):
    validate_collection_name(name)
    rag_engine.get_or_create_collection(name)
    return {"name": name, "created": True}


@router.delete("/collections/{name}")
async def delete_collection(name: str, _: dict = Depends(require_admin_key)):
    validate_collection_name(name)
    if name == "default":
        raise HTTPException(400, "Cannot delete default collection")
    rag_engine.delete_collection(name)
    return {"name": name, "deleted": True}
