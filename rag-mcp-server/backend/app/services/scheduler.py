"""APScheduler-based auto-sync for saved SMB shares."""

import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.services import smb_shares

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None
_running: set[str] = set()


def is_running(name: str) -> bool:
    return name in _running


async def run_share_sync(name: str):
    if name in _running:
        logger.info("Sync already running for %s, skipping", name)
        return
    share = smb_shares.get_decrypted(name)
    if not share:
        logger.warning("Saved share %s not found, skipping sync", name)
        return
    _running.add(name)
    logger.info("Starting scheduled sync for share: %s", name)
    try:
        result = await asyncio.to_thread(
            smb_shares.ingest_directory,
            server=share["server"],
            share=share["share"],
            path=share.get("path", "/"),
            username=share.get("username", "guest"),
            password=share.get("password", ""),
            domain=share.get("domain", "WORKGROUP"),
            collection=share.get("collection", "default"),
            recursive=share.get("recursive", True),
        )
        smb_shares.update_sync_result(name, result)
        logger.info("Sync complete for %s: %d files, %d chunks",
                     name, result["files_processed"], result["total_chunks"])
        _notify(
            f"SMB sync complete: {name}",
            f"{result['files_processed']} files, {result['total_chunks']} chunks ingested.",
            "normal",
        )
    except Exception:
        logger.exception("Sync failed for share: %s", name)
        smb_shares.update_sync_result(name, {"error": "sync failed"})
        _notify(f"SMB sync failed: {name}", "The scheduled sync raised an error. Check backend logs.", "warning")
    finally:
        _running.discard(name)


def _notify(subject: str, description: str, importance: str):
    try:
        from app.services.notify import send_unraid_notification
        send_unraid_notification(subject, description, importance)
    except Exception:
        pass


def start_scheduler():
    global _scheduler
    if _scheduler is not None:
        return
    _scheduler = AsyncIOScheduler()
    for share in smb_shares.list_saved():
        if share.get("auto_sync"):
            _add_job(share["name"], share.get("interval_minutes", 60))
    _scheduler.start()
    logger.info("Scheduler started")


def shutdown():
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("Scheduler shut down")


def _add_job(name: str, interval_minutes: int):
    if not _scheduler:
        return
    _scheduler.add_job(
        run_share_sync,
        "interval",
        minutes=interval_minutes,
        args=[name],
        id=name,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    logger.info("Scheduled job for %s every %d min", name, interval_minutes)


def reschedule(name: str, interval_minutes: int):
    if not _scheduler:
        return
    _add_job(name, interval_minutes)


def unschedule(name: str):
    if not _scheduler:
        return
    try:
        _scheduler.remove_job(name)
        logger.info("Unscheduled job: %s", name)
    except Exception:
        pass
