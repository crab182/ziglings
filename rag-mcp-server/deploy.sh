#!/bin/bash
set -e

echo "========================================="
echo "  RAG MCP Server - Deployment Script"
echo "  Target: BrownserverN5 (192.168.1.52)"
echo "========================================="
echo ""

# Create data directories
mkdir -p data/documents data/chromadb data/config data/certs

# Copy env file if it doesn't exist
if [ ! -f .env ]; then
    cp .env.example .env
    echo "[!] Created .env from template. Edit it to set your API key."
    echo ""
fi

# Generate self-signed TLS certificate (idempotent)
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

echo "[1/3] Building Docker images..."
docker compose build

echo ""
echo "[2/3] Starting services..."
docker compose up -d

echo ""
echo "[3/3] Waiting for services to start..."
sleep 5

# Health checks
echo ""
echo "Health checks:"
curl -ksf https://localhost:8943/api/health && echo " - Backend: OK" || echo " - Backend: STARTING (may take a moment for model download)"
curl -ksf https://localhost:8943/mcp/info > /dev/null 2>&1 && echo " - MCP Server: OK" || echo " - MCP Server: STARTING"
curl -ksf https://localhost:8943/ > /dev/null 2>&1 && echo " - Frontend: OK" || echo " - Frontend: STARTING"

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
