"""Server administration of AI photo import: provider, request limits, and which clubs may use it."""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..auth import is_server_admin
from ..database import connect
from ..vision_import import (
    admin_llm_settings,
    llm_limits,
    remove_admin_llm_settings,
    save_admin_llm_settings,
    save_llm_limits,
)
from ..web import render

router = APIRouter()

MAX_AI_NOTES = 20000


def _require_server_admin(request):
    # AI import spends the server's own API key, so every setting here is the server admin's.
    if not is_server_admin(request):
        raise HTTPException(403, "Kun serveradministrator")


def _page(request, status_code=200, **extra):
    with connect() as db:
        clubs = db.execute("""SELECT c.id,c.name,s.allow_ai_import,s.ai_notes FROM clubs c
            JOIN club_settings s ON s.id=c.id ORDER BY c.id""").fetchall()
    limits = llm_limits()
    context = {
        "llm_settings": admin_llm_settings(),
        "limits": dict(limits, max_total_mb=limits["max_total_bytes"] // 1_000_000),
        "clubs": clubs,
        "llm_form_values": {},
        "notice": request.query_params.get("saved", ""),
        "error": None,
        "error_section": "",
        "max_ai_notes": MAX_AI_NOTES,
    }
    context.update(extra)
    response = render(request, "admin_ai.html", **context)
    response.status_code = status_code
    return response


@router.get("/admin/ai", response_class=HTMLResponse)
def admin_ai(request: Request):
    _require_server_admin(request)
    return _page(request)


@router.post("/admin/ai/llm", response_class=HTMLResponse)
async def save_llm(request: Request):
    _require_server_admin(request)
    form = await request.form()
    base_url, model = str(form.get("base_url", "")), str(form.get("model", ""))
    try:
        save_admin_llm_settings(base_url, model, form.get("api_key", ""))
    except ValueError as exc:
        # Never echo the submitted key back into the page.
        return _page(request, llm_form_values={"base_url": base_url, "model": model},
                     error=str(exc), error_section="llm")
    return RedirectResponse("/admin/ai?saved=llm", status_code=303)


@router.post("/admin/ai/llm/remove")
def remove_llm(request: Request):
    _require_server_admin(request)
    remove_admin_llm_settings()
    return RedirectResponse("/admin/ai?saved=llm_removed", status_code=303)


@router.post("/admin/ai/limits", response_class=HTMLResponse)
async def save_limits(request: Request):
    _require_server_admin(request)
    form = await request.form()
    values = {key: form.get(key, "") for key in ("timeout", "max_images", "max_tokens")}
    try:
        values["max_total_bytes"] = int(str(form.get("max_total_mb", "")).strip()) * 1_000_000
        save_llm_limits(values)
    except ValueError as exc:
        message = "Samlet fotostørrelse skal være et helt tal" if "invalid literal" in str(exc) else str(exc)
        return _page(request, error=message, error_section="limits")
    return RedirectResponse("/admin/ai?saved=limits", status_code=303)


@router.post("/admin/ai/clubs/{club_id}", response_class=HTMLResponse)
async def save_club_ai(request: Request, club_id: int):
    _require_server_admin(request)
    form = await request.form()
    allow = form.get("allow_ai_import") == "true"
    notes = str(form.get("ai_notes", "")).replace("\r\n", "\n").strip()
    with connect() as db:
        if db.execute("SELECT 1 FROM club_settings WHERE id=?", (club_id,)).fetchone() is None:
            raise HTTPException(404)
    if allow and not admin_llm_settings().get("configured"):
        return _page(request, 400, error="Konfigurér først en AI-udbyder.", error_section=f"club-{club_id}")
    if len(notes) > MAX_AI_NOTES:
        return _page(request, 400, error=f"AI-noterne må højst være {MAX_AI_NOTES} tegn.",
                     error_section=f"club-{club_id}")
    with connect() as db:
        db.execute("UPDATE club_settings SET allow_ai_import=?, ai_notes=? WHERE id=?", (int(allow), notes, club_id))
    return RedirectResponse(f"/admin/ai?saved=club-{club_id}", status_code=303)
