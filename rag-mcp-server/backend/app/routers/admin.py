import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse

from app.config import load_config, save_config, settings
from app.models.schemas import APIKeyCreate
from app.services import auth, rag_engine
from app.services.audit import append_audit, read_audit
from app.services.security import allowed_collections, require_admin_key, require_api_key

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/status")
async def get_status(caller: dict = Depends(require_api_key)):
    config = load_config()
    try:
        # list_collections scans chunk metadata (O(total chunks)) — off the loop.
        collections = await asyncio.to_thread(rag_engine.list_collections)
        # Scoped keys must not enumerate out-of-scope collection names here —
        # this would bypass the filtered GET /api/documents/collections.
        allowed = allowed_collections(caller)
        if allowed is not None:
            collections = [c for c in collections if c["name"] in allowed]
        total_docs = sum(c["document_count"] for c in collections)
        collection_names = [c["name"] for c in collections]
    except Exception:
        logger.exception("Failed to query collections for status")
        collections = []
        total_docs = 0
        collection_names = []
    return {
        "hostname": settings.server_hostname,
        "ip": settings.server_ip,
        "mcp_enabled": config.get("mcp_enabled", True),
        "total_documents": total_docs,
        "collections": collection_names,
        "active_credentials": len([
            k for k in config.get("api_keys", [])
            if k.get("active", True) and k.get("name") != auth.SERVICE_KEY_NAME
        ]),
    }


@router.post("/api-keys")
async def create_api_key(req: APIKeyCreate, caller: dict = Depends(require_admin_key)):
    existing = {k["name"] for k in auth.list_api_keys()}
    if req.name in existing:
        raise HTTPException(409, f"API key already exists: {req.name}")
    if caller.get("bootstrap", False) and (not req.is_admin or req.collections):
        # Fail loud rather than silently minting an unrestricted admin key when
        # the operator asked for a scoped/read-only one — the first key is
        # always admin (it must be able to manage keys), so a restricted
        # request during bootstrap can't be honored.
        raise HTTPException(
            400,
            "The first key must be an unrestricted admin key "
            "(is_admin=true, no collections). Create scoped keys with it afterwards.",
        )
    is_admin = req.is_admin or caller.get("bootstrap", False)
    # ACLs only make sense on non-admin keys (admin bypasses them anyway).
    collections = [] if is_admin else req.collections
    result = auth.create_api_key(
        req.name, req.description, is_admin=is_admin, collections=collections
    )
    logger.info("API key created: name=%s is_admin=%s by=%s", req.name, is_admin, caller.get("name"))
    append_audit(
        caller.get("name", "?"), "api_key.create", req.name,
        detail={"collections": collections} if collections else None,
    )
    return {
        "name": result["name"],
        "key": result["raw_key"],
        "key_prefix": result["key_prefix"],
        "description": result["description"],
        "is_admin": result["is_admin"],
        "collections": result.get("collections", []),
        "created_at": result["created_at"],
        "message": "Save this key - it cannot be retrieved again",
    }


@router.get("/api-keys")
async def list_api_keys(_: dict = Depends(require_admin_key)):
    return auth.list_api_keys()


@router.delete("/api-keys/{name}")
async def delete_api_key(name: str, caller: dict = Depends(require_admin_key)):
    if auth.delete_api_key(name):
        logger.info("API key deleted: name=%s by=%s", name, caller.get("name"))
        append_audit(caller.get("name", "?"), "api_key.delete", name)
        return {"deleted": True, "name": name}
    raise HTTPException(404, f"API key not found: {name}")


@router.post("/api-keys/{name}/revoke")
async def revoke_api_key(name: str, caller: dict = Depends(require_admin_key)):
    if auth.revoke_api_key(name):
        logger.info("API key revoked: name=%s by=%s", name, caller.get("name"))
        append_audit(caller.get("name", "?"), "api_key.revoke", name)
        return {"revoked": True, "name": name}
    raise HTTPException(404, f"API key not found: {name}")


@router.post("/mcp/toggle")
async def toggle_mcp(enabled: bool = True, caller: dict = Depends(require_admin_key)):
    config = load_config()
    config["mcp_enabled"] = enabled
    save_config(config)
    append_audit(caller.get("name", "?"), "mcp.toggle", str(enabled))
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
            "collections": k.get("collections", []),
        }
        for k in config.get("api_keys", [])
        if k.get("name") != auth.SERVICE_KEY_NAME
    ]
    # Never expose internal key-management state
    safe_config.pop("content_hashes", None)
    if "smb_shares" in safe_config:
        safe_config["smb_shares"] = [
            {k: v for k, v in s.items() if k not in ("password", "encrypted_password")}
            for s in safe_config["smb_shares"]
        ]
    return safe_config


@router.get("/bootstrap-required")
async def bootstrap_required():
    """Public check: does this server need its first admin key created?"""
    try:
        return {"bootstrap_required": not auth.has_any_keys()}
    except Exception:
        logger.exception("Failed to check bootstrap status")
        return {"bootstrap_required": True}


@router.get("/metrics")
async def get_metrics(_: dict = Depends(require_admin_key)):
    """Performance metrics: query/ingest counts and average latencies."""
    m = rag_engine.get_metrics()
    qc = m.get("query_count", 0)
    return {
        "query_count": qc,
        "ingest_count": m.get("ingest_count", 0),
        "avg_retrieve_ms": round(m.get("total_retrieve_ms", 0) / qc, 1) if qc else 0,
        "avg_rerank_ms": round(m.get("total_rerank_ms", 0) / qc, 1) if qc else 0,
    }


@router.get("/audit")
async def get_audit(_: dict = Depends(require_admin_key)):
    """Recent admin activity: last 200 audit-log entries (newest last)."""
    return {"entries": read_audit(200)}


def _prom_escape(value: str) -> str:
    # Defense-in-depth: collection names are already constrained to
    # [A-Za-z0-9_-] by validate_collection_name, so this is a no-op today —
    # kept so a future loosening of that rule can't corrupt the exposition.
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


@router.get("/metrics/prometheus", response_class=PlainTextResponse)
async def get_metrics_prometheus(_: dict = Depends(require_admin_key)):
    """Metrics in Prometheus text exposition format (version 0.0.4).

    Scrape with a bearer token in prometheus.yml:
        authorization:
          type: Bearer
          credentials: <admin API key>
    """
    m = rag_engine.get_metrics()
    lines = [
        "# HELP rag_queries_total Total queries served since last restart.",
        "# TYPE rag_queries_total counter",
        f"rag_queries_total {m.get('query_count', 0)}",
        "# HELP rag_ingests_total Total ingest operations since last restart.",
        "# TYPE rag_ingests_total counter",
        f"rag_ingests_total {m.get('ingest_count', 0)}",
        "# HELP rag_retrieve_milliseconds_total Cumulative retrieval (vector+BM25) time.",
        "# TYPE rag_retrieve_milliseconds_total counter",
        f"rag_retrieve_milliseconds_total {m.get('total_retrieve_ms', 0):.1f}",
        "# HELP rag_rerank_milliseconds_total Cumulative cross-encoder rerank time.",
        "# TYPE rag_rerank_milliseconds_total counter",
        f"rag_rerank_milliseconds_total {m.get('total_rerank_ms', 0):.1f}",
    ]
    try:
        # O(total chunks) metadata scan — keep it off the event loop; a scrape
        # every few seconds must not stall /query or SSE streams.
        cols = await asyncio.to_thread(rag_engine.list_collections)
    except Exception:
        logger.exception("Failed to list collections for prometheus metrics")
        cols = []
    for metric, help_text, field in (
        ("rag_collection_documents", "Documents per collection.", "document_count"),
        ("rag_collection_chunks", "Stored chunks per collection.", "chunk_count"),
    ):
        lines += [f"# HELP {metric} {help_text}", f"# TYPE {metric} gauge"]
        lines += [
            f'{metric}{{collection="{_prom_escape(c["name"])}"}} {c.get(field, 0)}'
            for c in cols
        ]
    return "\n".join(lines) + "\n"
