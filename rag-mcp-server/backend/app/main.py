import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import admin, documents, smb

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")

app = FastAPI(
    title="RAG MCP Server - Backend API",
    description="Document RAG engine with MCP server for BrownserverN5",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(documents.router)
app.include_router(smb.router)
app.include_router(admin.router)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "rag-backend"}
