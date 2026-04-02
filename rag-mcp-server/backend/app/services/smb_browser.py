import logging
from datetime import datetime, timezone
from io import BytesIO

from smbprotocol.connection import Connection
from smbprotocol.session import Session
from smbprotocol.tree import TreeConnect
from smbprotocol.open import (
    Open,
    CreateDisposition,
    CreateOptions,
    FileAttributes,
    FilePipePrinterAccessMask,
    ImpersonationLevel,
    ShareAccess,
)
from smbprotocol.file_info import (
    FileInformationClass,
)
import smbprotocol

logger = logging.getLogger(__name__)


def _normalize_path(path: str) -> str:
    path = path.replace("/", "\\").strip("\\")
    return path


def browse_share(
    server: str,
    share: str,
    path: str = "/",
    username: str = "guest",
    password: str = "",
    domain: str = "WORKGROUP",
) -> list[dict]:
    """Browse files and directories in an SMB share."""
    import smbclient

    smbclient.register_session(server, username=username, password=password, port=445)

    smb_path = f"\\\\{server}\\{share}"
    if path and path != "/":
        normalized = _normalize_path(path)
        smb_path = f"{smb_path}\\{normalized}"

    entries = []
    try:
        for entry in smbclient.scandir(smb_path):
            stat = entry.stat()
            entries.append({
                "name": entry.name,
                "is_directory": entry.is_dir(),
                "size": stat.st_size if not entry.is_dir() else 0,
                "last_modified": datetime.fromtimestamp(
                    stat.st_mtime, tz=timezone.utc
                ).isoformat(),
            })
    except Exception as e:
        logger.error(f"Failed to browse {smb_path}: {e}")
        raise

    entries.sort(key=lambda x: (not x["is_directory"], x["name"].lower()))
    return entries


def read_file(
    server: str,
    share: str,
    path: str,
    username: str = "guest",
    password: str = "",
    domain: str = "WORKGROUP",
) -> bytes:
    """Read a file from an SMB share."""
    import smbclient

    smbclient.register_session(server, username=username, password=password, port=445)

    normalized = _normalize_path(path)
    smb_path = f"\\\\{server}\\{share}\\{normalized}"

    with smbclient.open_file(smb_path, mode="rb") as f:
        return f.read()


def list_shares(
    server: str,
    username: str = "guest",
    password: str = "",
    domain: str = "WORKGROUP",
) -> list[str]:
    """List available shares on an SMB server."""
    import smbclient

    smbclient.register_session(server, username=username, password=password, port=445)

    shares = []
    try:
        for entry in smbclient.scandir(f"\\\\{server}"):
            shares.append(entry.name)
    except Exception as e:
        logger.error(f"Failed to list shares on {server}: {e}")
        raise
    return sorted(shares)
