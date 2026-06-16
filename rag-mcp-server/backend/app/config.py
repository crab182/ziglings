import fcntl
import json
import os
from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    chroma_persist_dir: str = "/app/data/chromadb"
    documents_dir: str = "/app/data/documents"
    config_dir: str = "/app/data/config"
    embedding_model: str = "all-MiniLM-L6-v2"
    server_hostname: str = "BrownserverN5"
    server_ip: str = "192.168.1.52"

    class Config:
        env_file = ".env"


settings = Settings()

CONFIG_FILE = Path(settings.config_dir) / "server_config.json"
_LOCK_FILE = Path(settings.config_dir) / ".config.lock"

_DEFAULT_CONFIG = {
    "api_keys": [],
    "smb_shares": [],
    "collections": ["default"],
    "mcp_enabled": True,
    "content_hashes": {},
}


def _ensure_config():
    Path(settings.config_dir).mkdir(parents=True, exist_ok=True)
    if not CONFIG_FILE.exists():
        CONFIG_FILE.write_text(json.dumps(_DEFAULT_CONFIG, indent=2))


def _acquire_lock():
    _LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd = open(_LOCK_FILE, "w")
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


def _release_lock(fd):
    fcntl.flock(fd, fcntl.LOCK_UN)
    fd.close()


def load_config() -> dict:
    _ensure_config()
    return json.loads(CONFIG_FILE.read_text())


def save_config(config: dict):
    _ensure_config()
    CONFIG_FILE.write_text(json.dumps(config, indent=2))


def atomic_update(fn):
    """Read config under exclusive lock, call fn(config) to mutate, save. Returns fn's result."""
    lock = _acquire_lock()
    try:
        config = load_config()
        result = fn(config)
        save_config(config)
        return result
    finally:
        _release_lock(lock)
