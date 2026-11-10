"""Login, own account, server account administration and member logins."""

import re
import sqlite3

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse

from ..auth import (
    new_csrf_token,
    authenticated,
    current_account,
    hash_password,
    is_server_admin,
    new_temporary_password,
    same_origin,
    verify_account,
    verify_login,
)
from ..database import connect
from ..web import can_manage_club, render

router = APIRouter()


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if authenticated(request):
        return RedirectResponse("/", status_code=303)
    return render(request, "login.html", error=None)


@router.post("/login", response_class=HTMLResponse)
def login(request: Request, username: str = Form(...), password: str = Form(...)):
    if not same_origin(request):
        return PlainTextResponse("Cross-origin request rejected", status_code=403)
    client = request.client.host if request.client else "unknown"
    account = verify_account(username, password, client)
    if account:
        request.session.clear()
        request.session["account_id"] = account["id"]
        request.session["account_version"] = account["session_version"]
        new_csrf_token(request)
        return RedirectResponse("/account" if account["must_change_password"] else "/", status_code=303)
    if not verify_login(username, password, client):
        return render(
            request, "login.html", error="Forkert brugernavn eller adgangskode"
        )
    request.session.clear()
    request.session["user"] = "admin"
    new_csrf_token(request)
    return RedirectResponse("/", status_code=303)


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@router.get("/account", response_class=HTMLResponse)
def account_page(request: Request):
    account = current_account(request)
    if not account:
        return RedirectResponse("/", status_code=303)
    return render(request, "account.html", account=account, error=None, saved=False)


@router.post("/account/password", response_class=HTMLResponse)
def account_password(request: Request, current_password: str = Form(...),
                     new_password: str = Form(...), confirm_password: str = Form(...)):
    account = current_account(request)
    if not account:
        raise HTTPException(403)
    error = None
    if new_password != confirm_password:
        error = "Adgangskoderne er ikke ens"
    elif len(new_password) < 8 or len(new_password) > 256:
        error = "Adgangskoden skal være 8–256 tegn"
    elif not verify_account(account["username"], current_password,
                            request.client.host if request.client else "unknown"):
        error = "Nuværende adgangskode er forkert"
    if error:
        return render(request, "account.html", account=account, error=error, saved=False)
    with connect() as db:
        db.execute("""UPDATE user_accounts SET password_hash=?, must_change_password=0,
                     session_version=session_version+1 WHERE id=?""",
                   (hash_password(new_password), account["id"]))
        version = db.execute("SELECT session_version FROM user_accounts WHERE id=?",
                             (account["id"],)).fetchone()[0]
    request.session["account_version"] = version
    return RedirectResponse("/", status_code=303)


@router.get("/admin/accounts", response_class=HTMLResponse)
def admin_accounts(request: Request):
    if not is_server_admin(request):
        raise HTTPException(403)
    with connect() as db:
        accounts = db.execute("""SELECT u.id,u.username,u.must_change_password,
            GROUP_CONCAT(cm.name || ' (' || c.name || ')', ', ') AS member_name FROM user_accounts u
            LEFT JOIN member_accounts ma ON ma.user_id=u.id
            LEFT JOIN club_members cm ON cm.id=ma.member_id
            LEFT JOIN clubs c ON c.id=cm.club_id
            GROUP BY u.id ORDER BY u.username""").fetchall()
        members = db.execute("""SELECT cm.id,cm.name FROM club_members cm
            LEFT JOIN member_accounts ma ON ma.member_id=cm.id
            WHERE ma.member_id IS NULL ORDER BY cm.name""").fetchall()
    return render(request, "accounts.html", accounts=accounts, members=members,
                  temporary=None, error=None)


@router.post("/admin/accounts", response_class=HTMLResponse)
def admin_create_account(request: Request, username: str = Form(...),
                         member_id: int = Form(0)):
    if not is_server_admin(request):
        raise HTTPException(403)
    username = username.strip()
    if username.lower() == "admin" or not re.fullmatch(r"[A-Za-z0-9_.-]{3,64}", username):
        raise HTTPException(400, "Brugernavn skal være 3–64 tegn (bogstaver, tal, ._-)")
    temporary = new_temporary_password()
    try:
        with connect() as db:
            if member_id and not db.execute("SELECT 1 FROM club_members WHERE id=?", (member_id,)).fetchone():
                raise HTTPException(400, "Medlem findes ikke")
            cursor = db.execute("INSERT INTO user_accounts(username,password_hash) VALUES (?,?)",
                                (username, hash_password(temporary)))
            if member_id:
                db.execute("INSERT INTO member_accounts(member_id,user_id) VALUES (?,?)",
                           (member_id, cursor.lastrowid))
    except sqlite3.IntegrityError:
        raise HTTPException(409, "Brugernavn eller medlem er allerede tilknyttet")
    return render(request, "temporary_password.html", username=username, temporary=temporary)


@router.post("/club/members/{member_id}/login", response_class=HTMLResponse)
def create_member_login(request: Request, member_id: int, username: str = Form(...)):
    if not can_manage_club(request):
        raise HTTPException(403)
    username = username.strip()
    if username.lower() == "admin" or not re.fullmatch(r"[A-Za-z0-9_.-]{3,64}", username):
        raise HTTPException(400, "Ugyldigt brugernavn (3–64 tegn: bogstaver, tal, ._-)")
    temporary = new_temporary_password()
    try:
        with connect() as db:
            if not db.execute("SELECT 1 FROM club_members WHERE id=?", (member_id,)).fetchone():
                raise HTTPException(404, "Medlem findes ikke")
            if db.execute("SELECT 1 FROM member_accounts WHERE member_id=?", (member_id,)).fetchone():
                raise HTTPException(409, "Medlemmet har allerede et login")
            uid = db.execute(
                "INSERT INTO user_accounts(username,password_hash) VALUES (?,?)",
                (username, hash_password(temporary)),
            ).lastrowid
            db.execute("INSERT INTO member_accounts(member_id,user_id) VALUES (?,?)", (member_id, uid))
    except sqlite3.IntegrityError:
        raise HTTPException(409, "Brugernavnet er optaget eller medlemmet har allerede et login")
    return render(request, "temporary_password.html", username=username, temporary=temporary)


@router.post("/club/members/{member_id}/link", response_class=HTMLResponse)
def link_member_login(request: Request, member_id: int, account_id: int = Form(...)):
    # Linking identities is privileged: a matching name alone is insufficient.
    if not is_server_admin(request):
        raise HTTPException(403)
    try:
        with connect() as db:
            if not db.execute("SELECT 1 FROM club_members WHERE id=?", (member_id,)).fetchone():
                raise HTTPException(404, "Medlem findes ikke")
            if not db.execute("SELECT 1 FROM user_accounts WHERE id=?", (account_id,)).fetchone():
                raise HTTPException(404, "Konto findes ikke")
            if db.execute("""SELECT 1 FROM member_accounts ma JOIN club_members cm ON cm.id=ma.member_id
                    WHERE ma.user_id=? AND cm.club_id=(SELECT club_id FROM club_members WHERE id=?)""",
                    (account_id, member_id)).fetchone():
                raise HTTPException(409, "Kontoen er allerede knyttet til et medlem i denne klub")
            db.execute("INSERT INTO member_accounts(member_id,user_id) VALUES (?,?)", (member_id, account_id))
    except sqlite3.IntegrityError:
        raise HTTPException(409, "Medlemmet er allerede tilknyttet en konto")
    return RedirectResponse("/club", status_code=303)


@router.post("/club/members/{member_id}/invitation/reset", response_class=HTMLResponse)
def reset_member_invitation(request: Request, member_id: int):
    # A club administrator may renew an invitation only until first activation.
    # Once a user sets their password, recovery belongs to the server admin.
    if not can_manage_club(request):
        raise HTTPException(403)
    temporary = new_temporary_password()
    with connect() as db:
        user = db.execute("""SELECT u.id,u.username,u.must_change_password
            FROM member_accounts ma JOIN user_accounts u ON u.id=ma.user_id
            WHERE ma.member_id=?""", (member_id,)).fetchone()
        if not user:
            raise HTTPException(404, "Medlemmet har ingen logininvitation")
        if not user["must_change_password"]:
            raise HTTPException(403, "Aktiverede konti kan kun nulstilles af serveradministratoren")
        db.execute("""UPDATE user_accounts SET password_hash=?,
            session_version=session_version+1 WHERE id=? AND must_change_password=1""",
            (hash_password(temporary), user["id"]))
    return render(request, "temporary_password.html", username=user["username"],
                  temporary=temporary, back_url="/club")


@router.post("/admin/accounts/{uid}/reset", response_class=HTMLResponse)
def admin_reset_account(request: Request, uid: int):
    if not is_server_admin(request):
        raise HTTPException(403)
    temporary = new_temporary_password()
    with connect() as db:
        user = db.execute("SELECT username FROM user_accounts WHERE id=?", (uid,)).fetchone()
        if not user:
            raise HTTPException(404)
        db.execute("""UPDATE user_accounts SET password_hash=?, must_change_password=1,
                     session_version=session_version+1 WHERE id=?""",
                   (hash_password(temporary), uid))
    return render(request, "temporary_password.html", username=user["username"], temporary=temporary)
