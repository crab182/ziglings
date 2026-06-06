import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.middleware.base import BaseHTTPMiddleware

from app.routers import admin, documents, smb

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


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
    # Verify config dir is writable
    from app.config import settings
    config_dir = Path(settings.config_dir)
    config_dir.mkdir(parents=True, exist_ok=True)
    try:
        test_file = config_dir / ".startup_test"
        test_file.write_text("ok")
        test_file.unlink()
        logger.info("Config directory writable: %s", config_dir)
    except Exception:
        logger.error("CONFIG DIR NOT WRITABLE: %s", config_dir)

    # Start scheduler — completely non-fatal
    try:
        from app.services.scheduler import start_scheduler
        start_scheduler()
    except Exception:
        logger.warning("Scheduler unavailable (non-fatal): %s", __import__('traceback').format_exc())

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
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


app.include_router(documents.router)
app.include_router(smb.router)
app.include_router(admin.router)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "rag-backend"}
