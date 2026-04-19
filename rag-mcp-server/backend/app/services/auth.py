import hashlib
import hmac
import secrets
from datetime import datetime, timezone

from app.config import load_config, save_config


def generate_api_key() -> str:
    return f"rmcp_{secrets.token_urlsafe(32)}"


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def create_api_key(name: str, description: str = "", is_admin: bool = False) -> dict:
    config = load_config()
    raw_key = generate_api_key()
    key_entry = {
        "name": name,
        "key_hash": hash_key(raw_key),
        "key_prefix": raw_key[:12] + "...",
        "description": description,
        "is_admin": is_admin,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "active": True,
    }
    config.setdefault("api_keys", []).append(key_entry)
    save_config(config)
    return {"raw_key": raw_key, **key_entry}


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
    config = load_config()
    return bool(config.get("api_keys"))


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
    ]


def revoke_api_key(name: str) -> bool:
    config = load_config()
    for entry in config.get("api_keys", []):
        if entry["name"] == name:
            entry["active"] = False
            save_config(config)
            return True
    return False


def delete_api_key(name: str) -> bool:
    config = load_config()
    keys = config.get("api_keys", [])
    original_len = len(keys)
    config["api_keys"] = [e for e in keys if e["name"] != name]
    if len(config["api_keys"]) < original_len:
        save_config(config)
        return True
    return False
