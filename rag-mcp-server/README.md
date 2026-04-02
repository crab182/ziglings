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
