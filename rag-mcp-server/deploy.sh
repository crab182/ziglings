#!/bin/bash
set -e

echo "========================================="
echo "  RAG MCP Server - Deployment Script"
echo "  Target: BrownserverN5 (192.168.1.52)"
echo "========================================="
echo ""

# -----------------------------------------------
# 1. Stop any existing deployment
# -----------------------------------------------
if docker compose ps -q 2>/dev/null | grep -q .; then
    echo "[*] Stopping existing containers..."
    docker compose down --remove-orphans
    echo ""
fi

# -----------------------------------------------
# 2. Clean up old images and build cache
# -----------------------------------------------
echo "[*] Cleaning up old images and cache..."
docker compose down --rmi local 2>/dev/null || true
docker image prune -f 2>/dev/null || true
echo ""

# -----------------------------------------------
# 3. Create data directories with correct ownership
#    Backend runs as uid 10001, must be able to write
#    config, chromadb, and documents volumes.
# -----------------------------------------------
echo "[*] Preparing data directories..."
mkdir -p data/documents data/chromadb data/config data/certs
chown -R 10001:10001 data/documents data/chromadb data/config
echo "    Ownership set to uid 10001 (appuser)"

# -----------------------------------------------
# 4. Env file
# -----------------------------------------------
if [ ! -f .env ]; then
    cp .env.example .env 2>/dev/null || true
    echo "[!] Created .env from template."
    echo ""
fi

# -----------------------------------------------
# 5. Generate self-signed TLS certificate (idempotent)
# -----------------------------------------------
if [ ! -f data/certs/server.crt ]; then
    echo "[*] Generating self-signed TLS certificate..."
    openssl req -x509 -newkey rsa:2048 -nodes \
        -keyout data/certs/server.key \
        -out data/certs/server.crt \
        -days 825 \
        -subj "/CN=192.168.1.52" \
        -addext "subjectAltName=IP:192.168.1.52,DNS:localhost" \
        2>/dev/null
    chmod 600 data/certs/server.key
    echo "    Certificate generated for 192.168.1.52"
    echo ""
fi

# -----------------------------------------------
# 6. Build images
# -----------------------------------------------
echo "[1/3] Building Docker images..."
docker compose build --no-cache

# -----------------------------------------------
# 7. Start services
# -----------------------------------------------
echo ""
echo "[2/3] Starting services..."
docker compose up -d

# -----------------------------------------------
# 8. Wait and health-check
# -----------------------------------------------
echo ""
echo "[3/3] Waiting for services to start..."
sleep 8

echo ""
echo "Health checks:"

ok=true
curl -ksf https://localhost:8943/api/health 2>/dev/null && echo " - Backend: OK" || { echo " - Backend: STARTING (model download may take a moment)"; ok=false; }
curl -ksf https://localhost:8943/mcp/info  >/dev/null 2>&1 && echo " - MCP Server: OK" || { echo " - MCP Server: STARTING"; ok=false; }
curl -ksf https://localhost:8943/          >/dev/null 2>&1 && echo " - Frontend: OK" || { echo " - Frontend: STARTING"; ok=false; }

if [ "$ok" = false ]; then
    echo ""
    echo "[*] Some services still starting. Waiting 15 more seconds..."
    sleep 15
    echo "Retry:"
    curl -ksf https://localhost:8943/api/health 2>/dev/null && echo " - Backend: OK" || echo " - Backend: FAILED (check: docker logs rag-mcp-backend)"
    curl -ksf https://localhost:8943/mcp/info  >/dev/null 2>&1 && echo " - MCP Server: OK" || echo " - MCP Server: FAILED (check: docker logs rag-mcp-server)"
    curl -ksf https://localhost:8943/          >/dev/null 2>&1 && echo " - Frontend: OK" || echo " - Frontend: FAILED (check: docker logs rag-mcp-frontend)"
fi

echo ""
echo "========================================="
echo "  Deployment complete!"
echo ""
echo "  Web UI (HTTPS): https://192.168.1.52:8943"
echo "  Web UI (HTTP):  http://192.168.1.52:8902 (redirects to HTTPS)"
echo "  Backend API:    https://192.168.1.52:8943/api/"
echo "  MCP SSE:        https://192.168.1.52:8943/sse"
echo "  MCP HTTP:       https://192.168.1.52:8943/mcp"
echo ""
echo "  NOTE: Self-signed cert - browser will show a warning."
echo "  For real certs, use SWAG/NPM reverse proxy with a domain."
echo "========================================="
echo ""
echo "Next steps:"
echo "  1. Open the Web UI to create an admin API key"
echo "  2. Upload or ingest documents via SMB"
echo "  3. Configure your LLM to connect to the MCP server"
