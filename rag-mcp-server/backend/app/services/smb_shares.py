"""Saved SMB share profiles and the reusable ingest_directory function."""

import logging
from datetime import datetime, timezone

from app.config import load_config, save_config
from app.services import rag_engine, smb_browser
from app.services.crypto import decrypt, encrypt
from app.services.document_parser import can_parse, parse_file

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Saved share CRUD
# ---------------------------------------------------------------------------

def list_saved() -> list[dict]:
    config = load_config()
    out = []
    for s in config.get("smb_shares", []):
        out.append({
            "name": s["name"],
            "server": s["server"],
            "share": s["share"],
            "path": s.get("path", "/"),
            "username": s.get("username", "guest"),
            "domain": s.get("domain", "WORKGROUP"),
            "collection": s.get("collection", "default"),
            "recursive": s.get("recursive", True),
            "auto_sync": s.get("auto_sync", False),
            "interval_minutes": s.get("interval_minutes", 60),
            "last_sync": s.get("last_sync"),
            "last_result": s.get("last_result"),
        })
    return out


def save_share(data: dict) -> dict:
    config = load_config()
    shares = config.setdefault("smb_shares", [])
    entry = {
        "name": data["name"],
        "server": data["server"],
        "share": data["share"],
        "path": data.get("path", "/"),
        "username": data.get("username", "guest"),
        "domain": data.get("domain", "WORKGROUP"),
        "encrypted_password": encrypt(data.get("password", "")),
        "collection": data.get("collection", "default"),
        "recursive": data.get("recursive", True),
        "auto_sync": data.get("auto_sync", False),
        "interval_minutes": data.get("interval_minutes", 60),
        "last_sync": None,
        "last_result": None,
    }
    idx = next((i for i, s in enumerate(shares) if s["name"] == data["name"]), None)
    if idx is not None:
        entry["last_sync"] = shares[idx].get("last_sync")
        entry["last_result"] = shares[idx].get("last_result")
        shares[idx] = entry
    else:
        shares.append(entry)
    save_config(config)
    safe = {k: v for k, v in entry.items() if k != "encrypted_password"}
    return safe


def delete_share(name: str) -> bool:
    config = load_config()
    shares = config.get("smb_shares", [])
    original = len(shares)
    config["smb_shares"] = [s for s in shares if s["name"] != name]
    if len(config["smb_shares"]) < original:
        save_config(config)
        return True
    return False


def get_decrypted(name: str) -> dict | None:
    config = load_config()
    for s in config.get("smb_shares", []):
        if s["name"] == name:
            return {
                **{k: v for k, v in s.items() if k != "encrypted_password"},
                "password": decrypt(s.get("encrypted_password", encrypt(""))),
            }
    return None


def update_sync_result(name: str, result: dict):
    config = load_config()
    for s in config.get("smb_shares", []):
        if s["name"] == name:
            s["last_sync"] = datetime.now(timezone.utc).isoformat()
            s["last_result"] = result
            break
    save_config(config)


# ---------------------------------------------------------------------------
# Reusable ingest_directory (sync — must be called via asyncio.to_thread
# when invoked from async context to keep the event loop responsive).
# ---------------------------------------------------------------------------

def ingest_directory(
    server: str,
    share: str,
    path: str,
    username: str,
    password: str,
    domain: str,
    collection: str,
    recursive: bool,
) -> dict:
    try:
        entries = smb_browser.browse_share(
            server=server, share=share, path=path,
            username=username, password=password, domain=domain,
        )
    except Exception:
        logger.exception("SMB browse error: server=%s share=%s path=%s", server, share, path)
        return {"files_processed": 0, "total_chunks": 0, "errors": [f"{path}: SMB browse failed"]}

    total_chunks = 0
    files_processed = 0
    errors: list[str] = []

    for entry in entries:
        if entry["is_directory"]:
            if recursive:
                sub_path = f"{path.rstrip('/')}/{entry['name']}"
                sub = ingest_directory(
                    server, share, sub_path,
                    username, password, domain,
                    collection, recursive,
                )
                total_chunks += sub["total_chunks"]
                files_processed += sub["files_processed"]
                errors.extend(sub["errors"])
            continue

        if not can_parse(entry["name"]):
            continue

        file_path = f"{path.rstrip('/')}/{entry['name']}"
        try:
            content = smb_browser.read_file(
                server=server, share=share, path=file_path,
                username=username, password=password, domain=domain,
            )
            text = parse_file(content=content, filename=entry["name"])
            if text.strip():
                source = f"smb://{server}/{share}{file_path}"
                chunks = rag_engine.ingest_text(text, source=source, collection_name=collection)
                total_chunks += chunks
                files_processed += 1
        except Exception:
            logger.exception("SMB file ingest failed: %s", file_path)
            errors.append(f"{file_path}: ingest failed")

    return {
        "files_processed": files_processed,
        "total_chunks": total_chunks,
        "errors": errors,
    }
