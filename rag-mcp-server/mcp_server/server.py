"""
MCP (Model Context Protocol) Server with SSE transport.
Provides RAG-powered document search tools to cloud-based LLMs.
Authenticates via API key in the Authorization header.
"""

import asyncio
import hashlib
import hmac
import json
import logging
import os
import uuid
from pathlib import Path
from typing import AsyncGenerator

import httpx
from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse
from starlette.middleware.base import BaseHTTPMiddleware

logging.basicConfig(level=logging.INFO, format="%(asctime)s [MCP] %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

BACKEND_URL = os.environ.get("BACKEND_URL", "http://backend:8000")
CONFIG_DIR = os.environ.get("CONFIG_DIR", "/app/data/config")
CONFIG_FILE = Path(CONFIG_DIR) / "server_config.json"

# Server-issued credential for backend calls (spec forbids forwarding client tokens).
# Prefer an explicit MCP_BACKEND_KEY env var; otherwise fall back to the
# auto-provisioned service key the backend writes to the shared config volume.
MCP_BACKEND_KEY = os.environ.get("MCP_BACKEND_KEY", "")
SERVICE_KEY_FILE = Path(CONFIG_DIR) / "mcp_service.key"


def _backend_headers() -> dict:
    """Build the Authorization header for backend calls. Reads the auto-provisioned
    service key fresh so backend key rotation/regeneration is picked up."""
    key = MCP_BACKEND_KEY
    if not key:
        try:
            if SERVICE_KEY_FILE.exists():
                key = SERVICE_KEY_FILE.read_text().strip()
        except Exception:
            key = ""
    return {"Authorization": f"Bearer {key}"} if key else {}

CORS_ORIGINS = [o.strip() for o in os.environ.get("CORS_ALLOWED_ORIGINS", "http://192.168.1.52:8902,http://localhost:8902,https://192.168.1.52:8943,https://localhost:8943").split(",") if o.strip()]
ALLOWED_HOSTS = {h.strip() for h in os.environ.get("ALLOWED_HOSTS", "192.168.1.52:8901,192.168.1.52:8902,192.168.1.52:8943,localhost:8901,localhost:8902,localhost:8943,mcp-server:8001").split(",") if h.strip()}

app = FastAPI(title="RAG MCP Server", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept"],
    max_age=600,
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response


app.add_middleware(SecurityHeadersMiddleware)

# Store active SSE sessions: session_id -> {"active": bool, "queue": asyncio.Queue}
sessions: dict[str, dict] = {}


def load_config() -> dict:
    if CONFIG_FILE.exists():
        return json.loads(CONFIG_FILE.read_text())
    return {"api_keys": [], "mcp_enabled": True}


def validate_api_key(key: str) -> dict | None:
    """Return the key entry on success (includes is_admin), None on failure."""
    if not key:
        return None
    config = load_config()
    if not config.get("mcp_enabled", True):
        return None
    hashed = hashlib.sha256(key.encode()).hexdigest()
    match = None
    for entry in config.get("api_keys", []):
        if not entry.get("active", True):
            continue
        if hmac.compare_digest(entry["key_hash"], hashed):
            match = entry
    return match


def get_api_key(authorization: str | None) -> dict:
    """Validate and return the key entry dict. Raises 401/403."""
    if not authorization:
        raise HTTPException(401, "Missing Authorization header")
    key = authorization[7:].strip() if authorization.startswith("Bearer ") else authorization.strip()
    entry = validate_api_key(key)
    if not entry:
        raise HTTPException(403, "Invalid or inactive API key")
    return entry


def check_origin(request: Request) -> None:
    """Reject requests with browser Origin header not in allowlist (DNS rebinding / CSRF defense)."""
    origin = request.headers.get("origin")
    if origin is None:
        # Non-browser clients (LLMs, curl) typically omit Origin; allow these.
        return
    for allowed in CORS_ORIGINS:
        if origin == allowed:
            return
    raise HTTPException(403, "Origin not allowed")


# --- MCP Protocol Implementation ---

# Cap for document content returned through any MCP path (get_document tool
# and resources/read) — protects clients from unbounded payloads.
MAX_DOCUMENT_CHARS = 100_000

SERVER_INFO = {"name": "rag-document-server", "version": "1.0.0"}
SERVER_CAPABILITIES = {
    "tools": {"listChanged": False},
    "resources": {"subscribe": False, "listChanged": False},
    "prompts": {"listChanged": False},
}

TOOLS = [
    {
        "name": "search_documents",
        "description": "Search through indexed documents using semantic similarity. Returns relevant document chunks with source information and relevance scores.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query to find relevant documents"},
                "collection": {"type": "string", "description": "Document collection to search in (default: 'default')", "default": "default"},
                "n_results": {"type": "integer", "description": "Number of results to return (default: 5)", "default": 5},
            },
            "required": ["query"],
        },
    },
    {
        "name": "list_collections",
        "description": "List all available document collections with their document counts.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_documents",
        "description": "List all documents in a specific collection.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "collection": {"type": "string", "description": "Collection name (default: 'default')", "default": "default"},
            },
        },
    },
    {
        "name": "get_server_status",
        "description": "Get the current status of the RAG server including document counts and available collections.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_document",
        "description": "Retrieve the full content of a specific document by its source name. Returns all chunks joined in order.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "source": {"type": "string", "description": "The source identifier of the document (e.g. filename or smb:// path)"},
                "collection": {"type": "string", "description": "Collection name (default: 'default')", "default": "default"},
            },
            "required": ["source"],
        },
    },
    {
        "name": "ingest_note",
        "description": "Ingest a text note or document into the RAG system. Requires an admin-tier API key.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "The text content to ingest"},
                "source": {"type": "string", "description": "A name/identifier for this text (e.g. 'meeting-notes-2025-05-30')"},
                "collection": {"type": "string", "description": "Collection to ingest into (default: 'default')", "default": "default"},
            },
            "required": ["text", "source"],
        },
    },
    {
        "name": "ask_documents",
        "description": "Search documents and generate an answer using the local LLM. Returns a generated answer with cited sources. Falls back to search-only if no LLM is available.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The question to answer from the documents"},
                "collection": {"type": "string", "description": "Collection to search (default: 'default')", "default": "default"},
                "n_results": {"type": "integer", "description": "Number of source chunks to use (default: 5)", "default": 5},
            },
            "required": ["query"],
        },
    },
]


ADMIN_TOOLS = {"ingest_note"}


async def handle_tool_call(name: str, arguments: dict, caller_is_admin: bool) -> dict:
    if name in ADMIN_TOOLS and not caller_is_admin:
        return {"content": [{"type": "text", "text": "Permission denied: admin API key required"}], "isError": True}
    # Use the MCP server's own credential — never forward the client's token.
    headers = _backend_headers()
    async with httpx.AsyncClient(base_url=BACKEND_URL, timeout=60.0, headers=headers) as client:
        if name == "search_documents":
            resp = await client.post("/api/documents/query", json={
                "query": arguments.get("query", ""),
                "collection": arguments.get("collection", "default"),
                "n_results": min(max(int(arguments.get("n_results", 5)), 1), 50),
            })
            resp.raise_for_status()
            data = resp.json()
            results = data["results"]
            results_text = [
                f"**Source:** {r['source']} (score: {r['score']})\n{r['content']}"
                for r in results
            ]
            return {
                "content": [
                    {"type": "text", "text": "\n\n---\n\n".join(results_text) or "No results found."},
                    {"type": "text", "text": json.dumps(results)},
                ],
                "isError": False,
            }

        elif name == "list_collections":
            resp = await client.get("/api/documents/collections")
            resp.raise_for_status()
            data = resp.json()
            collections = data["collections"]
            text = "\n".join(
                f"- **{c['name']}**: {c['document_count']} documents"
                for c in collections
            ) or "No collections found."
            names = [c["name"] for c in collections]
            return {
                "content": [
                    {"type": "text", "text": text},
                    {"type": "text", "text": json.dumps(names)},
                ],
                "isError": False,
            }

        elif name == "list_documents":
            collection = arguments.get("collection", "default")
            resp = await client.get("/api/documents/list", params={"collection": collection})
            resp.raise_for_status()
            data = resp.json()
            docs = data.get("documents", [])
            text = "\n".join(f"- {d}" for d in docs) or "No documents in this collection."
            return {
                "content": [
                    {"type": "text", "text": text},
                    {"type": "text", "text": json.dumps(docs)},
                ],
                "isError": False,
            }

        elif name == "get_server_status":
            resp = await client.get("/api/admin/status")
            resp.raise_for_status()
            data = resp.json()
            text = (
                f"**Server:** {data['hostname']} ({data['ip']})\n"
                f"**MCP Enabled:** {data['mcp_enabled']}\n"
                f"**Total Documents:** {data['total_documents']}\n"
                f"**Collections:** {', '.join(data['collections']) or 'none'}\n"
                f"**Active API Keys:** {data['active_credentials']}"
            )
            return {
                "content": [
                    {"type": "text", "text": text},
                    {"type": "text", "text": json.dumps(data)},
                ],
                "isError": False,
            }

        elif name == "get_document":
            source = arguments.get("source", "")
            collection = arguments.get("collection", "default")
            resp = await client.get("/api/documents/content", params={"source": source, "collection": collection})
            if resp.status_code == 404:
                return {"content": [{"type": "text", "text": f"Document not found: {source}"}], "isError": False}
            resp.raise_for_status()
            data = resp.json()
            content = data.get("content", "")
            if len(content) > MAX_DOCUMENT_CHARS:
                content = content[:MAX_DOCUMENT_CHARS] + f"\n\n[Truncated — full document is {len(data['content'])} characters across {data['chunk_count']} chunks]"
            text = f"**Source:** {data['source']} ({data['chunk_count']} chunks)\n**Collection:** {data['collection']}\n\n{content}"
            return {"content": [{"type": "text", "text": text}], "isError": False}

        elif name == "ingest_note":
            note_text = arguments.get("text", "")
            source = arguments.get("source", "")
            collection = arguments.get("collection", "default")
            resp = await client.post("/api/documents/ingest-text", json={
                "text": note_text, "source": source, "collection": collection,
            })
            if resp.status_code == 403:
                return {"content": [{"type": "text", "text": "Permission denied: admin API key required to ingest documents"}], "isError": True}
            resp.raise_for_status()
            data = resp.json()
            text = f"Ingested **{data['source']}** into collection **{data['collection']}**: {data['chunks_created']} chunks created."
            return {"content": [{"type": "text", "text": text}], "isError": False}

        elif name == "ask_documents":
            resp = await client.post("/api/documents/ask", json={
                "query": arguments.get("query", ""),
                "collection": arguments.get("collection", "default"),
                "n_results": min(max(int(arguments.get("n_results", 5)), 1), 50),
            })
            resp.raise_for_status()
            data = resp.json()
            parts = []
            if data.get("answer"):
                parts.append(f"**Answer** (via {data.get('model', 'LLM')}):\n{data['answer']}")
            if data.get("sources"):
                parts.append("**Sources:**")
                for s in data["sources"]:
                    cite = f"- {s['source']} (score: {s['score']})"
                    if s.get("page"):
                        cite += f" p.{s['page']}"
                    if s.get("section"):
                        cite += f" {s['section']}"
                    parts.append(cite)
            text = "\n\n".join(parts) if parts else "No results found."
            return {"content": [{"type": "text", "text": text}], "isError": False}

        return {"content": [{"type": "text", "text": f"Unknown tool: {name}"}], "isError": True}


def handle_jsonrpc(request_body: dict) -> dict | None:
    method = request_body.get("method", "")
    req_id = request_body.get("id")
    params = request_body.get("params", {})

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": SERVER_CAPABILITIES,
                "serverInfo": SERVER_INFO,
            },
        }
    elif method == "notifications/initialized":
        return None
    elif method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOLS}}
    elif method == "tools/call":
        return {"_async_tool_call": True, "id": req_id, "params": params}
    elif method == "resources/list":
        return {"_async_resource_list": True, "id": req_id}
    elif method == "resources/read":
        return {"_async_resource_read": True, "id": req_id, "params": params}
    elif method == "resources/templates/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "resourceTemplates": [
                    {
                        "uriTemplate": "rag://collections/{collection}/documents/{source}",
                        "name": "RAG Document",
                        "description": "Full content of a document in a collection",
                        "mimeType": "text/plain",
                    },
                ],
            },
        }
    elif method == "prompts/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "prompts": [
                    {
                        "name": "summarize_document",
                        "description": "Summarize a specific document from the RAG",
                        "arguments": [
                            {"name": "source", "description": "Document source name", "required": True},
                            {"name": "collection", "description": "Collection name", "required": False},
                        ],
                    },
                    {
                        "name": "compare_documents",
                        "description": "Compare two documents from the RAG",
                        "arguments": [
                            {"name": "source_a", "description": "First document source", "required": True},
                            {"name": "source_b", "description": "Second document source", "required": True},
                            {"name": "collection", "description": "Collection name", "required": False},
                        ],
                    },
                ],
            },
        }
    elif method == "prompts/get":
        return {"_async_prompt_get": True, "id": req_id, "params": params}
    elif method == "ping":
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


async def _handle_async_jsonrpc(result: dict, caller_is_admin: bool) -> dict:
    """Handle async JSON-RPC calls (tools, resources, prompts)."""
    headers = _backend_headers()
    async with httpx.AsyncClient(base_url=BACKEND_URL, timeout=60.0, headers=headers) as client:
        if result.get("_async_tool_call"):
            params = result["params"]
            tool_name = params.get("name", "")
            tool_args = params.get("arguments", {})
            try:
                tool_result = await handle_tool_call(tool_name, tool_args, caller_is_admin)
            except Exception:
                logger.exception("Tool call failed: %s", tool_name)
                tool_result = {"content": [{"type": "text", "text": "Tool execution failed"}], "isError": True}
            return {"jsonrpc": "2.0", "id": result["id"], "result": tool_result}

        elif result.get("_async_resource_list"):
            try:
                resp = await client.get("/api/documents/collections")
                resp.raise_for_status()
                collections = resp.json().get("collections", [])
                resources = []
                for col in collections:
                    resp2 = await client.get("/api/documents/list", params={"collection": col["name"]})
                    if resp2.status_code == 200:
                        for doc in resp2.json().get("documents", []):
                            resources.append({
                                "uri": f"rag://collections/{col['name']}/documents/{doc}",
                                "name": doc,
                                "description": f"Document in collection '{col['name']}'",
                                "mimeType": "text/plain",
                            })
                return {"jsonrpc": "2.0", "id": result["id"], "result": {"resources": resources}}
            except Exception:
                logger.exception("Resource list failed")
                return {"jsonrpc": "2.0", "id": result["id"], "result": {"resources": []}}

        elif result.get("_async_resource_read"):
            uri = result["params"].get("uri", "")
            import re
            m = re.match(r"rag://collections/([^/]+)/documents/(.+)", uri)
            if not m:
                return {"jsonrpc": "2.0", "id": result["id"], "error": {"code": -32602, "message": f"Invalid resource URI: {uri}"}}
            collection, source = m.group(1), m.group(2)
            try:
                resp = await client.get("/api/documents/content", params={"source": source, "collection": collection})
                if resp.status_code == 404:
                    return {"jsonrpc": "2.0", "id": result["id"], "error": {"code": -32602, "message": "Document not found"}}
                resp.raise_for_status()
                data = resp.json()
                text = data.get("content", "")
                # Same cap as the get_document tool — an authenticated client must
                # not be able to force unbounded allocation through resources/read.
                if len(text) > MAX_DOCUMENT_CHARS:
                    text = text[:MAX_DOCUMENT_CHARS] + (
                        f"\n\n[Truncated — full document is {len(data.get('content', ''))} characters "
                        f"across {data.get('chunk_count', '?')} chunks]"
                    )
                return {"jsonrpc": "2.0", "id": result["id"], "result": {
                    "contents": [{"uri": uri, "mimeType": "text/plain", "text": text}],
                }}
            except Exception:
                logger.exception("Resource read failed: %s", uri)
                return {"jsonrpc": "2.0", "id": result["id"], "error": {"code": -32603, "message": "Resource read failed"}}

        elif result.get("_async_prompt_get"):
            name = result["params"].get("name", "")
            args = result["params"].get("arguments", {})
            collection = args.get("collection", "default")
            if name == "summarize_document":
                source = args.get("source", "")
                return {"jsonrpc": "2.0", "id": result["id"], "result": {
                    "messages": [
                        {"role": "user", "content": {"type": "text", "text": f"Please summarize the following document from collection '{collection}': {source}\n\nUse the search_documents or get_document tool to retrieve its content, then provide a clear, structured summary."}},
                    ],
                }}
            elif name == "compare_documents":
                a, b = args.get("source_a", ""), args.get("source_b", "")
                return {"jsonrpc": "2.0", "id": result["id"], "result": {
                    "messages": [
                        {"role": "user", "content": {"type": "text", "text": f"Compare these two documents from collection '{collection}':\n1. {a}\n2. {b}\n\nUse get_document to retrieve both, then provide a structured comparison of their content, similarities, and differences."}},
                    ],
                }}
            return {"jsonrpc": "2.0", "id": result["id"], "error": {"code": -32602, "message": f"Unknown prompt: {name}"}}

    return {"jsonrpc": "2.0", "id": result["id"], "error": {"code": -32603, "message": "Unhandled async call"}}


# --- HTTP + SSE Transport ---

@app.get("/sse")
async def sse_endpoint(request: Request, authorization: str | None = Header(None)):
    check_origin(request)
    get_api_key(authorization)

    session_id = str(uuid.uuid4())
    queue: asyncio.Queue = asyncio.Queue()
    sessions[session_id] = {"active": True, "queue": queue}
    logger.info(f"New SSE session: {session_id}")

    async def event_generator() -> AsyncGenerator:
        yield {"event": "endpoint", "data": f"/messages?session_id={session_id}"}
        try:
            while sessions.get(session_id, {}).get("active", False):
                try:
                    response = await asyncio.wait_for(queue.get(), timeout=1.0)
                    yield {"event": "message", "data": json.dumps(response)}
                except asyncio.TimeoutError:
                    continue
        except asyncio.CancelledError:
            pass
        finally:
            sessions.pop(session_id, None)
            logger.info(f"SSE session ended: {session_id}")

    return EventSourceResponse(event_generator())


@app.post("/messages")
async def handle_message(
    request: Request,
    session_id: str,
    authorization: str | None = Header(None),
):
    check_origin(request)
    caller = get_api_key(authorization)

    if session_id not in sessions:
        raise HTTPException(404, "Session not found")

    body = await request.json()
    logger.info(f"Received message: {body.get('method', 'unknown')}")

    result = handle_jsonrpc(body)
    if result is None:
        return {"status": "ok"}

    is_async = any(result.get(k) for k in ("_async_tool_call", "_async_resource_list", "_async_resource_read", "_async_prompt_get"))
    if is_async:
        response = await _handle_async_jsonrpc(result, caller.get("is_admin", False))
    else:
        response = result

    if session_id in sessions:
        await sessions[session_id]["queue"].put(response)

    return Response(status_code=202)


# --- Streamable HTTP Transport (newer MCP spec) ---

@app.post("/mcp")
async def mcp_streamable(request: Request, authorization: str | None = Header(None)):
    check_origin(request)
    caller = get_api_key(authorization)

    body = await request.json()
    logger.info(f"MCP streamable request: {body.get('method', 'unknown')}")

    result = handle_jsonrpc(body)
    if result is None:
        return Response(status_code=204)

    is_async = any(result.get(k) for k in ("_async_tool_call", "_async_resource_list", "_async_resource_read", "_async_prompt_get"))
    if is_async:
        return await _handle_async_jsonrpc(result, caller.get("is_admin", False))

    return result


@app.get("/health")
async def health():
    return {"status": "ok", "service": "mcp-server"}


@app.get("/mcp/info")
async def mcp_info():
    """Public endpoint showing MCP server capabilities."""
    return {
        "name": SERVER_INFO["name"],
        "version": SERVER_INFO["version"],
        "protocol_version": "2024-11-05",
        "capabilities": ["tools", "resources", "prompts"],
        "tools": [{"name": t["name"], "description": t["description"]} for t in TOOLS],
        "prompts": ["summarize_document", "compare_documents"],
        "resources": "rag://collections/{collection}/documents/{source}",
        "transports": ["sse", "streamable-http"],
        "auth": "Bearer token (API key)",
    }


@app.get("/.well-known/mcp")
async def well_known_mcp():
    """Discovery server card (SEP-1649 style)."""
    return {
        "name": SERVER_INFO["name"],
        "version": SERVER_INFO["version"],
        "protocolVersion": "2024-11-05",
        "capabilities": list(SERVER_CAPABILITIES.keys()),
        "endpoints": {"sse": "/sse", "streamableHttp": "/mcp"},
        "authentication": {"type": "bearer", "description": "API key as Bearer token"},
    }
