import logging
import os
import traceback
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.middleware.base import BaseHTTPMiddleware

from app.routers import admin, documents, smb
from app.services.security import require_admin_key

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# Show full errors in responses for debugging (disable in production)
DEBUG_ERRORS = os.environ.get("DEBUG_ERRORS", "1") == "1"


def _parse_origins(raw: str) -> list[str]:
    origins = [o.strip() for o in raw.split(",") if o.strip()]
    return origins or ["http://localhost", "http://127.0.0.1"]


CORS_ORIGINS = _parse_origins(os.environ.get(
    "CORS_ALLOWED_ORIGINS",
    "http://192.168.1.52:8902,http://localhost:8902,https://192.168.1.52:8943,https://localhost:8943",
))

limiter = Limiter(key_func=get_remote_address, default_limits=["120/minute"])


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.config import settings
    config_dir = Path(settings.config_dir)
    config_dir.mkdir(parents=True, exist_ok=True)
    try:
        test_file = config_dir / ".startup_test"
        test_file.write_text("ok")
        test_file.unlink()
        logger.info("Config directory writable: %s", config_dir)
    except Exception as e:
        logger.error("CONFIG DIR NOT WRITABLE: %s — %s", config_dir, e)

    try:
        from app.services.scheduler import start_scheduler
        start_scheduler()
        logger.info("Scheduler started")
    except Exception as e:
        logger.warning("Scheduler unavailable (non-fatal): %s", e)

    yield

    try:
        from app.services.scheduler import shutdown
        shutdown()
    except Exception:
        pass


app = FastAPI(
    title="RAG MCP Server - Backend API",
    description="Document RAG engine with MCP server",
    version="1.0.0",
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
    max_age=600,
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response


app.add_middleware(SecurityHeadersMiddleware)


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception):
    tb = traceback.format_exc()
    logger.error("Unhandled error on %s %s:\n%s", request.method, request.url.path, tb)
    detail = f"{type(exc).__name__}: {exc}" if DEBUG_ERRORS else "Internal server error"
    return JSONResponse(status_code=500, content={"detail": detail})


app.include_router(documents.router)
app.include_router(smb.router)
app.include_router(admin.router)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "rag-backend"}


@app.get("/api/debug")
async def debug_status(_: dict = Depends(require_admin_key)):
    """Diagnostic endpoint — reports the state of every subsystem. Admin only."""
    report = {
        "config": {"status": "unknown"},
        "chromadb": {"status": "unknown"},
        "embedding_model": {"status": "unknown"},
        "scheduler": {"status": "unknown"},
        "crypto": {"status": "unknown"},
        "smb_shares": {"status": "unknown"},
    }

    # 1. Config
    try:
        from app.config import load_config, settings, CONFIG_FILE
        config = load_config()
        report["config"] = {
            "status": "ok",
            "path": str(CONFIG_FILE),
            "exists": CONFIG_FILE.exists(),
            "keys_count": len(config.get("api_keys", [])),
            "smb_shares_count": len(config.get("smb_shares", [])),
        }
    except Exception as e:
        report["config"] = {"status": "error", "error": f"{type(e).__name__}: {e}"}

    # 2. ChromaDB
    try:
        from app.services.rag_engine import get_chroma_client
        client = get_chroma_client()
        collections = client.list_collections()
        report["chromadb"] = {
            "status": "ok",
            "collections_count": len(collections),
        }
    except Exception as e:
        report["chromadb"] = {"status": "error", "error": f"{type(e).__name__}: {e}"}

    # 3. Embedding model
    try:
        from app.services.rag_engine import get_embedding_model
        model = get_embedding_model()
        dim = model.get_sentence_embedding_dimension()
        report["embedding_model"] = {
            "status": "ok",
            "dimension": dim,
        }
    except Exception as e:
        report["embedding_model"] = {"status": "error", "error": f"{type(e).__name__}: {e}"}

    # 4. Scheduler
    try:
        from app.services.scheduler import _scheduler
        report["scheduler"] = {
            "status": "ok" if _scheduler else "not started",
            "running": _scheduler is not None,
        }
    except Exception as e:
        report["scheduler"] = {"status": "error", "error": f"{type(e).__name__}: {e}"}

    # 5. Crypto
    try:
        from app.services.crypto import get_fernet
        f = get_fernet()
        report["crypto"] = {"status": "ok", "fernet_available": f is not None}
    except Exception as e:
        report["crypto"] = {"status": "error", "error": f"{type(e).__name__}: {e}"}

    # 6. SMB shares module
    try:
        from app.services.smb_shares import list_saved
        saved = list_saved()
        report["smb_shares"] = {"status": "ok", "saved_count": len(saved)}
    except Exception as e:
        report["smb_shares"] = {"status": "error", "error": f"{type(e).__name__}: {e}"}

    return report
