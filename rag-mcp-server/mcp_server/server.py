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
import re
import uuid
from pathlib import Path
from typing import AsyncGenerator
from urllib.parse import quote

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
    {
        "name": "list_agents",
        "description": "List all deployed AI agents on the server with their current status, type, and last heartbeat time.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_agent_status",
        "description": "Get detailed status of a specific agent including recent tasks and health information.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string", "description": "The agent identifier (e.g., 'doc-summarizer', 'task-runner')"},
            },
            "required": ["agent_id"],
        },
    },
    {
        "name": "submit_agent_task",
        "description": "Submit a task to a specific agent for execution. The task-runner agent accepts arbitrary tasks processed by Claude. Requires an admin-tier API key.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string", "description": "Target agent identifier"},
                "description": {"type": "string", "description": "Human-readable task description"},
                "task_type": {"type": "string", "description": "Task type: 'shell', 'api_call', or 'query'", "default": "query"},
                "payload": {"type": "object", "description": "Task-specific parameters", "default": {}},
            },
            "required": ["agent_id", "description"],
        },
    },
    {
        "name": "get_agent_tasks",
        "description": "Get recent tasks for a specific agent with their status and results.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string", "description": "The agent identifier"},
                "limit": {"type": "integer", "description": "Number of recent tasks to return (default: 10)", "default": 10},
            },
            "required": ["agent_id"],
        },
    },
    {
        "name": "create_collection",
        "description": "Create a new document collection. Requires an admin-tier API key.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Collection name (1-64 chars: letters, digits, '_' or '-')"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "delete_collection",
        "description": "Delete a document collection and all its indexed chunks. The 'default' collection cannot be deleted. Requires an admin-tier API key.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Collection name to delete"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "delete_document",
        "description": "Delete a document (all its indexed chunks and the stored file) from a collection by source name. Requires an admin-tier API key.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "source": {"type": "string", "description": "The document's source name as shown by list_documents (e.g. 'manual.pdf')"},
                "collection": {"type": "string", "description": "Collection name (default: 'default')", "default": "default"},
            },
            "required": ["source"],
        },
    },
    {
        "name": "get_collection_stats",
        "description": "Get per-collection statistics: unique document count and indexed chunk count. Optionally filter to a single collection.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "collection": {"type": "string", "description": "Optional collection name; omit for all collections"},
            },
        },
    },
]


ADMIN_TOOLS = {"ingest_note", "submit_agent_task", "get_agent_tasks", "create_collection", "delete_collection", "delete_document"}

# Tools that read a specific collection — subject to per-collection ACLs on
# scoped (non-admin) keys. The backend can't enforce this for MCP calls because
# we call it with the admin service key, so the check must happen here.
# Any new tool whose inputSchema takes a `collection` must join this set.
_COLLECTION_SCOPED_TOOLS = {"search_documents", "list_documents", "get_document", "ask_documents"}


def _acl_for(caller_is_admin: bool, caller_collections: list | None) -> list | None:
    """The caller's collection allowlist, or None if unrestricted (admin or
    unscoped key). Single source of truth for ACL semantics in this service —
    mirrors backend security.allowed_collections."""
    return None if caller_is_admin else (caller_collections or None)


def _deny_collection(name: str) -> dict:
    return {
        "content": [{
            "type": "text",
            "text": f"Permission denied: this API key cannot access collection '{name}'",
        }],
        "isError": True,
    }


def _filter_by_acl(cols: list, acl: list | None) -> list:
    """Filter collection dicts (or name strings) down to the caller's scope."""
    if not acl:
        return cols
    return [c for c in cols if (c.get("name") if isinstance(c, dict) else c) in acl]


# Mirrors backend validate_collection_name


async def handle_tool_call(
    name: str,
    arguments: dict,
    caller_is_admin: bool,
    caller_collections: list | None = None,
) -> dict:
    if name in ADMIN_TOOLS and not caller_is_admin:
        return {"content": [{"type": "text", "text": "Permission denied: admin API key required"}], "isError": True}
    # Per-collection ACL: scoped keys may only read their allowed collections.
    acl = _acl_for(caller_is_admin, caller_collections)
    if acl and name in _COLLECTION_SCOPED_TOOLS:
        wanted = arguments.get("collection", "default")
        if wanted not in acl:
            return _deny_collection(wanted)
    if acl and name == "get_collection_stats":
        wanted = arguments.get("collection")
        if wanted and wanted not in acl:
            return _deny_collection(wanted)
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

        elif name == "ask_documents":
            # LLM generation can take far longer than the default client
            # timeout (prompt eval + token generation on a 14B model).
            resp = await client.post("/api/documents/ask", json={
                "query": arguments.get("query", ""),
                "collection": arguments.get("collection", "default"),
                "n_results": min(max(int(arguments.get("n_results", 5)), 1), 50),
            }, timeout=300.0)
            resp.raise_for_status()
            data = resp.json()
            answer = data.get("answer", "")
            sources = data.get("sources", [])
            src_lines = "\n".join(
                f"- {s.get('source', '?')} (score: {s.get('score', 0)})" for s in sources
            )
            text = (
                (answer or "No LLM answer available — showing search results only.")
                + (f"\n\n**Sources:**\n{src_lines}" if src_lines else "")
            )
            return {
                "content": [
                    {"type": "text", "text": text},
                    {"type": "text", "text": json.dumps(data)},
                ],
                "isError": False,
            }

        elif name == "list_collections":
            resp = await client.get("/api/documents/collections")
            resp.raise_for_status()
            data = resp.json()
            collections = _filter_by_acl(data["collections"], acl)
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
            if acl:
                # Scoped key: hide out-of-scope collection names and the
                # aggregate counts that span collections outside its scope.
                data["collections"] = _filter_by_acl(data.get("collections", []), acl)
                data.pop("total_documents", None)
                data.pop("active_credentials", None)
            parts = [
                f"**Server:** {data['hostname']} ({data['ip']})",
                f"**MCP Enabled:** {data['mcp_enabled']}",
            ]
            if "total_documents" in data:
                parts.append(f"**Total Documents:** {data['total_documents']}")
            parts.append(f"**Collections:** {', '.join(data['collections']) or 'none'}")
            if "active_credentials" in data:
                parts.append(f"**Active API Keys:** {data['active_credentials']}")
            text = "\n".join(parts)
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

        elif name == "create_collection":
            coll_name = arguments.get("name", "")
            bad = _reject_bad_name(coll_name)
            if bad:
                return bad
            resp = await client.post(f"/api/documents/collections/{coll_name}")
            if resp.status_code == 400:
                detail = _detail(resp)
                return {"content": [{"type": "text", "text": f"Invalid collection name: {detail}"}], "isError": True}
            resp.raise_for_status()
            data = resp.json()
            text = f"Created collection **{data['name']}**."
            return {
                "content": [
                    {"type": "text", "text": text},
                    {"type": "text", "text": json.dumps(data)},
                ],
                "isError": False,
            }

        elif name == "delete_collection":
            coll_name = arguments.get("name", "")
            bad = _reject_bad_name(coll_name)
            if bad:
                return bad
            resp = await client.delete(f"/api/documents/collections/{coll_name}")
            if resp.status_code == 400:
                # Backend rejects "default" with 400 and surfaces a detail message —
                # propagate it so the LLM can explain the constraint to the user.
                detail = _detail(resp)
                return {"content": [{"type": "text", "text": detail}], "isError": True}
            resp.raise_for_status()
            data = resp.json()
            text = f"Deleted collection **{data['name']}**."
            return {
                "content": [
                    {"type": "text", "text": text},
                    {"type": "text", "text": json.dumps(data)},
                ],
                "isError": False,
            }

        elif name == "delete_document":
            collection = arguments.get("collection", "default")
            bad = _reject_bad_name(collection)
            if bad:
                return bad
            source = arguments.get("source", "")
            if not source:
                return {"content": [{"type": "text", "text": "A 'source' argument is required."}], "isError": True}
            # quote(safe='') encodes any '/' so the source can't traverse the URL path.
            resp = await client.delete(
                f"/api/documents/{quote(source, safe='')}",
                params={"collection": collection},
            )
            if resp.status_code >= 400:
                return {"content": [{"type": "text", "text": _detail(resp)}], "isError": True}
            resp.raise_for_status()
            data = resp.json()
            n = data.get("deleted_chunks", 0)
            text = (f"Deleted **{data.get('filename', source)}** from **{collection}** "
                    f"({n} chunk{'s' if n != 1 else ''} removed)." if n
                    else f"No document named **{source}** was found in **{collection}**.")
            return {
                "content": [
                    {"type": "text", "text": text},
                    {"type": "text", "text": json.dumps(data)},
                ],
                "isError": False,
            }

        elif name == "get_collection_stats":
            resp = await client.get("/api/documents/collections")
            resp.raise_for_status()
            cols = _filter_by_acl(resp.json().get("collections", []), acl)
            wanted = arguments.get("collection")
            if wanted:
                cols = [c for c in cols if c.get("name") == wanted]
                if not cols:
                    return {"content": [{"type": "text", "text": f"Collection '{wanted}' not found."}], "isError": True}
            lines = [
                f"- **{c['name']}**: {c.get('document_count', 0)} documents, {c.get('chunk_count', 0)} chunks"
                for c in cols
            ]
            text = "\n".join(lines) or "No collections."
            return {
                "content": [
                    {"type": "text", "text": text},
                    {"type": "text", "text": json.dumps(cols)},
                ],
                "isError": False,
            }

        return {"content": [{"type": "text", "text": f"Unknown tool: {name}"}], "isError": True}


# Client-side collection-name guard: httpx normalizes `..` path segments, so an
# unvalidated name interpolated into the URL could redirect a request to a backend
# admin endpoint (api-keys, mcp/toggle) carrying the server's admin MCP_BACKEND_KEY.
# Validate before making ANY HTTP call.
_COLLECTION_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _reject_bad_name(name: str):
    """Return an isError tool result if the collection name is invalid, else None."""
    if not _COLLECTION_NAME_RE.match(name or ""):
        return {
            "content": [{
                "type": "text",
                "text": "Collection name must be 1-64 characters: letters, digits, '_' or '-'.",
            }],
            "isError": True,
        }
    return None


def _detail(resp) -> str:
    """Extract FastAPI's {'detail': ...} message, falling back to status text."""
    try:
        return str(resp.json().get("detail", resp.text))
    except Exception:
        return resp.text or f"HTTP {resp.status_code}"


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


async def _handle_async_jsonrpc(
    result: dict,
    caller_is_admin: bool,
    caller_collections: list | None = None,
) -> dict:
    """Handle async JSON-RPC calls (tools, resources, prompts)."""
    # Per-collection ACL for scoped (non-admin) keys; None = unrestricted.
    acl = _acl_for(caller_is_admin, caller_collections)
    headers = _backend_headers()
    async with httpx.AsyncClient(base_url=BACKEND_URL, timeout=60.0, headers=headers) as client:
        if result.get("_async_tool_call"):
            params = result["params"]
            tool_name = params.get("name", "")
            tool_args = params.get("arguments", {})
            try:
                tool_result = await handle_tool_call(
                    tool_name, tool_args, caller_is_admin, caller_collections=caller_collections
                )
            except Exception:
                logger.exception("Tool call failed: %s", tool_name)
                tool_result = {"content": [{"type": "text", "text": "Tool execution failed"}], "isError": True}
            return {"jsonrpc": "2.0", "id": result["id"], "result": tool_result}

        elif result.get("_async_resource_list"):
            try:
                resp = await client.get("/api/documents/collections")
                resp.raise_for_status()
                collections = _filter_by_acl(resp.json().get("collections", []), acl)
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
            if acl and collection not in acl:
                return {"jsonrpc": "2.0", "id": result["id"], "error": {
                    "code": -32602,
                    "message": f"Permission denied: this API key cannot access collection '{collection}'",
                }}
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
        response = await _handle_async_jsonrpc(result, caller.get("is_admin", False), caller.get("collections"))
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
        return await _handle_async_jsonrpc(result, caller.get("is_admin", False), caller.get("collections"))

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
