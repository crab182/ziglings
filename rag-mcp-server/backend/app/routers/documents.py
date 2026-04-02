import logging
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.config import settings
from app.models.schemas import QueryRequest, QueryResponse, QueryResult
from app.services import rag_engine
from app.services.document_parser import can_parse, parse_file

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/documents", tags=["documents"])


@router.post("/upload")
async def upload_document(
    file: UploadFile = File(...),
    collection: str = Form("default"),
):
    if not can_parse(file.filename):
        raise HTTPException(400, f"Unsupported file type: {file.filename}")

    content = await file.read()
    text = parse_file(content=content, filename=file.filename)
    if not text.strip():
        raise HTTPException(400, "Could not extract text from file")

    # Save file to disk
    save_dir = Path(settings.documents_dir) / collection
    save_dir.mkdir(parents=True, exist_ok=True)
    save_path = save_dir / file.filename
    save_path.write_bytes(content)

    chunks = rag_engine.ingest_text(text, source=file.filename, collection_name=collection)
    return {"filename": file.filename, "collection": collection, "chunks_created": chunks}


@router.post("/query", response_model=QueryResponse)
async def query_documents(req: QueryRequest):
    results = rag_engine.query(req.query, collection_name=req.collection, n_results=req.n_results)
    return QueryResponse(
        results=[QueryResult(**r) for r in results],
        query=req.query,
    )


@router.get("/list")
async def list_documents(collection: str = "default"):
    sources = rag_engine.list_documents(collection)
    return {"collection": collection, "documents": sources}


@router.delete("/{filename}")
async def delete_document(filename: str, collection: str = "default"):
    deleted = rag_engine.delete_document(filename, collection)
    # Also remove from disk
    file_path = Path(settings.documents_dir) / collection / filename
    if file_path.exists():
        file_path.unlink()
    return {"deleted_chunks": deleted, "filename": filename}


@router.post("/reindex")
async def reindex_collection(collection: str = "default"):
    doc_dir = Path(settings.documents_dir) / collection
    if not doc_dir.exists():
        raise HTTPException(404, f"No documents directory for collection: {collection}")

    # Delete existing collection and re-ingest
    try:
        rag_engine.delete_collection(collection)
    except Exception:
        pass

    total_chunks = 0
    files_processed = 0
    for file_path in doc_dir.iterdir():
        if file_path.is_file() and can_parse(file_path.name):
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
async def list_collections():
    return {"collections": rag_engine.list_collections()}


@router.post("/collections/{name}")
async def create_collection(name: str):
    rag_engine.get_or_create_collection(name)
    return {"name": name, "created": True}


@router.delete("/collections/{name}")
async def delete_collection(name: str):
    if name == "default":
        raise HTTPException(400, "Cannot delete default collection")
    rag_engine.delete_collection(name)
    return {"name": name, "deleted": True}
