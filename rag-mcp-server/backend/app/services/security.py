"""FastAPI security dependencies: API key auth, filename/path safety."""

import re
import unicodedata
from pathlib import Path

from fastapi import Header, HTTPException, status

from app.services import auth

COLLECTION_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
FILENAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,255}$")


def _extract_bearer(authorization: str | None) -> str:
    if not authorization:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing Authorization header")
    if authorization.startswith("Bearer "):
        return authorization[7:].strip()
    return authorization.strip()


def require_api_key(authorization: str | None = Header(None)) -> dict:
    """Any valid, active API key (admin or readonly)."""
    # Bootstrap: if no keys exist yet, allow unauthenticated access.
    # This lets the operator create the first admin key via the UI.
    if not auth.has_any_keys():
        return {"name": "__bootstrap__", "is_admin": True, "bootstrap": True}

    key = _extract_bearer(authorization)
    entry = auth.validate_api_key(key)
    if not entry:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid or inactive API key")
    return entry


def require_admin_key(authorization: str | None = Header(None)) -> dict:
    """Admin-tier API key. Bootstrap allowed when no keys exist."""
    entry = require_api_key(authorization)
    if not entry.get("is_admin", False):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin privileges required")
    return entry


def validate_collection_name(name: str) -> str:
    if not COLLECTION_NAME_RE.match(name):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Collection name must be 1-64 chars: letters, digits, '_' or '-'",
        )
    return name


def safe_filename(filename: str) -> str:
    """Strip path components and normalize to a safe basename."""
    if not filename:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Missing filename")
    # Strip any path separators - only keep the basename
    base = Path(filename).name
    # Normalize unicode to prevent homoglyph/overlong encodings
    base = unicodedata.normalize("NFKC", base)
    # Reject hidden files and names that resolve oddly
    if base in {"", ".", ".."} or base.startswith("."):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid filename")
    if not FILENAME_RE.match(base):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Filename must contain only letters, digits, '.', '_', '-'",
        )
    return base


def safe_join(base_dir: Path, *parts: str) -> Path:
    """Join paths and assert the result stays within base_dir."""
    base_resolved = base_dir.resolve()
    target = base_resolved.joinpath(*parts).resolve()
    if not target.is_relative_to(base_resolved):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Path traversal detected")
    return target
