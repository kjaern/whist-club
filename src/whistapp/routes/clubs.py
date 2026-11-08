"""Club picker and founding new clubs."""

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..auth import current_account
from ..club import active_club_id, initial_finance
from ..database import connect
from ..demo_club import fill_test_club
from ..rule_versions import save_version
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


def _found_club(db, name, account):
    """A new club with default settings; the founder joins as its administrator."""
    club_id = db.execute("INSERT INTO clubs(name,created_by_user_id) VALUES (?,?)",
                         (name, account["id"] if account else None)).lastrowid
    rates, fejl40 = initial_finance()
    db.execute("INSERT INTO club_settings(id,placements,fejl40) VALUES (?,?,?)", (club_id, rates, fejl40))
    # The founder joins as the new club's administrator, under their name from the first club.
    founder = account and db.execute("""SELECT cm.name FROM club_members cm
        JOIN member_accounts ma ON ma.member_id=cm.id
        WHERE ma.user_id=? AND cm.club_id=?""", (account["id"], FOUNDING_CLUB_ID)).fetchone()
    if founder:
        member_id = db.execute(
            "INSERT INTO club_members(name,status,is_club_admin,club_id) VALUES (?,'active',1,?)",
            (founder["name"], club_id)).lastrowid
        db.execute("INSERT INTO member_accounts(member_id,user_id) VALUES (?,?)", (member_id, account["id"]))
    save_version(db, club_id)
    return club_id


@router.post("/clubs/new", response_class=HTMLResponse)
def create_club(request: Request, name: str = Form("")):
    if not can_create_club(request):
        raise HTTPException(403, "Kun aktive medlemmer af den første klub kan oprette en klub")
    name = " ".join(name.split())
    if not 1 <= len(name) <= 80:
        return _clubs_page(request, error="Klubnavnet skal være 1–80 tegn.", name=name)
    with connect() as db:
        if db.execute("SELECT 1 FROM clubs WHERE name=? COLLATE NOCASE", (name,)).fetchone():
            return _clubs_page(request, error=f"Der findes allerede en klub med navnet {name}.", name=name)
        club_id = _found_club(db, name, current_account(request))
    request.session["club_id"] = club_id
    return RedirectResponse("/club", status_code=303)


@router.post("/clubs/new-test")
def create_test_club(request: Request):
    """A club of made-up members with five years of closed nights, for trying things out."""
    if not can_create_club(request):
        raise HTTPException(403, "Kun aktive medlemmer af den første klub kan oprette en klub")
    with connect() as db:
        taken = {r[0].casefold() for r in db.execute("SELECT name FROM clubs")}
        name = next(n for n in ("Testklub", *(f"Testklub {i}" for i in range(2, 1000)))
                    if n.casefold() not in taken)
        club_id = _found_club(db, name, current_account(request))
        fill_test_club(db, club_id)
    request.session["club_id"] = club_id
    return RedirectResponse("/statistics", status_code=303)
