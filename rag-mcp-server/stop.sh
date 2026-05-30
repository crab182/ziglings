#!/bin/bash
echo "Stopping RAG MCP Server..."
docker compose down --remove-orphans
docker image prune -f 2>/dev/null
echo "All services stopped and cleaned up."
