import logging

from fastapi import APIRouter, HTTPException

from app.config import load_config, save_config, settings
from app.models.schemas import APIKeyCreate, APIKeyResponse, ServerStatus
from app.services import auth, rag_engine

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/status", response_model=ServerStatus)
async def get_status():
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
async def create_api_key(req: APIKeyCreate):
    result = auth.create_api_key(req.name, req.description)
    return {
        "name": result["name"],
        "key": result["raw_key"],
        "key_prefix": result["key_prefix"],
        "description": result["description"],
        "created_at": result["created_at"],
        "message": "Save this key - it cannot be retrieved again",
    }


@router.get("/api-keys", response_model=list[APIKeyResponse])
async def list_api_keys():
    return auth.list_api_keys()


@router.delete("/api-keys/{name}")
async def delete_api_key(name: str):
    if auth.delete_api_key(name):
        return {"deleted": True, "name": name}
    raise HTTPException(404, f"API key not found: {name}")


@router.post("/api-keys/{name}/revoke")
async def revoke_api_key(name: str):
    if auth.revoke_api_key(name):
        return {"revoked": True, "name": name}
    raise HTTPException(404, f"API key not found: {name}")


@router.post("/mcp/toggle")
async def toggle_mcp(enabled: bool = True):
    config = load_config()
    config["mcp_enabled"] = enabled
    save_config(config)
    return {"mcp_enabled": enabled}


@router.get("/config")
async def get_config():
    config = load_config()
    # Strip sensitive data
    safe_config = {**config}
    safe_config["api_keys"] = [
        {"name": k["name"], "key_prefix": k["key_prefix"], "active": k.get("active", True)}
        for k in config.get("api_keys", [])
    ]
    return safe_config
