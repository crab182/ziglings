# AGENTS.md

## Cursor Cloud specific instructions

This repo is a monorepo with two unrelated products:

- **`rag-mcp-server/`** — the actively developed product (all recent commits/PRs target it). A
  self-hosted document RAG stack: FastAPI backend + MCP server (Python) + React/Vite frontend.
- **Ziglings** (repo root: `build.zig`, `exercises/`) — a Zig learning exercise set. It needs a
  `0.8.0-dev`-era Zig *master* compiler that is not installed and is impractical to obtain today;
  it is not the active focus. Standard usage is `zig build` (see the root `README.md`).

The update script provisions a Python virtualenv at `rag-mcp-server/.venv` and installs the
frontend's npm deps. Docker is **not** available in the Cloud VM, so run the services directly with
uvicorn/vite (the `deploy.sh`/`docker compose` paths in `rag-mcp-server/README.md` won't work here).

### Running the RAG MCP server (three services, from `rag-mcp-server/`)

The backend's default data dirs are Docker paths (`/app/data/...`), so override them to the repo.
Run each in its own shell/tmux session:

- **Backend (:8900)** — `cd backend && CONFIG_DIR=$PWD/../data/config DOCUMENTS_DIR=$PWD/../data/documents CHROMA_PERSIST_DIR=$PWD/../data/chromadb DOCLING_ENABLED=0 CORS_ALLOWED_ORIGINS="http://localhost:5173" ../.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8900`
- **MCP server (:8901)** — `cd mcp_server && BACKEND_URL=http://localhost:8900 CONFIG_DIR=$PWD/../data/config ../.venv/bin/uvicorn server:app --host 0.0.0.0 --port 8901`
- **Frontend (:5173, Vite dev)** — `cd frontend && npm run dev -- --host 0.0.0.0` (proxies `/api` → :8900).

Non-obvious caveats:

- **torch/torchvision must be a matched CPU pair** from the PyTorch CPU index
  (`https://download.pytorch.org/whl/cpu`). A mismatch (e.g. torchvision from PyPI) throws
  `operator torchvision::nms does not exist` at import. The update script installs them together.
- **First ingest/query downloads models from HuggingFace** (`all-MiniLM-L6-v2` embedder + the
  `cross-encoder/ms-marco-MiniLM-L-6-v2` reranker, ~80MB each) — needs network on first call.
- **Set `DOCLING_ENABLED=0` in dev** to avoid downloading Docling's large layout/table models on
  the first PDF upload; the backend falls back to `pypdf`.
- **First admin key (bootstrap):** on a fresh config the backend allows unauthenticated bootstrap.
  Create the first key via the UI's bootstrap screen, or
  `curl -X POST localhost:8900/api/admin/api-keys -H 'Content-Type: application/json' -d '{"name":"admin","is_admin":true}'`.

### Tests

- Backend: `cd rag-mcp-server/backend && ../.venv/bin/python tests/test_api.py` and `tests/test_eval.py`
  (both exit non-zero on failure; heavy ML deps are stubbed).
- MCP server: from `rag-mcp-server/`, `.venv/bin/python -m unittest mcp_server.tests.test_tools`
  (the project has **no** `pytest` dependency — use `unittest`, not `pytest`).
- Frontend has no test/lint runner configured.

### Known pre-existing issue (NOT an environment problem)

With the pinned `chromadb==0.5.23`, `client.list_collections()` returns `Collection` objects, but
`backend/app/services/rag_engine.py::list_collections()` treats each item as a name string and
returns it in the API payload. As a result `/api/documents/collections` and `/api/admin/status`
return HTTP 500. This breaks the **Dashboard**, the **Documents** listing, the **UI login gate**
(it validates via `/api/admin/status`), and the MCP `list_collections` / `get_collection_stats` /
`get_server_status` tools. It reproduces identically in the project's own Docker build (same pin),
so it is a product bug, not a setup gap.

Core ingest + semantic search are unaffected and work end-to-end: `POST /api/documents/ingest-text`,
`POST /api/documents/upload`, `POST /api/documents/query`, `GET /api/documents/list`, and the MCP
`search_documents` / `get_document` / `ingest_note` tools. To reach the management UI, use the
**bootstrap screen** (it enters the app without calling `/api/admin/status`), then use the **Search**
page (it tolerates the collections error).
