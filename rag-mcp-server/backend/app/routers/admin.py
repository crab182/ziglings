import logging

from fastapi import APIRouter, Depends, HTTPException

from app.config import load_config, save_config, settings
from app.models.schemas import APIKeyCreate, APIKeyResponse, ServerStatus
from app.services import auth, rag_engine
from app.services.security import require_admin_key, require_api_key

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/status", response_model=ServerStatus)
async def get_status(_: dict = Depends(require_api_key)):
    config = load_config()
    collections = rag_engine.list_collections()
    total_docs = sum(c["document_count"] for c in collections)
    return ServerStatus(
        hostname=settings.server_hostname,
        ip=settings.server_ip,
        mcp_enabled=config.get("mcp_enabled", True),
        total_documents=total_docs,
        collections=[c["name"] for c in collections],
        api_keys_count=len([k for k in config.get("api_keys", []) if k.get("active", True)]),
    )


@router.post("/api-keys")
async def create_api_key(req: APIKeyCreate, caller: dict = Depends(require_admin_key)):
    # Disallow duplicate names
    existing = {k["name"] for k in auth.list_api_keys()}
    if req.name in existing:
        raise HTTPException(409, f"API key already exists: {req.name}")
    # Bootstrap promotion: first key is always admin regardless of request
    is_admin = req.is_admin or caller.get("bootstrap", False)
    result = auth.create_api_key(req.name, req.description, is_admin=is_admin)
    logger.info("API key created: name=%s is_admin=%s by=%s", req.name, is_admin, caller.get("name"))
    return {
        "name": result["name"],
        "key": result["raw_key"],
        "key_prefix": result["key_prefix"],
        "description": result["description"],
        "is_admin": result["is_admin"],
        "created_at": result["created_at"],
        "message": "Save this key - it cannot be retrieved again",
    }


@router.get("/api-keys", response_model=list[APIKeyResponse])
async def list_api_keys(_: dict = Depends(require_admin_key)):
    return auth.list_api_keys()


@router.delete("/api-keys/{name}")
async def delete_api_key(name: str, caller: dict = Depends(require_admin_key)):
    if auth.delete_api_key(name):
        logger.info("API key deleted: name=%s by=%s", name, caller.get("name"))
        return {"deleted": True, "name": name}
    raise HTTPException(404, f"API key not found: {name}")


@router.post("/api-keys/{name}/revoke")
async def revoke_api_key(name: str, caller: dict = Depends(require_admin_key)):
    if auth.revoke_api_key(name):
        logger.info("API key revoked: name=%s by=%s", name, caller.get("name"))
        return {"revoked": True, "name": name}
    raise HTTPException(404, f"API key not found: {name}")


@router.post("/mcp/toggle")
async def toggle_mcp(enabled: bool = True, _: dict = Depends(require_admin_key)):
    config = load_config()
    config["mcp_enabled"] = enabled
    save_config(config)
    return {"mcp_enabled": enabled}


@router.get("/config")
async def get_config(_: dict = Depends(require_admin_key)):
    config = load_config()
    safe_config = {**config}
    safe_config["api_keys"] = [
        {
            "name": k["name"],
            "key_prefix": k["key_prefix"],
            "is_admin": k.get("is_admin", False),
            "active": k.get("active", True),
        }
        for k in config.get("api_keys", [])
    ]
    # Strip any stored SMB credentials
    if "smb_shares" in safe_config:
        safe_config["smb_shares"] = [
            {k: v for k, v in s.items() if k != "password"}
            for s in safe_config["smb_shares"]
        ]
    return safe_config


@router.get("/bootstrap-required")
async def bootstrap_required():
    """Public check: does this server need its first admin key created?"""
    return {"bootstrap_required": not auth.has_any_keys()}
