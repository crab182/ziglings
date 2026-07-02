#!/bin/bash
set -euo pipefail

# Opt-in only. This hook performs network dependency installs, so it does NOT
# run automatically just because the branch is opened in Claude Code on the web.
# To enable it, set RAG_HOOK_OPTIN=1 in your Claude Web environment. Without that
# explicit opt-in there is no auto-execution / supply-chain boundary here.
if [ "${RAG_HOOK_OPTIN:-}" != "1" ]; then
  echo "[session-start] Skipped (set RAG_HOOK_OPTIN=1 to enable dependency install)."
  exit 0
fi

# Only run in Claude Code on the web (remote environments)
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

echo "[session-start] Installing RAG MCP Server test dependencies..."

# Lightweight deps needed by the QA suite (backend/tests/test_api.py).
# The suite stubs the heavy ML packages (chromadb, sentence-transformers,
# torch, smbclient, cryptography) so we intentionally do NOT install them —
# that keeps session startup fast (~30s instead of ~10min for the CUDA stack).
# All versions are pinned exactly (no floating ranges) for reproducibility.
pip install --quiet \
  fastapi==0.115.6 \
  "uvicorn[standard]==0.34.0" \
  pydantic==2.10.4 \
  pydantic-settings==2.7.1 \
  python-multipart==0.0.18 \
  slowapi==0.1.9 \
  httpx==0.28.1 \
  chardet==5.2.0 \
  pypdf==5.1.0 \
  APScheduler==3.10.4 \
  rank-bm25==0.2.2

# Frontend deps (for vite build / bracket checks).
# - npm ci installs the EXACT versions pinned in the committed
#   package-lock.json (no dependency drift), falling back to npm install
#   only if the lockfile is somehow absent.
# - --ignore-scripts blocks dependency lifecycle scripts from executing
#   during install (supply-chain hardening; vite/react need no install scripts).
if [ -d "$CLAUDE_PROJECT_DIR/rag-mcp-server/frontend" ]; then
  (cd "$CLAUDE_PROJECT_DIR/rag-mcp-server/frontend" \
    && { npm ci --ignore-scripts --no-audit --no-fund --silent \
         || npm install --ignore-scripts --no-audit --no-fund --silent; }) || \
    echo "[session-start] npm install failed (non-fatal)"
fi

echo "[session-start] Done. Run tests: cd rag-mcp-server/backend && python tests/test_api.py"
