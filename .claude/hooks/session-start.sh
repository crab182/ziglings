#!/bin/bash
set -euo pipefail

# Only run in Claude Code on the web (remote environments)
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

echo "[session-start] Installing RAG MCP Server test dependencies..."

# Lightweight deps needed by the QA suite (backend/tests/test_api.py).
# The suite stubs the heavy ML packages (chromadb, sentence-transformers,
# torch, smbclient, cryptography) so we intentionally do NOT install them —
# that keeps session startup fast (~30s instead of ~10min for the CUDA stack).
pip install --quiet \
  fastapi==0.115.6 \
  "uvicorn[standard]==0.34.0" \
  pydantic==2.10.4 \
  pydantic-settings==2.7.1 \
  python-multipart==0.0.18 \
  slowapi==0.1.9 \
  httpx \
  chardet==5.2.0 \
  "pypdf>=4.0.0" \
  APScheduler==3.10.4 \
  rank-bm25==0.2.2

# Frontend deps (for vite build / bracket checks).
# --ignore-scripts blocks dependency lifecycle scripts from executing during
# install (supply-chain hardening; vite/react don't need install scripts).
if [ -d "$CLAUDE_PROJECT_DIR/rag-mcp-server/frontend" ]; then
  (cd "$CLAUDE_PROJECT_DIR/rag-mcp-server/frontend" && npm install --ignore-scripts --no-audit --no-fund --silent) || \
    echo "[session-start] npm install failed (non-fatal)"
fi

echo "[session-start] Done. Run tests: cd rag-mcp-server/backend && python tests/test_api.py"
