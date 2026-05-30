"""Fernet encryption for secrets at rest (e.g. SMB passwords)."""

import os
from pathlib import Path

from cryptography.fernet import Fernet

from app.config import settings

_fernet: Fernet | None = None


def get_fernet() -> Fernet:
    global _fernet
    if _fernet is not None:
        return _fernet

    env_key = os.environ.get("SECRET_KEY")
    if env_key:
        _fernet = Fernet(env_key.encode())
        return _fernet

    key_path = Path(settings.config_dir) / "secret.key"
    if key_path.exists():
        _fernet = Fernet(key_path.read_bytes().strip())
        return _fernet

    key = Fernet.generate_key()
    key_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(key_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, key)
    finally:
        os.close(fd)
    _fernet = Fernet(key)
    return _fernet


def encrypt(plaintext: str) -> str:
    return get_fernet().encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    return get_fernet().decrypt(token.encode()).decode()
