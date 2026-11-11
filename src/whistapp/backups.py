"""Database backups: copies made with SQLite's online backup in a folder of their own.

The folder is WHIST_BACKUP_DIR (relative to the Whist home), or `backups/` in the home.
In Docker it is a separate bind mount, so it can live on another disk. Backups are only
ever added here: the app never rewrites or deletes one. A monthly backup is made
automatically (`ensure_monthly`, checked at startup and every hour while the server runs).
"""

import asyncio
import logging
import os
import re
import sqlite3
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

from .database import db_path
from .settings import HOME
from .web import DISPLAY_TZ

# Uvicorn configures this logger, so the lines reach `docker compose logs`.
log = logging.getLogger("uvicorn.error")

CHECK_SECONDS = 3600
KIND_LABELS = {"manual": "Manuel", "monthly": "Månedlig"}
NAME = re.compile(r"^whist-(manual|monthly)-([0-9]{4}-[0-9]{2}(?:-[0-9]{2}-[0-9]{6}(?:-[0-9]+)?)?)\.sqlite3$")
PARTIAL_PREFIX = ".whist-partial-"
DOWNLOAD_PREFIX = "whist-download-"
STALE_DOWNLOAD_SECONDS = 3600

# One backup is written at a time (the server is one process), so names and partial files never clash.
_writing = threading.Lock()


def _reason(exc):
    return getattr(exc, "strerror", None) or str(exc)


class BackupError(Exception):
    pass


def backup_dir():
    """Read on every call, so the folder can be changed by the environment (and in tests)."""
    return HOME / Path(os.environ.get("WHIST_BACKUP_DIR") or "backups").expanduser()


def _now():
    return datetime.now(DISPLAY_TZ)


def _copy_database(target):
    """A consistent copy of the live database at `target`, checked before it is trusted."""
    if not db_path().is_file(): raise BackupError(f"Databasen findes ikke: {db_path()}")
    src = sqlite3.connect(db_path(), timeout=10)
    try:
        dst = sqlite3.connect(target)
        try:
            src.backup(dst)
            ok = dst.execute("PRAGMA quick_check").fetchone()[0]
        finally:
            dst.close()
    finally:
        src.close()
    if ok != "ok":
        raise BackupError(f"Kopien bestod ikke SQLite's kontrol: {ok}")


def _free_name(folder, stem):
    """`stem`.sqlite3, or with -2, -3 ... when a backup of that name exists already."""
    name, n = f"{stem}.sqlite3", 1
    while (folder / name).exists():
        n += 1
        name = f"{stem}-{n}.sqlite3"
    return name


def _write(stem, unique=True):
    """Write a backup into the folder; a half-written file never gets a backup's name.

    unique: pick a free name rather than skipping when `stem` exists; an existing backup is never replaced."""
    folder = backup_dir()
    with _writing:
        try:
            folder.mkdir(parents=True, exist_ok=True)
            # Left by a server stopped mid-copy; with the lock held no copy is running now.
            for stale in folder.glob(f"{PARTIAL_PREFIX}*"): stale.unlink(missing_ok=True)
            if unique: name = _free_name(folder, stem)
            elif (folder / f"{stem}.sqlite3").exists(): return None
            else: name = f"{stem}.sqlite3"
            partial = folder / f"{PARTIAL_PREFIX}{name}"
            try:
                _copy_database(partial)
                os.replace(partial, folder / name)
            finally:
                partial.unlink(missing_ok=True)
        except (OSError, sqlite3.Error) as exc:
            raise BackupError(f"Kunne ikke skrive backup i {folder}: {_reason(exc)}") from exc
    return folder / name


def create_backup(now=None):
    """A manual backup, named after the moment it was made."""
    return _write(f"whist-manual-{(now or _now()):%Y-%m-%d-%H%M%S}")


def ensure_monthly(now=None):
    """Make this month's backup unless it exists; returns its path when one was made."""
    return _write(f"whist-monthly-{(now or _now()):%Y-%m}", unique=False)


def list_backups():
    """Backups in the folder, newest first. Other files there (it may be a shared disk) are ignored."""
    folder = backup_dir()
    try:
        entries = list(folder.iterdir())
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise BackupError(f"Kunne ikke læse {folder}: {_reason(exc)}") from exc
    rows = []
    for path in entries:
        match = NAME.match(path.name)
        if not match: continue
        try:
            if not path.is_file(): continue
            stat = path.stat()
        except OSError:
            continue  # removed, or the disk went away, while listing
        rows.append({
            "name": path.name,
            "kind": KIND_LABELS[match.group(1)],
            "size": stat.st_size,
            "modified": datetime.fromtimestamp(stat.st_mtime, DISPLAY_TZ),
        })
    return sorted(rows, key=lambda row: row["modified"], reverse=True)


def backup_file(name):
    """The path of a listed backup; only names this module writes are accepted, so no path escapes the folder."""
    if not NAME.match(name): return None
    path = backup_dir() / name
    return path if path.is_file() else None


def _remove_stale_downloads():
    """Downloads whose cleanup never ran (the client went away mid-transfer); /tmp may be in memory."""
    cutoff = time.time() - STALE_DOWNLOAD_SECONDS
    for path in Path(tempfile.gettempdir()).glob(f"{DOWNLOAD_PREFIX}*.sqlite3"):
        try:
            if path.stat().st_mtime < cutoff: path.unlink()
        except OSError:
            pass


def snapshot_for_download():
    """A fresh copy of the database in a temporary file, for downloading without keeping it."""
    _remove_stale_downloads()
    try:
        handle, name = tempfile.mkstemp(prefix=DOWNLOAD_PREFIX, suffix=".sqlite3")
        os.close(handle)
    except OSError as exc:
        raise BackupError(f"Kunne ikke lave en midlertidig kopi: {_reason(exc)}") from exc
    try:
        _copy_database(name)
    except (OSError, sqlite3.Error) as exc:
        os.unlink(name)
        raise BackupError(f"Kunne ikke lave en midlertidig kopi: {_reason(exc)}") from exc
    except BaseException:
        os.unlink(name)
        raise
    return Path(name)


async def monthly_loop():
    """Runs with the server: makes the month's backup as soon as a new month has begun."""
    while True:
        try:
            made = await asyncio.to_thread(ensure_monthly)
            if made: log.info("Monthly backup written to %s", made)
        except Exception:
            log.exception("Monthly backup failed")
        await asyncio.sleep(CHECK_SECONDS)
