"""Klubkasse: every member reads it; only the club's kasserer writes to it."""

import logging
import sqlite3
from fractions import Fraction
from html import escape

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..auth import is_server_admin
from ..club import active_club_id
from ..database import connect
from ..kasse import ledger
from ..kasse.base import UnknownEntryType, parse_amount
from ..kasse.registry import ENTRY_TYPES, RULE_TITLES, rule_sources
from ..web import can_manage_club, linked_member, local_time, render

router = APIRouter()


def is_kasserer(request, db):
    member = linked_member(request)
    found = ledger.kasserer(db, active_club_id())
    return bool(member and found and member["id"] == found["id"])


UNKNOWN_ENTRY = ("Klubkassen kan ikke vises: den indeholder en postering af en type, appen ikke kender, "
                 "så saldi og kassebeholdning ville blive forkerte. Kontakt serveradministratoren.")


def finalize_nights(club_id):
    """Post the club's due nights before its balances are shown (see ledger.finalize_due).

    Called when a page that shows balances is read. The request has already passed the login and
    club checks; it posts nothing it chose itself, only what the database says is due, in its own
    BEGIN IMMEDIATE transaction. A night that cannot be worked out is skipped (ledger.try_post_night)
    and the others still post. If posting fails the page still shows the right I alt, because a
    night that is not posted counts in Afventer; the page never fails because of it.
    """
    if club_id is None:
        return
    try:
        with connect() as db:
            if not ledger.due_nights(db, club_id):
                return
        with connect() as db:
            db.execute("BEGIN IMMEDIATE")
            ledger.finalize_due(db, club_id)
    except (sqlite3.Error, *ledger.aftener.NIGHT_ERRORS):
        logging.getLogger(__name__).exception("Kasse could not post due nights for club %s", club_id)


def log_unknown_entry(exc):
    """Log that the books were not added up because of an entry kind no file defines."""
    logging.getLogger(__name__).error("Kasse refused to add up the books: %s", exc)


def unknown_entry_response(exc):
    log_unknown_entry(exc)
    return HTMLResponse(f"<p>{escape(UNKNOWN_ENTRY)}</p>", status_code=500)


def _page(request, db, error=None, status_code=200, form=None):
    club_id = active_club_id()
    writer = is_kasserer(request, db)
    insider = writer or is_server_admin(request)  # sees the alerts
    member = linked_member(request)
    kasserer = ledger.kasserer(db, club_id)
    try:
        books, buckets = ledger.balances(db, club_id), ledger.buckets(db, club_id)
    except UnknownEntryType as exc:
        return unknown_entry_response(exc)
    own_id = member["id"] if member else None
    response = render(
        request, "kasse.html",
        books=books,
        own=ledger.member_row(books, own_id) if own_id else None,
        posted_nights=ledger.posted_nights(db, club_id),
        entries=ledger.entries(db, club_id),
        buckets=buckets,
        alerts=ledger.alerts(db, club_id) if insider else [],
        log=ledger.log_lines(db, club_id),  # every member reads the log
        entry_types=list(ENTRY_TYPES.values()), rule_titles=RULE_TITLES,
        kasserer=kasserer, is_kasserer=writer, insider=insider,
        own_id=own_id, reopen_hours=ledger.aftener.REOPEN_HOURS,
        active_members=db.execute("SELECT id,name FROM club_members WHERE club_id=? AND status='active' ORDER BY name",
                                  (club_id,)).fetchall(),
        today=ledger.today(), error=error, form=form or {},
        saved=request.query_params.get("saved") == "1", local_time=local_time,
    )
    response.status_code = status_code
    return response


@router.get("/kasse", response_class=HTMLResponse)
def kasse_page(request: Request):
    finalize_nights(active_club_id())
    with connect() as db:
        return _page(request, db)


def _write(request, action):
    """Run one kasserer write in its own transaction; a Danish ValueError re-renders the page."""
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        if not is_kasserer(request, db):
            db.rollback()
            raise HTTPException(403, "Kun klubbens kasserer kan skrive i klubkassen")
        try:
            action(db, active_club_id())
        except LookupError:
            db.rollback()
            raise HTTPException(404)
        except ValueError as exc:
            db.rollback()
            return str(exc)
    return None


def _optional_int(value):
    value = str(value or "").strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        raise ValueError("Ugyldigt valg")


@router.post("/kasse/entries", response_class=HTMLResponse)
async def add_entry(request: Request):
    form = await request.form()
    kind = str(form.get("kind", ""))

    def action(db, club_id):
        if kind not in ENTRY_TYPES:
            raise ValueError("Ukendt posteringstype")
        entry_type = ENTRY_TYPES[kind]
        # The form always sends both selects; only the one the type uses counts.
        ledger.add_entry(db, club_id, kind, parse_amount(form.get("amount"), signed=entry_type.signed),
                         member_id=None if entry_type.target == "bucket" else _optional_int(form.get("member_id")),
                         bucket_id=_optional_int(form.get("bucket_id")) if entry_type.target == "bucket" else None,
                         entry_date=form.get("entry_date"), note=form.get("note"))
    error = _write(request, action)
    if error:
        with connect() as db:
            return _page(request, db, error=error, status_code=400, form=dict(form))
    return RedirectResponse("/kasse?saved=1", status_code=303)


@router.post("/kasse/entries/{entry_id}/reverse", response_class=HTMLResponse)
def reverse_entry(request: Request, entry_id: int, note: str = Form("")):
    error = _write(request, lambda db, club_id: ledger.reverse_entry(db, club_id, entry_id, note))
    if error:
        with connect() as db:
            return _page(request, db, error=error, status_code=409)
    return RedirectResponse("/kasse?saved=1", status_code=303)


@router.post("/kasse/buckets", response_class=HTMLResponse)
def add_bucket(request: Request, name: str = Form(""), note: str = Form("")):
    error = _write(request, lambda db, club_id: ledger.add_bucket(db, club_id, name, note))
    if error:
        with connect() as db:
            return _page(request, db, error=error, status_code=400)
    return RedirectResponse("/kasse?saved=1", status_code=303)


@router.post("/kasse/buckets/{bucket_id}", response_class=HTMLResponse)
def update_bucket(request: Request, bucket_id: int, name: str = Form(""), note: str = Form("")):
    error = _write(request, lambda db, club_id: ledger.update_bucket(db, club_id, bucket_id, name, note))
    if error:
        with connect() as db:
            return _page(request, db, error=error, status_code=400)
    return RedirectResponse("/kasse?saved=1", status_code=303)


@router.post("/kasse/start", response_class=HTMLResponse)
def set_start(request: Request, kasse_start: str = Form(""), start_cash: str = Form("")):
    def action(db, club_id):
        # Startbeholdning may be 0 or negative (an overdraft); empty means 0.
        cash = parse_amount(start_cash, signed=True, allow_zero=True) if start_cash.strip() else Fraction(0)
        ledger.set_start(db, club_id, kasse_start, cash)
    error = _write(request, action)
    if error:
        with connect() as db:
            return _page(request, db, error=error, status_code=400)
    return RedirectResponse("/kasse?saved=1", status_code=303)


@router.post("/kasse/alerts/{alert_id}/seen")
def alert_seen(request: Request, alert_id: int):
    _write(request, lambda db, club_id: ledger.mark_seen(db, club_id, alert_id))
    return RedirectResponse("/kasse", status_code=303)


@router.get("/kasse/rules/{key}", response_class=HTMLResponse)
def show_kasse_rule(key: str):
    """The code behind one kind of entry, as Vis regler shows a bid's code."""
    if key not in RULE_TITLES:
        raise HTTPException(404)
    description = ENTRY_TYPES[key].description if key in ENTRY_TYPES else \
        "Hvad en afsluttet spilaften med klubkasse koster hvert medlem."
    sources = "".join(f"<h4>{escape(name)}</h4><pre><code>{escape(code)}</code></pre>"
                      for name, code in rule_sources(key))
    return HTMLResponse(
        '<section class="bid-rule-source">'
        f"<h3>{escape(RULE_TITLES[key])} — beregningsregler</h3>"
        f"<p>{escape(description)}</p>"
        '<p class="muted">Saldoen er summen af det, aftenerne koster, og hver posterings virkning. '
        "<code>kasse/ledger.py</code> lægger kun virkningerne sammen. Herunder koden.</p>"
        f"{sources}</section>"
    )


@router.post("/club/kasserer")
def assign_kasserer(request: Request, member_id: str = Form("")):
    """The club's administrators decide who is kasserer. This is not a club rule: no new version."""
    if not can_manage_club(request):
        raise HTTPException(403, "Kun klubadministratorer kan vælge kasserer")
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            ledger.set_kasserer(db, active_club_id(), _optional_int(member_id))
        except ValueError as exc:
            db.rollback()
            raise HTTPException(400, str(exc))
    return RedirectResponse("/club?saved=1", status_code=303)
