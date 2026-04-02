import logging

from fastapi import APIRouter, HTTPException

from app.models.schemas import IngestSMBRequest, SMBBrowseRequest, SMBFileEntry
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
async def list_shares(server: str, username: str = "guest", password: str = "", domain: str = "WORKGROUP"):
    try:
        shares = smb_browser.list_shares(server, username, password, domain)
        return {"server": server, "shares": shares}
    except Exception as e:
        raise HTTPException(500, f"Failed to list shares: {str(e)}")


@router.post("/ingest")
async def ingest_from_smb(req: IngestSMBRequest):
    try:
        entries = smb_browser.browse_share(
            server=req.server,
            share=req.share,
            path=req.path,
            username=req.username,
            password=req.password,
            domain=req.domain,
        )
    except Exception as e:
        raise HTTPException(500, f"SMB browse failed: {str(e)}")

    total_chunks = 0
    files_processed = 0
    errors = []

    for entry in entries:
        if entry["is_directory"]:
            if req.recursive:
                # Recursively ingest subdirectories
                sub_path = f"{req.path.rstrip('/')}/{entry['name']}"
                try:
                    sub_req = IngestSMBRequest(
                        server=req.server,
                        share=req.share,
                        path=sub_path,
                        username=req.username,
                        password=req.password,
                        domain=req.domain,
                        collection=req.collection,
                        recursive=True,
                    )
                    result = await ingest_from_smb(sub_req)
                    total_chunks += result["total_chunks"]
                    files_processed += result["files_processed"]
                except Exception as e:
                    errors.append(f"{sub_path}: {str(e)}")
            continue

        if not can_parse(entry["name"]):
            continue

        file_path = f"{req.path.rstrip('/')}/{entry['name']}"
        try:
            content = smb_browser.read_file(
                server=req.server,
                share=req.share,
                path=file_path,
                username=req.username,
                password=req.password,
                domain=req.domain,
            )
            text = parse_file(content=content, filename=entry["name"])
            if text.strip():
                source = f"smb://{req.server}/{req.share}{file_path}"
                chunks = rag_engine.ingest_text(
                    text, source=source, collection_name=req.collection
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
