"""
MCP (Model Context Protocol) Server with SSE transport.
Provides RAG-powered document search tools to cloud-based LLMs.
Authenticates via API key in the Authorization header.
"""

import asyncio
import hashlib
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

logging.basicConfig(level=logging.INFO, format="%(asctime)s [MCP] %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

BACKEND_URL = os.environ.get("BACKEND_URL", "http://backend:8000")
CONFIG_DIR = os.environ.get("CONFIG_DIR", "/app/data/config")
CONFIG_FILE = Path(CONFIG_DIR) / "server_config.json"

app = FastAPI(title="RAG MCP Server", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Store active SSE sessions: session_id -> {"active": bool, "queue": asyncio.Queue}
sessions: dict[str, dict] = {}


def load_config() -> dict:
    if CONFIG_FILE.exists():
        return json.loads(CONFIG_FILE.read_text())
    return {"api_keys": [], "mcp_enabled": True}


def validate_api_key(key: str) -> bool:
    config = load_config()
    if not config.get("mcp_enabled", True):
        return False
    hashed = hashlib.sha256(key.encode()).hexdigest()
    for entry in config.get("api_keys", []):
        if entry["key_hash"] == hashed and entry.get("active", True):
            return True
    return False


def get_api_key(authorization: str | None) -> str:
    if not authorization:
        raise HTTPException(401, "Missing Authorization header")
    if authorization.startswith("Bearer "):
        key = authorization[7:]
    else:
        key = authorization
    if not validate_api_key(key):
        raise HTTPException(403, "Invalid or inactive API key")
    return key


# --- MCP Protocol Implementation ---

SERVER_INFO = {
    "name": "rag-document-server",
    "version": "1.0.0",
}

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
                "query": {
                    "type": "string",
                    "description": "The search query to find relevant documents",
                },
                "collection": {
                    "type": "string",
                    "description": "Document collection to search in (default: 'default')",
                    "default": "default",
                },
                "n_results": {
                    "type": "integer",
                    "description": "Number of results to return (default: 5)",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "list_collections",
        "description": "List all available document collections with their document counts.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "list_documents",
        "description": "List all documents in a specific collection.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "collection": {
                    "type": "string",
                    "description": "Collection name (default: 'default')",
                    "default": "default",
                },
            },
        },
    },
    {
        "name": "get_server_status",
        "description": "Get the current status of the RAG server including document counts and available collections.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
]


async def handle_tool_call(name: str, arguments: dict) -> dict:
    async with httpx.AsyncClient(base_url=BACKEND_URL, timeout=60.0) as client:
        if name == "search_documents":
            resp = await client.post("/api/documents/query", json={
                "query": arguments["query"],
                "collection": arguments.get("collection", "default"),
                "n_results": arguments.get("n_results", 5),
            })
            resp.raise_for_status()
            data = resp.json()
            results_text = []
            for r in data["results"]:
                results_text.append(
                    f"**Source:** {r['source']} (score: {r['score']})\n{r['content']}"
                )
            return {
                "content": [{"type": "text", "text": "\n\n---\n\n".join(results_text) or "No results found."}],
                "isError": False,
            }

        elif name == "list_collections":
            resp = await client.get("/api/documents/collections")
            resp.raise_for_status()
            data = resp.json()
            text = "\n".join(
                f"- **{c['name']}**: {c['document_count']} documents"
                for c in data["collections"]
            ) or "No collections found."
            return {"content": [{"type": "text", "text": text}], "isError": False}

        elif name == "list_documents":
            collection = arguments.get("collection", "default")
            resp = await client.get(f"/api/documents/list?collection={collection}")
            resp.raise_for_status()
            data = resp.json()
            docs = data.get("documents", [])
            text = "\n".join(f"- {d}" for d in docs) or "No documents in this collection."
            return {"content": [{"type": "text", "text": text}], "isError": False}

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
            return {"content": [{"type": "text", "text": text}], "isError": False}

        else:
            return {
                "content": [{"type": "text", "text": f"Unknown tool: {name}"}],
                "isError": True,
            }


def handle_jsonrpc(request_body: dict) -> dict:
    """Process a JSON-RPC request and return the response skeleton."""
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
        return None  # No response for notifications
    elif method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"tools": TOOLS},
        }
    elif method == "tools/call":
        # Handled async separately
        return {"_async_tool_call": True, "id": req_id, "params": params}
    elif method == "resources/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"resources": []},
        }
    elif method == "ping":
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}
    else:
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }


# --- HTTP + SSE Transport ---

@app.get("/sse")
async def sse_endpoint(request: Request, authorization: str | None = Header(None)):
    get_api_key(authorization)

    session_id = str(uuid.uuid4())
    queue: asyncio.Queue = asyncio.Queue()
    sessions[session_id] = {"active": True, "queue": queue}
    logger.info(f"New SSE session: {session_id}")

    async def event_generator() -> AsyncGenerator:
        # Send the endpoint URL for the client to POST messages to
        yield {
            "event": "endpoint",
            "data": f"/messages?session_id={session_id}",
        }

        try:
            while sessions.get(session_id, {}).get("active", False):
                try:
                    response = await asyncio.wait_for(queue.get(), timeout=1.0)
                    yield {
                        "event": "message",
                        "data": json.dumps(response),
                    }
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
    get_api_key(authorization)

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
            tool_result = await handle_tool_call(tool_name, tool_args)
        except Exception as e:
            tool_result = {
                "content": [{"type": "text", "text": f"Error: {str(e)}"}],
                "isError": True,
            }
        response = {
            "jsonrpc": "2.0",
            "id": result["id"],
            "result": tool_result,
        }
    else:
        response = result

    # Queue response for SSE delivery
    if session_id in sessions:
        await sessions[session_id]["queue"].put(response)

    return Response(status_code=202)


# --- Streamable HTTP Transport (newer MCP spec) ---

@app.post("/mcp")
async def mcp_streamable(
    request: Request,
    authorization: str | None = Header(None),
):
    """Streamable HTTP endpoint - handles single request/response MCP calls."""
    get_api_key(authorization)

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
            tool_result = await handle_tool_call(tool_name, tool_args)
        except Exception as e:
            tool_result = {
                "content": [{"type": "text", "text": f"Error: {str(e)}"}],
                "isError": True,
            }
        return {
            "jsonrpc": "2.0",
            "id": result["id"],
            "result": tool_result,
        }

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
