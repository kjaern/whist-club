"""Club picker and founding new clubs."""

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..auth import current_account
from ..club import active_club_id, initial_finance
from ..database import connect
from ..web import can_create_club, FOUNDING_CLUB_ID, render, user_clubs

router = APIRouter()


def _clubs_page(request, error=None, name=""):
    return render(request, "clubs.html", clubs=user_clubs(request), current=active_club_id(),
                  can_create=can_create_club(request), error=error, name=name)


@router.get("/clubs", response_class=HTMLResponse)
def clubs_page(request: Request):
    return _clubs_page(request)


@router.post("/clubs/select")
def select_club(request: Request, club_id: int = Form(...)):
    if club_id not in {c["id"] for c in user_clubs(request)}:
        raise HTTPException(403, "Du er ikke medlem af den klub")
    request.session["club_id"] = club_id
    return RedirectResponse("/", status_code=303)


@router.post("/clubs/new", response_class=HTMLResponse)
def create_club(request: Request, name: str = Form("")):
    if not can_create_club(request):
        raise HTTPException(403, "Kun aktive medlemmer af den første klub kan oprette en klub")
    name = " ".join(name.split())
    if not 1 <= len(name) <= 80:
        return _clubs_page(request, error="Klubnavnet skal være 1–80 tegn.", name=name)
    account = current_account(request)
    with connect() as db:
        if db.execute("SELECT 1 FROM clubs WHERE name=? COLLATE NOCASE", (name,)).fetchone():
            return _clubs_page(request, error=f"Der findes allerede en klub med navnet {name}.", name=name)
        club_id = db.execute("INSERT INTO clubs(name,created_by_user_id) VALUES (?,?)",
                             (name, account["id"] if account else None)).lastrowid
        rates, fejl40 = initial_finance()
        db.execute("INSERT INTO club_settings(id,placements,fejl40) VALUES (?,?,?)", (club_id, rates, fejl40))
        # The founder joins as the new club's administrator, under their name from the first club.
        if account:
            founder = db.execute("""SELECT cm.name FROM club_members cm
                JOIN member_accounts ma ON ma.member_id=cm.id
                WHERE ma.user_id=? AND cm.club_id=?""", (account["id"], FOUNDING_CLUB_ID)).fetchone()
            member_id = db.execute(
                "INSERT INTO club_members(name,status,is_club_admin,club_id) VALUES (?,'active',1,?)",
                (founder["name"], club_id)).lastrowid
            db.execute("INSERT INTO member_accounts(member_id,user_id) VALUES (?,?)", (member_id, account["id"]))
    request.session["club_id"] = club_id
    return RedirectResponse("/club", status_code=303)
