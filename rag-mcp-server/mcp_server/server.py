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
# Set MCP_BACKEND_KEY to a dedicated admin key created for the MCP service.
MCP_BACKEND_KEY = os.environ.get("MCP_BACKEND_KEY", "")

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

SERVER_INFO = {"name": "rag-document-server", "version": "1.0.0"}
SERVER_CAPABILITIES = {
    "tools": {"listChanged": False},
    "resources": {"subscribe": False, "listChanged": False},
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
]


ADMIN_TOOLS = {"ingest_note", "submit_agent_task"}


async def handle_tool_call(name: str, arguments: dict, caller_is_admin: bool) -> dict:
    if name in ADMIN_TOOLS and not caller_is_admin:
        return {"content": [{"type": "text", "text": "Permission denied: admin API key required"}], "isError": True}
    # Use the MCP server's own credential — never forward the client's token.
    headers = {"Authorization": f"Bearer {MCP_BACKEND_KEY}"} if MCP_BACKEND_KEY else {}
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
                f"**Active API Keys:** {data['api_keys_count']}"
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
            MAX_CHARS = 100_000
            if len(content) > MAX_CHARS:
                content = content[:MAX_CHARS] + f"\n\n[Truncated — full document is {len(data['content'])} characters across {data['chunk_count']} chunks]"
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

        elif name == "list_agents":
            resp = await client.get("/api/agents/list")
            resp.raise_for_status()
            agents = resp.json().get("agents", [])
            if not agents:
                text = "No agents currently registered."
            else:
                lines = []
                for a in agents:
                    status_icon = "running" if a["status"] == "running" else a["status"]
                    lines.append(
                        f"- **{a['agent_id']}** ({a['agent_type']}) - {status_icon} | "
                        f"tasks: {a.get('tasks_completed', 0)} | heartbeat: {a.get('last_heartbeat', 'never')}"
                    )
                text = "\n".join(lines)
            return {"content": [{"type": "text", "text": text}], "isError": False}

        elif name == "get_agent_status":
            agent_id = arguments.get("agent_id", "")
            resp = await client.get(f"/api/agents/{agent_id}/status")
            resp.raise_for_status()
            a = resp.json()
            text = (
                f"**Agent:** {a['agent_id']}\n"
                f"**Type:** {a['agent_type']}\n"
                f"**Status:** {a.get('status', 'unknown')}\n"
                f"**Container:** {a.get('container_name', '')}\n"
                f"**Registered:** {a.get('registered_at', '')}\n"
                f"**Last Heartbeat:** {a.get('last_heartbeat', '')}\n"
                f"**Tasks Completed:** {a.get('tasks_completed', 0)}\n"
            )
            recent = a.get("recent_tasks", [])
            if recent:
                text += "\n**Recent Tasks:**\n"
                for t in recent:
                    text += f"- [{t['status']}] {t['description'][:80]} (id: {t['task_id']})\n"
            return {"content": [{"type": "text", "text": text}], "isError": False}

        elif name == "submit_agent_task":
            agent_id = arguments.get("agent_id", "")
            resp = await client.post(f"/api/agents/{agent_id}/tasks", json={
                "description": arguments.get("description", ""),
                "task_type": arguments.get("task_type", "query"),
                "payload": arguments.get("payload", {}),
            })
            resp.raise_for_status()
            task = resp.json()
            text = f"Task submitted to **{agent_id}**\n- Task ID: `{task['task_id']}`\n- Status: {task['status']}"
            return {"content": [{"type": "text", "text": text}], "isError": False}

        elif name == "get_agent_tasks":
            agent_id = arguments.get("agent_id", "")
            limit = min(max(int(arguments.get("limit", 10)), 1), 100)
            resp = await client.get(f"/api/agents/{agent_id}/tasks", params={"limit": limit})
            resp.raise_for_status()
            tasks = resp.json().get("tasks", [])
            if not tasks:
                text = f"No tasks found for agent **{agent_id}**."
            else:
                lines = [f"**Tasks for {agent_id}:**"]
                for t in tasks:
                    result_preview = ""
                    if t.get("result"):
                        result_preview = f" | result: {t['result'][:100]}..."
                    lines.append(f"- `{t['task_id']}` [{t['status']}] {t['description'][:60]}{result_preview}")
                text = "\n".join(lines)
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
        return {"jsonrpc": "2.0", "id": req_id, "result": {"resources": []}}
    elif method == "ping":
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


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

    if result.get("_async_tool_call"):
        params = result["params"]
        tool_name = params.get("name", "")
        tool_args = params.get("arguments", {})
        try:
            tool_result = await handle_tool_call(tool_name, tool_args, caller.get("is_admin", False))
        except Exception:
            logger.exception("Tool call failed: %s", tool_name)
            tool_result = {"content": [{"type": "text", "text": "Tool execution failed"}], "isError": True}
        response = {"jsonrpc": "2.0", "id": result["id"], "result": tool_result}
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

    if result.get("_async_tool_call"):
        params = result["params"]
        tool_name = params.get("name", "")
        tool_args = params.get("arguments", {})
        try:
            tool_result = await handle_tool_call(tool_name, tool_args, caller.get("is_admin", False))
        except Exception:
            logger.exception("Tool call failed: %s", tool_name)
            tool_result = {"content": [{"type": "text", "text": "Tool execution failed"}], "isError": True}
        return {"jsonrpc": "2.0", "id": result["id"], "result": tool_result}

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
        "tools": [{"name": t["name"], "description": t["description"]} for t in TOOLS],
        "transports": ["sse", "streamable-http"],
        "auth": "Bearer token (API key)",
    }
