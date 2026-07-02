# RAG MCP Server for BrownserverN5

A self-hosted document RAG (Retrieval Augmented Generation) system with an MCP (Model Context Protocol) server, designed for deployment on Unraid.

Cloud-based LLMs (Claude, GPT, etc.) connect via the MCP server to search your local documents using semantic similarity - your documents never leave your server.

## Architecture

```
                          ┌─────────────────┐
                          │   Web UI (:8902) │
                          │   React + Nginx  │
                          └───────┬──────────┘
                                  │
                  ┌───────────────┼───────────────┐
                  │                               │
         ┌────────┴────────┐            ┌─────────┴────────┐
         │ Backend (:8900) │            │ MCP Server(:8901)│
         │ FastAPI + RAG   │◄───────────│ SSE + HTTP       │
         │ ChromaDB        │            │ API Key Auth     │
         └────────┬────────┘            └──────────────────┘
                  │
         ┌────────┴────────┐
         │ SMB Shares (LAN)│
         │ 192.168.1.x     │
         └─────────────────┘
```

**Three Docker services:**

| Service | Internal Port | External Port | Purpose |
|---------|--------------|---------------|---------|
| Backend | 8000 | **8900** | FastAPI + ChromaDB RAG engine |
| MCP Server | 8001 | **8901** | MCP protocol for cloud LLMs |
| Frontend | 80 | **8902** | React management UI |

## Quick Start

### 1. Deploy on Unraid

SSH into your server or use the Unraid terminal:

```bash
cd /mnt/user/appdata  # or wherever you keep app data
git clone <this-repo> rag-mcp-server
cd rag-mcp-server

# Copy and edit environment config
cp .env.example .env

# Deploy
chmod +x deploy.sh
./deploy.sh
```

### 2. Open the Web UI

Navigate to `http://192.168.1.52:8902` in your browser.

### 3. Create an API Key

Go to **API Keys** in the sidebar and create a key. Copy it immediately - it's shown only once.

### 4. Upload Documents

Use the **Documents** page to upload files, or use the **SMB Browser** to ingest documents from LAN shares.

### 5. Connect Your LLM

#### Claude Desktop / Claude Code
Add to your MCP config:
```json
{
  "mcpServers": {
    "rag-documents": {
      "url": "http://192.168.1.52:8901/sse",
      "headers": {
        "Authorization": "Bearer YOUR_API_KEY"
      }
    }
  }
}
```

#### Streamable HTTP (alternative)
For clients that support it, use `http://192.168.1.52:8901/mcp` as the endpoint.

## Supported File Types

| Category | Extensions |
|----------|-----------|
| Text | `.txt`, `.md`, `.csv`, `.log`, `.ini`, `.conf`, `.cfg` |
| Code | `.py`, `.js`, `.ts`, `.go`, `.java`, `.c`, `.cpp`, `.rs`, `.zig`, `.sh`, `.sql` |
| Documents | `.pdf`, `.docx`, `.xlsx` |
| Data | `.json`, `.yaml`, `.yml`, `.xml`, `.html`, `.css`, `.toml` |

## MCP Tools Available

| Tool | Description |
|------|-------------|
| `search_documents` | Semantic search across indexed documents |
| `list_collections` | List all document collections |
| `list_documents` | List documents in a collection |
| `get_server_status` | Server status and stats |

## API Endpoints

### Backend (port 8900)
- `POST /api/documents/upload` - Upload and index a document
- `POST /api/documents/query` - Semantic search
- `GET /api/documents/list?collection=default` - List documents
- `DELETE /api/documents/{filename}` - Remove a document
- `POST /api/documents/reindex` - Re-index a collection
- `POST /api/smb/browse` - Browse SMB share
- `POST /api/smb/ingest` - Ingest from SMB share
- `GET /api/admin/status` - Server status

### MCP Server (port 8901)
- `GET /sse` - SSE transport endpoint
- `POST /messages?session_id=X` - SSE message endpoint
- `POST /mcp` - Streamable HTTP endpoint
- `GET /mcp/info` - Server capabilities (public)

## Data Storage

All persistent data is stored in `./data/`:
- `documents/` - Uploaded document files
- `chromadb/` - Vector database
- `config/` - Server configuration and API key hashes

## Management

```bash
# Start
docker compose up -d

# Stop
./stop.sh

# View logs
docker compose logs -f

# Rebuild after changes
docker compose build && docker compose up -d
```

## GPU Acceleration (optional)

The backend runs the embedding model on CPU by default. To use an NVIDIA GPU
(e.g. an RTX 5070 over oculink) for faster embedding:

**Host prerequisites (Unraid):**
- Install the **Nvidia Driver** plugin
- Ensure `nvidia-smi` lists the GPU
- Docker has the NVIDIA runtime (the plugin provides it)

**Deploy with GPU:**
```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build
```

This builds the backend with CUDA-enabled torch, reserves the GPU for the
backend container, and sets `EMBEDDING_DEVICE=cuda`. The embedding model
falls back to CPU automatically if no CUDA device is found, so it is safe.

To go back to CPU, deploy with the plain `docker compose up -d --build`.

## Local Client (`clients/rag_client.py`)

A command-line client for any machine on the LAN — search, ingest folders of
manuals, and fetch documents without the web UI. It can also embed query text
locally on a connected GPU / neural chip (`--local-embed`) to offload that
work from the server.

```bash
pip install httpx                       # minimal
pip install sentence-transformers torch # only for --local-embed

export RAG_URL=https://192.168.1.52:8943
export RAG_KEY=rmcp_your_key_here

python clients/rag_client.py --insecure status
python clients/rag_client.py --insecure ingest-dir ./manuals --collection manuals
python clients/rag_client.py --insecure search "how to reset" --collection manuals
python clients/rag_client.py --insecure search "wifi setup" --local-embed
```

### Ingesting device manuals

There is no automatic network scan or manual download (that would require
crawling the internet and your LAN). The intended workflow:

1. Collect the PDF manuals for your devices into a folder (or an SMB share).
2. Ingest them with the local client (`ingest-dir`) or the **SMB Browser**
   page in the web UI ("Ingest This Folder").
3. Optionally save the SMB share and enable auto-sync so new manuals are
   picked up automatically.

## Testing / QA

A self-contained QA suite exercises the full API (auth, bootstrap, CRUD,
PDF parsing, input-validation edge cases) using in-memory stubs for the heavy
ML dependencies — no GPU or vector DB required:

```bash
cd backend
python tests/test_api.py        # exits non-zero on any failure
```

```bash
python tests/test_eval.py       # retrieval golden-set (top-1 ranking)
```

## Retrieval pipeline

Ingestion: files are parsed (PDFs via **Docling** — layout/table-aware Markdown
with `<!-- page N -->` markers, falling back to pypdf), chunked
(Markdown-structure-aware when possible), optionally enriched with
contextual-retrieval sentences, embedded, and stored in ChromaDB. Content-hash
dedup skips unchanged files on re-ingest.

Query: HyDE query expansion (opt-in) → dense vector search + BM25 → Reciprocal
Rank Fusion → cross-encoder rerank → prompt-injection sanitize → parent-child
context expansion (prev/next chunks) for the LLM.

### Ask (streaming answers)

`POST /api/documents/ask/stream` returns Server-Sent Events:

- `event: sources` — array of `{source, score, excerpt, page?, section?}`
- `event: delta` — `{"text": "..."}` incremental answer tokens
- `event: done` — `{"model": "..."}` (empty model ⇒ no LLM available)

The web UI's **Ask** tab streams the answer live with clickable `[n]` citations
that scroll to the matching source; clicking a source opens a full-document
preview. `POST /api/documents/ask` remains available as the non-streaming variant.

## Feature flags (env vars)

| Var | Default | Effect |
|-----|---------|--------|
| `DOCLING_ENABLED` | `1` | Layout-aware PDF parsing (falls back to pypdf) |
| `ENABLE_HYDE` | `0` (1 on GPU) | Hypothetical-document query expansion |
| `EXPAND_CONTEXT` | `1` | Parent-child context expansion for the LLM |
| `ENABLE_CONTEXTUAL_INGEST` | `0` | LLM-enriched chunks at ingest (slow, needs Ollama) |
| `RERANKER_MODEL` | `cross-encoder/ms-marco-MiniLM-L-6-v2` | Reranker model |
| `LLM_MODEL` | `qwen2.5:14b` | Ollama model for Ask |

## GPU + Ollama

`docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build`

The `ollama` service auto-pulls `LLM_MODEL` on first start (idempotent) and is
gated by a healthcheck so the backend waits for it. HyDE is enabled on GPU
deploys. Requires the Unraid **Nvidia Driver** plugin; on Unraid 7.2, driver
`580.105.05` has a known `--runtime=nvidia` bug — use `580.95.05`.

## Unraid host integration (optional)

- **System notifications**: uncomment the `unraid-notify` volume mount in
  `docker-compose.yml` (backend service) to get "sync complete/failed"
  notifications through Unraid's notification center. No-op if not mounted.
- **Storage**: for best ChromaDB I/O, place `data/` under `/mnt/cache/appdata/`
  (bypasses the FUSE shfs layer). Trade-off: not parity-protected.
- **Backup**: use the **Appdata Backup** plugin (stops containers for a
  consistent copy). Exclude `data/ollama/` from daily backups — model weights
  are large and re-pullable.

## Healthchecks

All services define Docker healthchecks; `docker ps` shows health status.
`mcp-server` and `frontend` wait for the backend to be healthy before starting.
MCP discovery is exposed at `/.well-known/mcp`.
