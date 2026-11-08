"""Editing a closed night. The edit lives in the browser until it is saved in one request.

Starting an edit only writes a line in the evening log. The edit page is one form; nothing
is stored while it is filled in. "Beregn" sends the form to /edit/calculate, which applies
it in a transaction, renders the outcome and rolls back. "Gem" sends the same form as
PUT /sessions/{sid}, which runs the same code and commits: changed hands, Fejl40 and a new
settlement under the night's rule version, all or nothing. A form built before another
device changed the night is refused.
"""

from fractions import Fraction

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from ..database import connect
from ..rule_versions import is_club_night, night_rules
from ..web import can_manage_club, get_session, night_state_token, render, view_data
from .sessions import hand_result, settle_night, write_hand

router = APIRouter()

EDIT_ERRORS = (ValueError, TypeError, KeyError, IndexError)


def _closed_night(request, db, sid):
    if not can_manage_club(request):
        raise HTTPException(403, "Kun klubadministrator kan redigere en afsluttet spilaften")
    session = get_session(db, sid)
    if session["ended_at"] is None:
        raise HTTPException(409, "Spilaftenen er ikke afsluttet")
    return session


def _log(db, request, sid, text):
    db.execute("INSERT INTO evening_log(evening_id,user_id,description) VALUES (?,?,?)",
               (sid, request.session.get("user", "admin"), text))


def _stored(db, hand):
    hid = hand["id"]
    return dict(
        key=hand["bid_key"], target=hand["target"], actual=hand["actual"], partner=hand["partner_id"],
        declarer=db.execute("SELECT player_id FROM hand_declarers WHERE hand_id=?", (hid,)).fetchone()[0],
        specials=sorted(tuple(r) for r in db.execute(
            "SELECT player_id,actual,won FROM special_bids WHERE hand_id=?", (hid,))),
        scores={r[0]: Fraction(r[1], r[2]) for r in db.execute(
            "SELECT player_id,numerator,denominator FROM hand_scores WHERE hand_id=?", (hid,))},
    )


def _changed(stored, r):
    return (stored["key"], stored["target"], stored["actual"], stored["partner"], stored["declarer"],
            stored["specials"], stored["scores"]) != (
        r["key"], r["target"], r["actual"], r["partner"], r["declarer"], sorted(r["specials"]),
        {pid: Fraction(str(v)) for pid, v in r["scores"].items()})


def apply_edit(db, session, form):
    """Apply an edit form to a closed night in the open transaction.

    Returns (what changed, errors, settlement change). With errors the caller rolls back.
    """
    sid = session["id"]
    changes, errors = [], []
    hands = db.execute("SELECT * FROM hands WHERE session_id=? ORDER BY id", (sid,)).fetchall()
    for number, hand in enumerate(hands, 1):
        hid = hand["id"]
        if form.get(f"h{hid}_delete"):
            db.execute("DELETE FROM hands WHERE id=?", (hid,))
            changes.append(f"slettede hånd {number}")
            continue
        if form.get(f"h{hid}_bid_key") is None:
            continue  # not on the form: unchanged

        def get(name, default=None, hid=hid):
            return form.get(f"h{hid}_{name}", default)

        def getlist(name, hid=hid):
            return form.getlist(f"h{hid}_{name}")
        try:
            result = hand_result(db, session, hand, get, getlist)
        except EDIT_ERRORS as exc:
            errors.append(f"Hånd {number}: {exc}")
            continue
        if _changed(_stored(db, hand), result):
            write_hand(db, hid, result)
            changes.append(f"rettede hånd {number}")
    if len(hands) and all(form.get(f"h{h['id']}_delete") for h in hands):
        errors.append("En afsluttet spilaften skal have mindst én hånd.")

    names = {r["id"]: r["name"] for r in db.execute(
        "SELECT p.id,COALESCE(cm.name,p.name) AS name FROM players p LEFT JOIN club_members cm ON cm.id=p.member_id "
        "WHERE p.session_id=?", (sid,))}
    keep = {int(v) for v in form.getlist("keep_penalty") if str(v).isdigit()}
    for row in db.execute("SELECT id,player_id FROM penalty_payments WHERE session_id=?", (sid,)).fetchall():
        if row["id"] not in keep:
            db.execute("DELETE FROM penalty_payments WHERE id=?", (row["id"],))
            changes.append(f"fjernede Fejl40 for {names[row['player_id']]}")
    fee = night_rules(db, sid)["fejl40"]
    for pid, name in names.items():
        try:
            count = int(form.get(f"add_penalty_{pid}") or 0)
        except ValueError:
            count = -1
        if not 0 <= count <= 20:
            errors.append(f"Ugyldigt antal Fejl40 for {name}")
            continue
        db.executemany("INSERT INTO penalty_payments(session_id,player_id,amount) VALUES (?,?,?)",
                       [(sid, pid, fee)] * count)
        if count:
            changes.append(f"tilføjede {count} × Fejl40 for {name}")

    if errors:
        return changes, errors, ""
    collect = bool(session["collect_club_money"] and is_club_night(db, sid))
    try:
        settlement = settle_night(db, sid, collect) if changes else ""
    except EDIT_ERRORS as exc:
        return changes, [f"Afregningen kunne ikke beregnes: {exc}"], ""
    return changes, errors, settlement


def _outcome(db, before, sid, changes, errors, settlement):
    """Totals per player before and after the edit, for the preview."""
    after = view_data(db, sid)
    totals = [dict(name=p["name"], before=before["totals"][p["id"]], after=after["totals"][p["id"]])
              for p in after["players"]]
    return dict(changes=changes, errors=errors, settlement=settlement, totals=totals)


@router.post("/sessions/{sid}/edit/start")
def start_edit(request: Request, sid: int):
    """Starting an edit changes nothing about the night; it is only logged."""
    with connect() as db:
        _closed_night(request, db, sid)
        _log(db, request, sid, "Begyndte at redigere den afsluttede spilaften")
    return RedirectResponse(f"/sessions/{sid}/edit", status_code=303)


@router.get("/sessions/{sid}/edit", response_class=HTMLResponse)
def edit_page(request: Request, sid: int):
    with connect() as db:
        _closed_night(request, db, sid)
        data = view_data(db, sid)
    return render(request, "night_edit.html", **data)


@router.post("/sessions/{sid}/edit/calculate", response_class=HTMLResponse)
async def calculate_edit(request: Request, sid: int):
    """What saving the form would do. It runs the save in a transaction and rolls it back."""
    form = await request.form()
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        session = _closed_night(request, db, sid)
        stale = form.get("night_state") != night_state_token(db, sid)
        before = view_data(db, sid)
        try:
            changes, errors, settlement = apply_edit(db, session, form)
            outcome = _outcome(db, before, sid, changes, errors, settlement)
        finally:
            db.rollback()
    return render(request, "partials/night_edit_preview.html", stale=stale, saved=False, **outcome)


@router.put("/sessions/{sid}", response_class=HTMLResponse)
async def save_edit(request: Request, sid: int):
    """Save an edited closed night: all of it in one transaction, or nothing."""
    form = await request.form()
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")  # two devices cannot both pass the checks below
        session = _closed_night(request, db, sid)
        if form.get("night_state") != night_state_token(db, sid):
            db.rollback()
            return render(request, "partials/night_edit_preview.html", stale=True, saved=False,
                          changes=[], errors=["Intet blev gemt."], settlement="", totals=[])
        before = view_data(db, sid)
        changes, errors, settlement = apply_edit(db, session, form)
        if errors or not changes:
            outcome = _outcome(db, before, sid, changes, errors, settlement)
            db.rollback()
            return render(request, "partials/night_edit_preview.html", stale=False, saved=False, **outcome)
        _log(db, request, sid, "Redigeret: " + ", ".join(changes) + (f"; {settlement}" if settlement else ""))
    return Response(status_code=200, headers={"HX-Redirect": f"/sessions/{sid}"})
