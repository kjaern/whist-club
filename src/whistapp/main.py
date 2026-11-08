from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .auth import authenticated, current_account, is_server_admin, same_origin, setup_session
from .club import active_club_id, active_club_reset, active_club_set
from .database import (
    audit_actor_reset,
    audit_actor_set,
    audit_operation_reset,
    audit_operation_set,
    init_db,
)
from .web import (
    ai_import_allowed,
    can_manage_club,
    in_active_club,
    linked_member,
    member_operation_allowed,
    resolve_active_club,
    ROOT,
)
from .routes import accounts, admin_ai, clubs, guide, kasse, sessions, club, imports, rules


@asynccontextmanager
async def lifespan(_app):
    init_db()
    yield


app = FastAPI(title="Whist", lifespan=lifespan)


@app.middleware("http")
async def require_authentication(request: Request, call_next):
    path = request.url.path
    if path == "/login" or path == "/logout":
        return await call_next(request)
    if not authenticated(request):
        if path.startswith("/static/"):
            return await call_next(request)
        if path.startswith("/sessions/") and path.endswith("/editor/events"):
            return PlainTextResponse("Login required", status_code=401)
        if request.headers.get("HX-Request"):
            return HTMLResponse("", status_code=401, headers={"HX-Redirect": "/login"})
        return RedirectResponse("/login", status_code=303)
    club_token = active_club_set(resolve_active_club(request))
    try:
        return await _authorized_request(request, call_next, path)
    finally:
        active_club_reset(club_token)


# Pages usable before a club is chosen: account upkeep and the club picker.
CLUBLESS_PATHS = ("/account", "/account/password", "/logout", "/clubs", "/clubs/select", "/clubs/new", "/clubs/new-test", "/guide")


async def _authorized_request(request, call_next, path):
    # Server-wide account administration does not belong to a club.
    clubless = path in CLUBLESS_PATHS or path.startswith("/static/") or path.startswith("/admin/")
    if not is_server_admin(request):
        account = current_account(request)
        if account and account["must_change_password"] and path not in ("/", "/account", "/account/password", "/logout") and not path.startswith("/static/"):
            return RedirectResponse("/account", status_code=303)
        if path.startswith("/admin/"):
            return PlainTextResponse("Kun serveradministrator", status_code=403)
    if not clubless and active_club_id() is None:
        if request.method in ("GET", "HEAD") and not request.headers.get("HX-Request"):
            return RedirectResponse("/clubs", status_code=303)
        return PlainTextResponse("Vælg en klub", status_code=403)
    if not is_server_admin(request):
        if path != "/" and not clubless:
            if not linked_member(request):
                return PlainTextResponse("Ingen aktivt klubmedlemskab", status_code=403)
            if not can_manage_club(request) and not member_operation_allowed(path, request.method):
                return PlainTextResponse("Kun klubadministrator", status_code=403)
            # Club administrators may manage club operations, not global account linking.
            if path.startswith("/club/members/") and path.endswith("/link"):
                return PlainTextResponse("Kun serveradministrator", status_code=403)
    if not clubless and not in_active_club(path):
        return PlainTextResponse("Findes ikke i den valgte klub", status_code=404)
    if (path == "/import/ai" or path == "/import/current" or path.startswith("/import/current/")) and not ai_import_allowed():
        return PlainTextResponse("AI-import er ikke aktiveret for klubben", status_code=403)
    if request.method not in ("GET", "HEAD", "OPTIONS") and not same_origin(request):
        return PlainTextResponse("Cross-origin request rejected", status_code=403)
    account = current_account(request) if not is_server_admin(request) else None
    actor = account["username"] if account else ("admin" if is_server_admin(request) else "anonymous")
    token = audit_actor_set(actor, account["id"] if account else None)
    operation_token = audit_operation_set()
    try:
        return await call_next(request)
    finally:
        audit_operation_reset(operation_token)
        audit_actor_reset(token)


setup_session(app)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
app.include_router(accounts.router)
app.include_router(admin_ai.router)
app.include_router(clubs.router)
app.include_router(guide.router)
app.include_router(rules.router)
app.include_router(sessions.router)
app.include_router(club.router)
app.include_router(imports.router)
app.include_router(kasse.router)
