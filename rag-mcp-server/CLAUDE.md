# CLAUDE.md — RAG MCP Server

A self-hosted RAG (Retrieval-Augmented Generation) stack exposed over MCP.
Companion server to **Thread Hub** (`code-project-` repo), whose desktop app
connects here natively.

## Components & ports

| Component | Dir | Host port | What it is |
|---|---|---|---|
| Backend | `backend/` | **8900** (→8000) | FastAPI REST: ingestion, query, collections, admin |
| MCP server | `mcp_server/` | **8901** (→8001) | MCP over SSE (`/sse`+`/messages`) and Streamable HTTP (`/mcp`) |
| Frontend | `frontend/` | **8902** (→80) | React management UI |

RAG pipeline: SentenceTransformer embeddings + ChromaDB; documents are
chunked (512 words, 64 overlap) on upload. `docker-compose.gpu.yml` is the
GPU variant.

## MCP server facts

- Tools: `search_documents`, `list_collections`, `list_documents`,
  `get_server_status`, `get_document`, and `ingest_note` (**admin-tier key
  required** — enforced via `ADMIN_TOOLS`).
- Tool responses are **dual-format**: a markdown text item (LLM-readable)
  plus a JSON-encoded item (programmatic clients parse this first).
- **Credential separation**: the MCP server calls the backend with its own
  `MCP_BACKEND_KEY` (a dedicated admin key) — client tokens are validated
  for access but **never forwarded** to the backend.
- Client auth: Bearer key in `Authorization`; keys are SHA-256-hashed in
  `$CONFIG_DIR/server_config.json`; `mcp_enabled` there is a kill switch.
- Browser-origin requests are checked against `CORS_ALLOWED_ORIGINS`
  (`check_origin`, DNS-rebinding/CSRF defense); non-browser clients without
  an Origin header pass. `ALLOWED_HOSTS` gates Host headers.

## Backend REST facts

`backend/app/routers/documents.py`: `POST /upload`, `POST /query`,
`GET /list`, `DELETE /{filename}`, `POST /reindex`, `GET /collections`,
`POST /collections/{name}`, `DELETE /collections/{name}`, `GET /content`,
`POST /ingest-text`.

- **Collection create/delete are admin-key endpoints** (search works with
  any key) — clients should surface that distinction in error messages.
- Collection names: 1–64 chars, letters/digits/`_`/`-`
  (`services/security.py::validate_collection_name`). The `default`
  collection cannot be deleted.
- The Thread Hub desktop derives this REST base from the MCP URL by
  convention: `:8901/mcp` → `:8900`.

## Run / test

```bash
./deploy.sh                      # docker compose stack (8900/8901/8902)
./stop.sh

# Tests
python -m pytest backend/tests/ -v   # backend API tests (test_api.py)
```

No Docker daemon (Cloud/agent containers)? Run uvicorn directly per
component, mirroring the Thread Hub pattern: backend `app.main:app` on 8900
with a writable `CONFIG_DIR`, then `mcp_server` on 8901 with `BACKEND_URL`
pointing at it and `MCP_BACKEND_KEY` set to an admin key you created.

## Conventions

- Keep MCP tool responses dual-format (markdown + JSON items) — the Thread
  Hub desktop parses JSON first and falls back to markdown.
- New admin-capable tools must be added to `ADMIN_TOOLS`.
- Never forward client Authorization to the backend; use `MCP_BACKEND_KEY`.
