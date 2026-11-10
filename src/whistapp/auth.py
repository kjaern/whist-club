"""Local login and cookie-session access control."""

import logging
import time
from collections import defaultdict, deque
from configparser import ConfigParser
from secrets import compare_digest, token_urlsafe
from .database import connect
from urllib.parse import urlparse

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, VerifyMismatchError
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request

from .settings import CONFIG, settings

_hasher = PasswordHasher()
_attempts = defaultdict(deque)


def secret_settings():
    cfg = ConfigParser(interpolation=None)
    path = CONFIG / "secrets.ini"
    if not cfg.read(path, encoding="utf-8") or not cfg.has_section("auth"):
        raise RuntimeError(f"Missing {path}; run: python -m whistapp.cli init")

    return cfg["auth"]


def verify_login(username, password, client):
    # Small single-worker rate limit, not a substitute for reverse-proxy throttling.
    key = str(client)
    now = time.monotonic()
    q = _attempts[key]
    while q and q[0] < now - 300:
        q.popleft()
    if len(q) >= 10:
        return False

    auth = secret_settings()
    ok = False
    if compare_digest(username, auth.get("username", "")):
        try:
            ok = _hasher.verify(auth["password_hash"], password)
        except (VerifyMismatchError, VerificationError):
            pass

    if not ok:
        q.append(now)
    else:
        q.clear()

    return ok


def new_temporary_password():
    return token_urlsafe(24)


def hash_password(password):
    return _hasher.hash(password)


def verify_account(username, password, client):
    key = str(client)
    now = time.monotonic()
    q = _attempts[key]
    while q and q[0] < now - 300:
        q.popleft()
    if len(q) >= 10:
        return None
    with connect() as db:
        user = db.execute("SELECT * FROM user_accounts WHERE username=?", (username,)).fetchone()
    ok = False
    if user:
        try:
            ok = _hasher.verify(user["password_hash"], password)
        except (VerifyMismatchError, VerificationError):
            pass
    if not ok:
        q.append(now)
        return None
    q.clear()
    return dict(user)


def current_account(request):
    uid = request.session.get("account_id")
    if not isinstance(uid, int):
        return None
    with connect() as db:
        user = db.execute("SELECT * FROM user_accounts WHERE id=?", (uid,)).fetchone()
    if not user or request.session.get("account_version") != user["session_version"]:
        return None
    return dict(user)


def is_server_admin(request):
    return request.session.get("user") == "admin"


def setup_session(app):
    auth = secret_settings()
    key = auth.get("session_key", "")
    if len(key) < 48:
        raise RuntimeError("session_key must contain at least 48 characters")

    secure = settings().getboolean("server", "secure_cookies", fallback=False)
    app.add_middleware(
        SessionMiddleware,
        secret_key=key,
        session_cookie="whist_session",
        same_site="strict",
        https_only=secure,
        max_age=60 * 60 * 24 * 7,
    )


def authenticated(request):
    return request.session.get("user") == "admin" or current_account(request) is not None


# CSRF token: a random value kept in the signed login session (new at every login), sent back by
# every state-changing request, as the form field csrf_token (ordinary forms, rendered by
# csrf_field() in web.py) or the X-CSRF-Token header (htmx and fetch, set in base.html). It is
# never put in a URL and never logged. It comes on top of the same-origin check and SameSite.
CSRF_FIELD = "csrf_token"
CSRF_HEADER = "x-csrf-token"
# Logging in has no session to bind a token to yet; logging out only ends the session.
CSRF_EXEMPT = ("/login", "/logout")
SAFE_METHODS = ("GET", "HEAD", "OPTIONS")
FORM_TYPES = ("application/x-www-form-urlencoded", "multipart/form-data")


def new_csrf_token(request):
    request.session["csrf"] = token_urlsafe(32)
    return request.session["csrf"]


def csrf_token(request):
    """The login session's token, made if the session has none yet (fx a session from before tokens)."""
    token = request.session.get("csrf")
    return token if isinstance(token, str) and len(token) >= 32 else new_csrf_token(request)


async def verify_csrf(request: Request):
    """App-wide dependency: a state-changing request must carry the session's token.

    The form is read with request.form(), which Starlette caches on the request, so the route reads
    the same parsed form afterwards.
    """
    from fastapi import HTTPException
    if request.method in SAFE_METHODS or request.url.path in CSRF_EXEMPT:
        return
    expected = request.session.get("csrf")
    sent = request.headers.get(CSRF_HEADER)
    if sent is None and request.headers.get("content-type", "").split(";")[0].strip().lower() in FORM_TYPES:
        value = (await request.form()).get(CSRF_FIELD)
        sent = value if isinstance(value, str) else None
    if not isinstance(expected, str) or not sent or not compare_digest(sent, expected):
        logging.getLogger(__name__).warning("CSRF token %s for %s %s", "missing" if not sent else "invalid",
                                            request.method, request.url.path)
        raise HTTPException(403, "Sikkerhedsnøglen mangler eller er forældet. Genindlæs siden, og prøv igen.")


def same_origin(request):
    # Deny cross-origin browser form submissions; cookie is SameSite=Strict as well.
    source = request.headers.get("origin") or request.headers.get("referer")
    if not source:
        return True  # Non-browser clients; authentication still required.

    parsed = urlparse(source)
    return parsed.netloc == request.headers.get("host") and parsed.scheme in (
        "http",
        "https",
    )
