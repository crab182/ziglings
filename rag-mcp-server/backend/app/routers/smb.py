import logging

from fastapi import APIRouter, HTTPException

from app.models.schemas import IngestSMBRequest, SMBBrowseRequest, SMBFileEntry, SMBListSharesRequest
from app.services import rag_engine, smb_browser
from app.services.document_parser import can_parse, parse_file

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/smb", tags=["smb"])


@router.post("/browse", response_model=list[SMBFileEntry])
async def browse_smb(req: SMBBrowseRequest):
    try:
        entries = smb_browser.browse_share(
            server=req.server,
            share=req.share,
            path=req.path,
            username=req.username,
            password=req.password,
            domain=req.domain,
        )
        return entries
    except Exception as e:
        raise HTTPException(500, f"SMB browse failed: {str(e)}")


@router.post("/shares")
async def list_shares(req: SMBListSharesRequest):
    try:
        shares = smb_browser.list_shares(req.server, req.username, req.password, req.domain)
        return {"server": req.server, "shares": shares}
    except Exception as e:
        raise HTTPException(500, f"Failed to list shares: {str(e)}")


def _ingest_directory(
    server: str,
    share: str,
    path: str,
    username: str,
    password: str,
    domain: str,
    collection: str,
    recursive: bool,
) -> dict:
    """Recursively ingest documents from an SMB path."""
    try:
        entries = smb_browser.browse_share(
            server=server, share=share, path=path,
            username=username, password=password, domain=domain,
        )
    except Exception as e:
        return {"files_processed": 0, "total_chunks": 0, "errors": [f"{path}: {str(e)}"]}

    total_chunks = 0
    files_processed = 0
    errors = []

    for entry in entries:
        if entry["is_directory"]:
            if recursive:
                sub_path = f"{path.rstrip('/')}/{entry['name']}"
                sub_result = _ingest_directory(
                    server, share, sub_path,
                    username, password, domain,
                    collection, recursive,
                )
                total_chunks += sub_result["total_chunks"]
                files_processed += sub_result["files_processed"]
                errors.extend(sub_result["errors"])
            continue

        if not can_parse(entry["name"]):
            continue

        file_path = f"{path.rstrip('/')}/{entry['name']}"
        try:
            content = smb_browser.read_file(
                server=server, share=share, path=file_path,
                username=username, password=password, domain=domain,
            )
            text = parse_file(content=content, filename=entry["name"])
            if text.strip():
                source = f"smb://{server}/{share}{file_path}"
                chunks = rag_engine.ingest_text(
                    text, source=source, collection_name=collection,
                )
                total_chunks += chunks
                files_processed += 1
        except Exception as e:
            errors.append(f"{file_path}: {str(e)}")

    return {
        "files_processed": files_processed,
        "total_chunks": total_chunks,
        "errors": errors,
    }


@router.post("/ingest")
async def ingest_from_smb(req: IngestSMBRequest):
    result = _ingest_directory(
        server=req.server,
        share=req.share,
        path=req.path,
        username=req.username,
        password=req.password,
        domain=req.domain,
        collection=req.collection,
        recursive=req.recursive,
    )
    return result
