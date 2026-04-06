#!/bin/bash
set -e

echo "========================================="
echo "  RAG MCP Server - Deployment Script"
echo "  Target: BrownserverN5 (192.168.1.52)"
echo "========================================="
echo ""

# Create data directories
mkdir -p data/documents data/chromadb data/config

# Copy env file if it doesn't exist
if [ ! -f .env ]; then
    cp .env.example .env
    echo "[!] Created .env from template. Edit it to set your API key."
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
curl -sf http://localhost:8900/health && echo " - Backend: OK" || echo " - Backend: STARTING (may take a moment for model download)"
curl -sf http://localhost:8901/health && echo " - MCP Server: OK" || echo " - MCP Server: STARTING"
curl -sf http://localhost:8902/ > /dev/null 2>&1 && echo " - Frontend: OK" || echo " - Frontend: STARTING"

echo ""
echo "========================================="
echo "  Deployment complete!"
echo ""
echo "  Web UI:      http://192.168.1.52:8902"
echo "  Backend API: http://192.168.1.52:8900"
echo "  MCP Server:  http://192.168.1.52:8901"
echo "  MCP SSE:     http://192.168.1.52:8901/sse"
echo "  MCP HTTP:    http://192.168.1.52:8901/mcp"
echo "========================================="
echo ""
echo "Next steps:"
echo "  1. Open the Web UI to create an API key"
echo "  2. Upload or ingest documents via SMB"
echo "  3. Configure your LLM to connect to the MCP server"
