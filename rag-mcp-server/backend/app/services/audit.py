"""Admin audit log: one JSON line per privileged action.

Best-effort and non-fatal — auditing must NEVER break the request it records,
so every write is wrapped in try/except and read tolerates malformed lines.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)


def _audit_path() -> Path:
    return Path(settings.config_dir) / "audit.log"


def append_audit(actor: str, action: str, target: str = "", detail: dict | None = None) -> None:
    """Append one audit entry as a JSON line. Never raises."""
    try:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "actor": actor,
            "action": action,
            "target": target,
            "detail": detail,
        }
        path = _audit_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        logger.exception("Failed to append audit entry (action=%s)", action)


def read_audit(limit: int = 100) -> list[dict]:
    """Return the last `limit` audit entries (newest last). Tolerant of malformed lines."""
    try:
        path = _audit_path()
        if not path.exists():
            return []
        lines = path.read_text().splitlines()
    except Exception:
        logger.exception("Failed to read audit log")
        return []

    entries: list[dict] = []
    for line in lines[-limit:] if limit and limit > 0 else lines:
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except Exception:
            continue
    return entries
