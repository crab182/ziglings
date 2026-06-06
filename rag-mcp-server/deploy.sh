#!/bin/bash
set -e

echo "========================================="
echo "  RAG MCP Server - Deployment Script"
echo "  Target: BrownserverN5 (192.168.1.52)"
echo "========================================="
echo ""

# -----------------------------------------------
# 1. Stop and remove everything from previous runs
# -----------------------------------------------
echo "[*] Cleaning up previous deployment..."
docker compose down --remove-orphans 2>/dev/null || true
docker compose down --rmi local 2>/dev/null || true
docker image prune -f 2>/dev/null || true
echo ""

# -----------------------------------------------
# 2. Prepare data directories
# -----------------------------------------------
echo "[*] Preparing data directories..."
mkdir -p data/documents data/chromadb data/config data/certs
chmod -R 777 data/documents data/chromadb data/config 2>/dev/null || true
echo "    Data directories ready"

# -----------------------------------------------
# 3. Wipe stale config if empty/corrupt
# -----------------------------------------------
if [ -f data/config/server_config.json ]; then
    if ! python3 -c "import json; json.load(open('data/config/server_config.json'))" 2>/dev/null; then
        echo "[!] Corrupt config detected, removing..."
        rm -f data/config/server_config.json
    fi
fi

# -----------------------------------------------
# 4. Env file
# -----------------------------------------------
if [ ! -f .env ]; then
    cp .env.example .env 2>/dev/null || true
fi

# -----------------------------------------------
# 5. Generate self-signed TLS certificate
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
    echo "    Certificate generated"
    echo ""
fi

# -----------------------------------------------
# 6. Build (clean) and start
# -----------------------------------------------
echo "[1/3] Building Docker images..."
docker compose build --no-cache

echo ""
echo "[2/3] Starting services..."
docker compose up -d

echo ""
echo "[3/3] Waiting for services to start..."
sleep 8

# -----------------------------------------------
# 7. Health checks with retry
# -----------------------------------------------
echo ""
echo "Health checks:"
check_health() {
    curl -ksf https://localhost:8943/health 2>/dev/null && echo " - Backend: OK" && return 0
    echo " - Backend: not ready yet"
    return 1
}

if ! check_health; then
    echo "    Waiting 20s for backend to load embedding model..."
    sleep 20
    check_health || echo " - Backend: FAILED — run: docker logs rag-mcp-backend"
fi

curl -ksf https://localhost:8943/mcp/info >/dev/null 2>&1 && echo " - MCP Server: OK" || echo " - MCP Server: check: docker logs rag-mcp-server"
curl -ksf https://localhost:8943/ >/dev/null 2>&1 && echo " - Frontend: OK" || echo " - Frontend: check: docker logs rag-mcp-frontend"

# Quick write-test on config dir
docker exec rag-mcp-backend sh -c 'echo test > /app/data/config/.writetest && rm /app/data/config/.writetest' 2>/dev/null \
    && echo " - Config writable: OK" \
    || echo " - Config writable: FAILED — bootstrap will not work!"

echo ""
echo "========================================="
echo "  Deployment complete!"
echo ""
echo "  Web UI: https://192.168.1.52:8943"
echo "    (accept the self-signed cert warning)"
echo ""
echo "  MCP:    https://192.168.1.52:8943/sse"
echo "          https://192.168.1.52:8943/mcp"
echo "========================================="
echo ""
echo "First time? Open the Web UI — you'll create an admin key."
