"""Optional Unraid system notifications.

No-op unless the host notify script is mounted into the container at
/usr/local/bin/unraid-notify (see docker-compose.yml commented volume). This
keeps the stack fully self-contained by default.
"""

import logging
import shutil
import subprocess

logger = logging.getLogger(__name__)

_NOTIFY_BIN = "/usr/local/bin/unraid-notify"


def send_unraid_notification(subject: str, description: str, importance: str = "normal") -> bool:
    """importance: normal | warning | alert. Returns True if dispatched."""
    if not shutil.which(_NOTIFY_BIN) and not _exists(_NOTIFY_BIN):
        return False
    try:
        subprocess.run(
            [_NOTIFY_BIN, "-e", "RAG MCP Server", "-s", subject, "-d", description, "-i", importance],
            timeout=10, check=False,
        )
        return True
    except Exception:
        logger.exception("Unraid notification failed")
        return False


def _exists(path: str) -> bool:
    import os
    return os.path.exists(path)
