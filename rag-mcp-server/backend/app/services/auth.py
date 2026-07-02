import hashlib
import hmac
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path

from app.config import atomic_update, load_config, settings

# Internal key the MCP server uses to authenticate to the backend. It is
# excluded from has_any_keys()/list_api_keys() so the bootstrap UX and the
# user-facing key list are unaffected.
SERVICE_KEY_NAME = "__mcp_service__"
SERVICE_KEY_FILE = Path(settings.config_dir) / "mcp_service.key"


def generate_api_key() -> str:
    return f"rmcp_{secrets.token_urlsafe(32)}"


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def _make_entry(name: str, raw_key: str, description: str, is_admin: bool) -> dict:
    return {
        "name": name,
        "key_hash": hash_key(raw_key),
        "key_prefix": raw_key[:12] + "...",
        "description": description,
        "is_admin": is_admin,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "active": True,
    }


def create_api_key(name: str, description: str = "", is_admin: bool = False) -> dict:
    raw_key = generate_api_key()
    entry = _make_entry(name, raw_key, description, is_admin)
    atomic_update(lambda cfg: cfg.setdefault("api_keys", []).append(entry))
    return {"raw_key": raw_key, **entry}


def validate_api_key(key: str) -> dict | None:
    """Return the key entry dict on success, None on failure. Constant-time."""
    if not key:
        return None
    config = load_config()
    hashed = hash_key(key)
    match: dict | None = None
    for entry in config.get("api_keys", []):
        if not entry.get("active", True):
            continue
        if hmac.compare_digest(entry["key_hash"], hashed):
            match = entry
    return match


def has_any_keys() -> bool:
    """True if any USER key exists. The internal service key doesn't count, so
    the bootstrap screen still appears on a fresh deploy."""
    config = load_config()
    return any(e.get("name") != SERVICE_KEY_NAME for e in config.get("api_keys", []))


def list_api_keys() -> list[dict]:
    config = load_config()
    return [
        {
            "name": e["name"],
            "key_prefix": e["key_prefix"],
            "description": e.get("description", ""),
            "is_admin": e.get("is_admin", False),
            "created_at": e["created_at"],
            "active": e.get("active", True),
        }
        for e in config.get("api_keys", [])
        if e.get("name") != SERVICE_KEY_NAME
    ]


def revoke_api_key(name: str) -> bool:
    if name == SERVICE_KEY_NAME:
        return False  # protect the internal service key

    def _update(config):
        for entry in config.get("api_keys", []):
            if entry["name"] == name:
                entry["active"] = False
                return True
        return False
    return atomic_update(_update)


def delete_api_key(name: str) -> bool:
    if name == SERVICE_KEY_NAME:
        return False  # protect the internal service key

    def _update(config):
        keys = config.get("api_keys", [])
        original = len(keys)
        config["api_keys"] = [e for e in keys if e["name"] != name]
        return len(config["api_keys"]) < original
    return atomic_update(_update)


def ensure_service_key() -> str:
    """Ensure the internal MCP service key exists and its raw value is written to
    SERVICE_KEY_FILE (0600) for the MCP server to read. Idempotent: regenerates
    only if the entry or file is missing/inconsistent."""
    config = load_config()
    entry = next(
        (e for e in config.get("api_keys", [])
         if e.get("name") == SERVICE_KEY_NAME and e.get("active", True)),
        None,
    )
    if entry and SERVICE_KEY_FILE.exists():
        try:
            raw = SERVICE_KEY_FILE.read_text().strip()
            if raw and hmac.compare_digest(entry["key_hash"], hash_key(raw)):
                return raw  # already consistent
        except Exception:
            pass

    raw = generate_api_key()
    new_entry = _make_entry(SERVICE_KEY_NAME, raw, "Internal MCP service account (auto-managed)", True)

    def _update(cfg):
        cfg["api_keys"] = [e for e in cfg.get("api_keys", []) if e.get("name") != SERVICE_KEY_NAME]
        cfg["api_keys"].append(new_entry)
    atomic_update(_update)

    fd = os.open(str(SERVICE_KEY_FILE), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, raw.encode())
    finally:
        os.close(fd)
    return raw
