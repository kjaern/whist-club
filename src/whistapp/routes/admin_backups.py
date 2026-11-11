"""Server administration of database backups: make one, list them, download one or the live database."""

from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from starlette.background import BackgroundTask

from .. import backups
from ..auth import is_server_admin
from ..web import DISPLAY_TZ, render

router = APIRouter()

SQLITE_TYPE = "application/vnd.sqlite3"


def _require_server_admin(request):
    # A backup is the whole server's data: every club, login and password hash.
    if not is_server_admin(request):
        raise HTTPException(403, "Kun serveradministrator")


def _size(size):
    for unit in ("B", "KB", "MB"):
        if size < 1000: return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}".replace(".", ",")
        size /= 1000
    return f"{size:.1f} GB".replace(".", ",")


def _page(request, status_code=200, error=None):
    rows = []
    try:
        rows = [dict(row, size_text=_size(row["size"]), modified_text=f"{row['modified']:%Y-%m-%d %H:%M}")
                for row in backups.list_backups()]
    except backups.BackupError as exc:
        error = error or str(exc)
    response = render(request, "admin_backups.html", backups=rows, folder=str(backups.backup_dir()),
                      notice=request.query_params.get("saved", ""), error=error)
    response.status_code = status_code
    return response


@router.get("/admin/backups", response_class=HTMLResponse)
def admin_backups(request: Request):
    _require_server_admin(request)
    return _page(request)


@router.post("/admin/backups", response_class=HTMLResponse)
def make_backup(request: Request):
    _require_server_admin(request)
    try:
        path = backups.create_backup()
    except backups.BackupError as exc:
        return _page(request, 500, error=str(exc))
    return RedirectResponse(f"/admin/backups?saved={path.name}", status_code=303)


@router.get("/admin/backups/download")
def download_live(request: Request):
    _require_server_admin(request)
    try:
        path = backups.snapshot_for_download()
    except backups.BackupError as exc:
        return _page(request, 500, error=str(exc))
    return FileResponse(path, media_type=SQLITE_TYPE, filename=f"whist-{datetime.now(DISPLAY_TZ):%Y-%m-%d-%H%M%S}.sqlite3",
                        background=BackgroundTask(path.unlink, missing_ok=True))


@router.get("/admin/backups/{name}")
def download_backup(request: Request, name: str):
    _require_server_admin(request)
    path = backups.backup_file(name)
    if path is None: raise HTTPException(404)
    return FileResponse(path, media_type=SQLITE_TYPE, filename=name)
