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


def _ensure_config():
    Path(settings.config_dir).mkdir(parents=True, exist_ok=True)
    if not CONFIG_FILE.exists():
        default = {
            "api_keys": [],
            "smb_shares": [],
            "collections": ["default"],
            "mcp_enabled": True,
        }
        CONFIG_FILE.write_text(json.dumps(default, indent=2))


def load_config() -> dict:
    _ensure_config()
    return json.loads(CONFIG_FILE.read_text())


def save_config(config: dict):
    _ensure_config()
    CONFIG_FILE.write_text(json.dumps(config, indent=2))
