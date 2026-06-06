import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException

from app.models.schemas import (
    IngestSMBRequest,
    SavedShareCreate,
    SMBBrowseRequest,
    SMBFileEntry,
    SMBListSharesRequest,
)
from app.services import smb_browser
from app.services.security import require_admin_key, validate_collection_name

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/smb", tags=["smb"])


def _get_scheduler():
    """Lazy import — scheduler depends on APScheduler + cryptography."""
    try:
        from app.services import scheduler
        return scheduler
    except Exception:
        return None


def _get_smb_shares():
    """Lazy import — smb_shares depends on cryptography."""
    try:
        from app.services import smb_shares
        return smb_shares
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Live SMB browsing (ad-hoc credentials)
# ---------------------------------------------------------------------------

@router.post("/browse")
async def browse_smb(req: SMBBrowseRequest, _: dict = Depends(require_admin_key)):
    try:
        return smb_browser.browse_share(
            server=req.server, share=req.share, path=req.path,
            username=req.username, password=req.password, domain=req.domain,
        )
    except Exception:
        logger.exception("SMB browse failed: server=%s share=%s", req.server, req.share)
        raise HTTPException(502, "SMB browse failed")


@router.post("/shares")
async def list_shares(req: SMBListSharesRequest, _: dict = Depends(require_admin_key)):
    try:
        shares = smb_browser.list_shares(req.server, req.username, req.password, req.domain)
        return {"server": req.server, "shares": shares}
    except Exception:
        logger.exception("SMB list shares failed: server=%s", req.server)
        raise HTTPException(502, "Failed to list SMB shares")


@router.post("/ingest")
async def ingest_from_smb(req: IngestSMBRequest, _: dict = Depends(require_admin_key)):
    validate_collection_name(req.collection)
    smb_shares = _get_smb_shares()
    if not smb_shares:
        raise HTTPException(503, "SMB shares service unavailable")
    result = await asyncio.to_thread(
        smb_shares.ingest_directory,
        server=req.server, share=req.share, path=req.path,
        username=req.username, password=req.password, domain=req.domain,
        collection=req.collection, recursive=req.recursive,
    )
    return result


# ---------------------------------------------------------------------------
# Saved share profiles (encrypted creds at rest)
# ---------------------------------------------------------------------------

@router.get("/saved")
async def list_saved_shares(_: dict = Depends(require_admin_key)):
    smb_shares = _get_smb_shares()
    sched = _get_scheduler()
    if not smb_shares:
        return []
    shares = smb_shares.list_saved()
    for s in shares:
        s["sync_running"] = sched.is_running(s["name"]) if sched else False
    return shares


@router.post("/saved")
async def save_share(req: SavedShareCreate, _: dict = Depends(require_admin_key)):
    validate_collection_name(req.collection)
    smb_shares = _get_smb_shares()
    sched = _get_scheduler()
    if not smb_shares:
        raise HTTPException(503, "SMB shares service unavailable")
    entry = smb_shares.save_share(req.model_dump())
    if sched:
        if req.auto_sync:
            sched.reschedule(req.name, req.interval_minutes)
        else:
            sched.unschedule(req.name)
        entry["sync_running"] = sched.is_running(req.name)
    else:
        entry["sync_running"] = False
    return entry


@router.delete("/saved/{name}")
async def delete_saved_share(name: str, _: dict = Depends(require_admin_key)):
    smb_shares = _get_smb_shares()
    sched = _get_scheduler()
    if not smb_shares:
        raise HTTPException(503, "SMB shares service unavailable")
    if sched:
        sched.unschedule(name)
    if smb_shares.delete_share(name):
        return {"deleted": True, "name": name}
    raise HTTPException(404, "Saved share not found")


@router.post("/saved/{name}/ingest")
async def ingest_saved_share(name: str, _: dict = Depends(require_admin_key)):
    smb_shares = _get_smb_shares()
    if not smb_shares:
        raise HTTPException(503, "SMB shares service unavailable")
    share = smb_shares.get_decrypted(name)
    if not share:
        raise HTTPException(404, "Saved share not found")
    result = await asyncio.to_thread(
        smb_shares.ingest_directory,
        server=share["server"], share=share["share"],
        path=share.get("path", "/"),
        username=share.get("username", "guest"),
        password=share.get("password", ""),
        domain=share.get("domain", "WORKGROUP"),
        collection=share.get("collection", "default"),
        recursive=share.get("recursive", True),
    )
    smb_shares.update_sync_result(name, result)
    return result


# ---------------------------------------------------------------------------
# Sync controls
# ---------------------------------------------------------------------------

@router.post("/saved/{name}/sync/enable")
async def enable_sync(name: str, _: dict = Depends(require_admin_key)):
    smb_shares = _get_smb_shares()
    sched = _get_scheduler()
    if not smb_shares:
        raise HTTPException(503, "SMB shares service unavailable")
    share = smb_shares.get_decrypted(name)
    if not share:
        raise HTTPException(404, "Saved share not found")
    from app.config import load_config, save_config
    config = load_config()
    for s in config.get("smb_shares", []):
        if s["name"] == name:
            s["auto_sync"] = True
            break
    save_config(config)
    if sched:
        sched.reschedule(name, share.get("interval_minutes", 60))
    return {"name": name, "auto_sync": True}


@router.post("/saved/{name}/sync/disable")
async def disable_sync(name: str, _: dict = Depends(require_admin_key)):
    sched = _get_scheduler()
    from app.config import load_config, save_config
    config = load_config()
    for s in config.get("smb_shares", []):
        if s["name"] == name:
            s["auto_sync"] = False
            break
    save_config(config)
    if sched:
        sched.unschedule(name)
    return {"name": name, "auto_sync": False}


@router.post("/saved/{name}/sync/trigger")
async def trigger_sync(name: str, _: dict = Depends(require_admin_key)):
    smb_shares = _get_smb_shares()
    sched = _get_scheduler()
    if not smb_shares or not sched:
        raise HTTPException(503, "Scheduler unavailable")
    share = smb_shares.get_decrypted(name)
    if not share:
        raise HTTPException(404, "Saved share not found")
    if sched.is_running(name):
        raise HTTPException(409, "Sync already running for this share")
    asyncio.create_task(sched.run_share_sync(name))
    return {"name": name, "triggered": True}
