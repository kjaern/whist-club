"""Front page, statistics, game nights, hands, settlement and bid rules."""

import asyncio
import hashlib
import inspect
import json
import logging
import shutil
import sqlite3
from collections import Counter

from datetime import date
from fractions import Fraction
from html import escape
from io import BytesIO

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response, StreamingResponse
from whistapp.bids.registry import get_bid

from .imports import _photo_list, _session_source_view, PHOTO_ROOT, SOURCE_ROOT
from ..auth import current_account, is_server_admin
from ..club import active_club_id, club_players, club_settings
from ..buildinfo import build_stamp
from ..database import connect
from ..kasse import ledger as kasse
from ..kasse.base import UnknownEntryType
from .kasse import finalize_nights
from ..finance import export_workbook, export_year_workbook, settlement_entries
from ..rule_versions import is_club_night, latest_version, night_rules, stamp_night
from .rules import version_name
from ..rules import enabled_bid_keys, load_rules, rules_snapshot, special_outcome, target_for
from ..stats.base import cell as stat_cell
from ..stats.data import Scope, load
from ..stats.registry import rule_sources, statistic, topic_tables, topics_for
from ..stats.trends import periods as trend_periods, trend
from ..web import (
    REOPEN_HOURS,
    can_manage_club,
    can_reopen,
    fmt,
    is_final,
    linked_member,
    get_session,
    night_state_token,
    hand_round,
    night_time,
    render,
    require_open,
    session_bid_settings,
    session_players,
    session_rules,
    templates,
    view_data,
)

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def home(request: Request):
    if not is_server_admin(request):
        account = current_account(request)
        if account and account["must_change_password"]:
            return RedirectResponse("/account", status_code=303)
    finalize_nights(active_club_id())  # the kasse summary below shows balances
    if not is_server_admin(request):
        with connect() as db:
            sessions = db.execute("SELECT sessions.*, (SELECT COUNT(*) FROM players WHERE players.session_id=sessions.id) AS player_count FROM sessions WHERE club_id=? ORDER BY sessions.id DESC", (active_club_id(),)).fetchall()
        with connect() as db:
            roster = club_players(db, active_only=True)
            rules_now = _current_rules(db)
            summary = _kasse_summary(db, request)
        return render(request, "home.html", sessions=sessions,
                      default_title=date.today().isoformat(), club_players=roster,
                      member_view=not can_manage_club(request), current_rules=rules_now, kasse=summary)
    with connect() as db:
        sessions = db.execute(
            "SELECT sessions.*, (SELECT COUNT(*) FROM players WHERE players.session_id=sessions.id) AS player_count FROM sessions WHERE club_id=? ORDER BY sessions.id DESC",
            (active_club_id(),),
        ).fetchall()
        roster = club_players(db, active_only=True)
        rules_now = _current_rules(db)
        summary = _kasse_summary(db, request)
    return render(
        request,
        "home.html",
        sessions=sessions,
        default_title=date.today().isoformat(),
        club_players=roster,
        current_rules=rules_now,
        kasse=summary,
    )


def _kasse_summary(db, request):
    """The club's cash, the member's own Bogført, Afventer and I alt and, for the kasserer, unseen
    alerts; shown on Spilaftener."""
    member = linked_member(request)
    found = kasse.kasserer(db, active_club_id())
    try:
        books = kasse.balances(db, active_club_id())
    except UnknownEntryType as exc:
        logging.getLogger(__name__).error("Kasse refused to add up the books: %s", exc)
        return dict(error=True)
    row = kasse.member_row(books, member["id"]) if member else None
    return dict(cash=books["cash"],
                own=row["balance"] if row else (Fraction() if member else None),
                own_posted=row["posted"] if row else Fraction(), own_pending=row["pending"] if row else Fraction(),
                alerts=kasse.unseen_alerts(db, active_club_id()) if member and found and found["id"] == member["id"] else 0)


def _current_rules(db):
    """The club's newest version, for the new-night form. Only read: a version is made when
    rules are saved or a night is created, never by opening a page."""
    version = latest_version(db, active_club_id())
    return version and dict(number=version["number"], name=version_name(version))


@router.post("/sessions")
async def create_session(request: Request):
    form = await request.form()
    title = str(form.get("title") or date.today().isoformat())[:100]
    rotation_mode = str(form.get("rotation_mode", "consecutive"))
    if rotation_mode not in ("consecutive", "gap"):
        rotation_mode = "consecutive"

    with connect() as db:
        roster = dict(club_players(db, active_only=True))

    selected = [str(v) for v in form.getlist("club_players")]
    if len(set(selected)) != len(selected) or not set(selected) <= set(roster):
        raise HTTPException(400, "Invalid club players")

    guests = [n.strip() for n in str(form.get("guests", "")).splitlines() if n.strip()]
    try:
        seats = [(int(form[f"seat_{pid}"]), pid, roster[pid], pid) for pid in selected]
    except (ValueError, TypeError, KeyError):
        raise HTTPException(400, "Invalid seating order")
    if len({seat for seat, *_ in seats}) != len(seats):
        raise HTTPException(400, "Seat numbers must be unique")

    # Guests occupy seats after the selected club members at creation.
    seats.sort(key=lambda row: row[0])
    names = [(name, cid) for _, _, name, cid in seats] + [
        (name, None) for name in guests
    ]
    if not 4 <= len(names) <= 30 or len({name.casefold() for name, _ in names}) != len(
        names
    ):
        raise HTTPException(400, "Choose 4–30 distinct players")

    is_club = int(not guests)
    with connect() as db:
        sid = db.execute(
            """INSERT INTO sessions(title,rotation_mode,is_club,club_id,server_version,server_commit)
                VALUES (?,?,?,?,?,?)""",
            (title, rotation_mode, is_club, active_club_id(), *build_stamp()),
        ).lastrowid
        db.executemany(
            "INSERT INTO players(session_id,name,club_id,member_id,seat_order) VALUES (?,?,?,?,?)",
            [
                (sid, name[:80], None, int(cid) if cid else None, position)
                for position, (name, cid) in enumerate(names)
            ],
        )
        stamp_night(db, sid)
    return RedirectResponse(f"/sessions/{sid}", status_code=303)


@router.post("/sessions/{sid}/delete")
def delete_session(request: Request, sid: int):
    with connect() as db:
        if is_final(get_session(db, sid)):
            raise HTTPException(409, f"Spilaftenen blev afsluttet for mere end {REOPEN_HOURS} timer siden og kan ikke slettes")
        with kasse.watch(db, sid, "delete"):
            db.execute("DELETE FROM sessions WHERE id=?", (sid,))
    shutil.rmtree(PHOTO_ROOT / str(sid), ignore_errors=True)
    shutil.rmtree(SOURCE_ROOT / str(sid), ignore_errors=True)
    return RedirectResponse("/", status_code=303)


def _statistics_page(request, db, scope, topic, **context):
    """One page for every scope: topic tabs over the registered statistics.

    A page topic (Hall of Fame) builds its own content from the same data instead of tables.
    """
    data = load(db, scope)
    topics = topics_for(scope.kind)
    current = next((t for t in topics if t.key == topic), topics[0])
    if current.build:
        return render(request, "statistics.html", scope=scope, topics=topics, topic=current,
                      topic_data=current.build(data), tables=[], trends={}, origin=scope.origin,
                      stat_cell=stat_cell, **context)
    tables = topic_tables(current, data)
    sub_periods = trend_periods(data)
    trends = {stat.key: trend(stat, table, data, sub_periods) for stat, table in tables}
    return render(request, "statistics.html", scope=scope, topics=topics, topic=current,
                  tables=tables, trends=trends, origin=scope.origin, stat_cell=stat_cell,
                  **context)


@router.get("/statistics", response_class=HTMLResponse)
def statistics(request: Request, origin: str = "all", topic: str = "", fra: str = "", til: str = ""):
    if origin not in ("all", "live", "imported"):
        raise HTTPException(400, "Invalid origin")
    if fra or til:
        return _statistics_period(request, origin, topic, fra, til)
    with connect() as db:
        nights = load(db, Scope(origin=origin)).nights_with_members()
        years = Counter(n.year for n in nights)
        return _statistics_page(request, db, Scope(origin=origin), topic,
                                years=sorted(years.items(), reverse=True))


def _statistics_period(request, origin, topic, fra, til):
    """Statistics for the nights between two dates, both included; either end may be open."""
    try:
        start, end = (date.fromisoformat(v).isoformat() if v else None for v in (fra, til))
    except ValueError:
        raise HTTPException(400, "Ugyldig dato")
    if start and end and start > end:
        start, end = end, start
    scope = Scope("period", origin=origin, date_from=start, date_to=end)
    with connect() as db:
        data = load(db, scope)
        hands = Counter(h.session_id for h in data.hands)
        nights = [dict(id=n.id, title=n.title, date=n.date, hand_count=hands[n.id])
                  for n in sorted(data.nights_with_members(), key=lambda n: (n.date, n.id), reverse=True)]
        return _statistics_page(request, db, scope, topic, nights=nights)


def _check_year(year, origin):
    if year < 1900 or year > 9999:
        raise HTTPException(404)
    if origin not in ("all", "live", "imported"):
        raise HTTPException(400, "Invalid origin")


def _year_nights(db, year, origin):
    """The active club's nights with results for club members in the year, newest first."""
    return db.execute(
            """
            SELECT s.id, s.title, s.created_at, s.origin, COUNT(DISTINCT h.id) AS hand_count
            FROM sessions s
            JOIN players p ON p.session_id=s.id
            LEFT JOIN hands h ON h.session_id=s.id
            LEFT JOIN hand_participants hp ON hp.hand_id=h.id AND hp.player_id=p.id
            WHERE s.is_club=1 AND p.member_id IS NOT NULL AND s.club_id=?
              AND (hp.player_id IS NOT NULL OR EXISTS
                  (SELECT 1 FROM historical_final_scores fs WHERE fs.session_id=s.id AND fs.player_id=p.id))
              AND substr(s.created_at,1,4)=?
              AND (?='all' OR s.origin=?)
            GROUP BY s.id
            ORDER BY s.created_at DESC, s.id DESC
        """,
            (active_club_id(), str(year), origin, origin),
        ).fetchall()


@router.get("/statistics/{year}", response_class=HTMLResponse)
def statistics_year(request: Request, year: int, origin: str = "all", topic: str = ""):
    _check_year(year, origin)
    with connect() as db:
        nights = [dict(n, date=night_time(n)[:10]) for n in _year_nights(db, year, origin)]
        return _statistics_page(request, db, Scope("year", year=year, origin=origin), topic,
                                nights=nights)


@router.get("/sessions/{sid}/statistics", response_class=HTMLResponse)
def statistics_night(request: Request, sid: int, topic: str = ""):
    with connect() as db:
        session = get_session(db, sid)
        return _statistics_page(request, db, Scope("night", session_id=sid), topic,
                                session=session, night_date=night_time(session)[:10])


@router.get("/statistics/rules/{key}", response_class=HTMLResponse)
def show_statistic_rule(key: str):
    """The code that computes one statistic, as Vis regler shows a bid's code."""
    stat = statistic(key)
    if stat is None:
        raise HTTPException(404)
    sources = "".join(
        f"<h4>{escape(name)}</h4><pre><code>{escape(code)}</code></pre>" for name, code in rule_sources(stat)
    )
    return HTMLResponse(
        '<section class="bid-rule-source">'
        f"<h3>{escape(stat.title)} — beregningsregler</h3>"
        f"<p>{escape(stat.description)}</p>"
        "<p class=\"muted\">Statistikken bygger på klubaftenerne i perioden, indlæst én gang af "
        "<code>stats/data.py</code>; den læser ikke selv databasen. Herunder koden, der beregner den.</p>"
        f"{sources}"
        "</section>"
    )


@router.get("/statistics/{year}/export.xlsx")
def export_year(year: int, origin: str = "all"):
    _check_year(year, origin)
    with connect() as db:
        # Oldest night first, like a paper score book.
        nights = [
            (night_time(n)[:10], view_data(db, n["id"]))
            for n in reversed(_year_nights(db, year, origin))
        ]
        try:
            payload = export_year_workbook(db, year, nights)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
    return StreamingResponse(
        BytesIO(payload),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="whist-{year}.xlsx"'},
    )


@router.get("/sessions/{sid}", response_class=HTMLResponse)
def session_page(request: Request, sid: int):
    with connect() as db:
        data = view_data(db, sid)
        data["photos"] = _photo_list(db, sid)
        data["source_files"], data["source_previews"] = _session_source_view(db, sid)
        data["audit_events"] = [dict(row) for row in db.execute(
            """SELECT created_at,actor_username,entity,action,hand_id,before_json,after_json
               FROM audit_events WHERE session_id=? ORDER BY id DESC LIMIT 300""", (sid,)
        )]
        data["change_logs"] = [
            dict(row)
            for row in db.execute(
                "SELECT id,user_id,created_at,description FROM evening_log WHERE evening_id=? ORDER BY id DESC",
                (sid,),
            )
        ]
    return render(request, "session.html", **data)


def _night_summary(entries, parsed):
    """A change to the night itself: closing, reopening and what its settlement charged."""
    ended = [(parsed(e["before_json"]) or {}, parsed(e["after_json"]) or {}) for e in entries
             if e["entity"] == "sessions" and e["action"] == "update"]
    settled = any(e["entity"] == "club_member_settlements" for e in entries)
    if any(b.get("ended_at") and not a.get("ended_at") for b, a in ended):
        return "genåbnede spilaftenen"
    if any(a.get("ended_at") and not b.get("ended_at") for b, a in ended):
        return "afsluttede spilaftenen" + (" og opdaterede afregningen" if settled else "")
    return "ændrede afregningen" if settled else "ændrede spilaftenen"


def _history_operations(db, sid):
    """Render one human-readable entry per changed hand, never per SQL row."""
    events = [dict(row) for row in db.execute(
        """SELECT id,created_at,actor_username,entity,action,hand_id,before_json,after_json,operation_id
           FROM audit_events WHERE session_id=? ORDER BY id DESC LIMIT 2000""", (sid,))]
    # A batch may share a request operation ID. A hand is still its own operation.
    groups = {}
    for event in events:
        key = (event["operation_id"], event["hand_id"]) if event["operation_id"] else ("legacy", event["id"])
        if key not in groups:
            groups[key] = {"created_at": event["created_at"],
                           "actor_username": event["actor_username"], "events": [],
                           "hand_id": event["hand_id"]}
        groups[key]["events"].append(event)

    def parsed(value):
        if not value:
            return None
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return None

    def bid(data):
        if not data:
            return None
        label = data.get("bid_label") or data.get("bid_key")
        if not label:
            return None
        target = data.get("target")
        return f"{target} {label}" if target is not None and target > 0 else str(label)

    result = []
    for group in groups.values():
        entries = group["events"]
        hand_entries = [e for e in entries if e["entity"] == "hands"]
        # Trigger inserts and subsequent updates are part of the same add operation.
        added = any(e["action"] == "insert" for e in hand_entries)
        deleted = any(e["action"] == "delete" for e in hand_entries)
        first = next((parsed(e["after_json"]) for e in reversed(hand_entries)
                      if parsed(e["after_json"])), None)
        last = next((parsed(e["before_json"]) for e in hand_entries
                     if parsed(e["before_json"])), None)
        hand_id = group["hand_id"]
        prefix = f"hånd {hand_id}" if hand_id is not None else "spilaftenen"
        if added:
            summary = f"tilføjede {prefix}" + (f": {bid(first)}" if bid(first) else "")
        elif deleted:
            summary = f"slettede {prefix}" + (f": {bid(last)}" if bid(last) else "")
        elif hand_id is not None:
            before_bid, after_bid = bid(last), bid(first)
            if before_bid and after_bid and before_bid != after_bid:
                summary = f"rettede {prefix}: {before_bid} → {after_bid}"
            else:
                summary = f"rettede {prefix}"
                if last and first and last.get("actual") != first.get("actual"):
                    summary += f": {last.get('actual')} → {first.get('actual')} stik"
        else:
            summary = _night_summary(entries, parsed)
        result.append({"actor_username": group["actor_username"] or "Ukendt bruger",
                       "created_at": group["created_at"], "summary": summary})
    return result


@router.get("/sessions/{sid}/history", response_class=HTMLResponse)
def session_history(request: Request, sid: int):
    with connect() as db:
        session = db.execute("SELECT id,title,ended_at FROM sessions WHERE id=?", (sid,)).fetchone()
        if session is None:
            raise HTTPException(404, "Spilaften findes ikke")
        operations = _history_operations(db, sid)
    return render(request, "session_history.html", session=session, operations=operations)


LIVE_VERSION_MARK = "__live_version__"


def _live_scoreboard(request, data):
    """The live scoreboard and a fingerprint of it. The page polls with the fingerprint it
    shows, and an unchanged scoreboard is answered with 204, so the page is not redrawn
    (and does not jump) when nothing has happened."""
    html = templates.get_template("partials/live_scoreboard.html").render(
        request=request, live_version=LIVE_VERSION_MARK, **data)
    version = hashlib.sha256(html.encode()).hexdigest()[:16]
    return html.replace(LIVE_VERSION_MARK, version), version


@router.get("/sessions/{sid}/live", response_class=HTMLResponse)
def live_page(request: Request, sid: int):
    with connect() as db:
        data = view_data(db, sid)
    return render(request, "live.html", live_version=_live_scoreboard(request, data)[1], **data)


@router.get("/sessions/{sid}/live/scoreboard", response_class=HTMLResponse)
def live_scoreboard(request: Request, sid: int, v: str = ""):
    with connect() as db:
        data = view_data(db, sid)
    html, version = _live_scoreboard(request, data)
    if v == version:
        return Response(status_code=204)
    return HTMLResponse(html)


def _results_signature(sid: int) -> str:
    # Database-backed detection works even after an application restart.
    with connect() as db:
        get_session(db, sid)
        parts = [
            tuple(row)
            for row in db.execute(
                "SELECT h.id,h.bid_key,h.target,h.actual,h.won,h.partner_id FROM hands h WHERE h.session_id=? ORDER BY h.id",
                (sid,),
            )
        ]
        scores = [
            tuple(row)
            for row in db.execute(
                "SELECT hs.hand_id,hs.player_id,hs.numerator,hs.denominator FROM hand_scores hs JOIN hands h ON h.id=hs.hand_id WHERE h.session_id=? ORDER BY hs.hand_id,hs.player_id",
                (sid,),
            )
        ]
        state = db.execute(
            "SELECT ended_at FROM sessions WHERE id=?", (sid,)
        ).fetchone()[0]
    return hashlib.sha256(repr((parts, scores, state)).encode()).hexdigest()


@router.get("/sessions/{sid}/editor/new-hand", response_class=HTMLResponse)
def editor_new_hand(request: Request, sid: int):
    with connect() as db:
        data = view_data(db, sid)
    return render(request, "partials/new_hand_area.html", **data)


@router.get("/sessions/{sid}/editor/results", response_class=HTMLResponse)
def editor_results(request: Request, sid: int):
    with connect() as db:
        data = view_data(db, sid)
    return render(request, "partials/editor_results.html", **data)


@router.get("/sessions/{sid}/editor/events")
async def editor_events(request: Request, sid: int, hand: str = ""):
    """`hand` is the last hand the page's form was built after. A phone that slept
    reconnects with it, so hands saved in the meantime are reported at once."""
    initial = await asyncio.to_thread(_results_signature, sid)

    def latest_hand():
        with connect() as db:
            row = db.execute(
                "SELECT id,origin_client_id FROM hands WHERE session_id=? ORDER BY id DESC LIMIT 1",
                (sid,),
            ).fetchone()
            return (row["id"], row["origin_client_id"]) if row else (0, None)

    initial_hand = await asyncio.to_thread(latest_hand)

    async def events():
        previous = initial
        previous_hand = initial_hand
        yield ": connected\n\n"
        if hand.isdigit() and int(hand) != previous_hand[0]:
            # The page's form is stale whoever saved the hand, so no origin is sent.
            payload = json.dumps({"new_hand": True, "origin": None})
            yield f"event: results-changed\ndata: {payload}\n\n"
        while not await request.is_disconnected():
            await asyncio.sleep(1.5)
            current = await asyncio.to_thread(_results_signature, sid)
            if current != previous:
                previous = current
                new_hand = await asyncio.to_thread(latest_hand)
                is_new = new_hand[0] > previous_hand[0]
                previous_hand = new_hand
                payload = json.dumps(
                    {"new_hand": is_new, "origin": new_hand[1] if is_new else None}
                )
                yield f"event: results-changed\ndata: {payload}\n\n"
            else:
                yield ": heartbeat\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


@router.get("/sessions/{sid}/bid-fields", response_class=HTMLResponse)
def bid_fields(request: Request, sid: int, bid_key: str):
    with connect() as db:
        bid = session_rules(db, sid).get(bid_key)
        if not bid:
            raise HTTPException(400, "Unknown bid")
        active = session_players(db, sid, active_only=True)
    return render(request, "partials/target.html", bid=bid, active=active)


@router.post("/sessions/{sid}/players", response_class=HTMLResponse)
def add_player(
    request: Request, sid: int, name: str = Form(...), before_player: int = Form(0)
):
    name = name.strip()[:80]

    with connect() as db:
        require_open(db, sid)
        if (
            not name
            or db.execute(
                "SELECT 1 FROM players WHERE session_id=? AND lower(name)=lower(?)",
                (sid, name),
            ).fetchone()
        ):
            raise HTTPException(400, "Name missing or already exists")

        players = db.execute(
            "SELECT id,seat_order FROM players WHERE session_id=? ORDER BY seat_order,id",
            (sid,),
        ).fetchall()
        ids = [p["id"] for p in players]
        if before_player and before_player not in ids:
            raise HTTPException(400, "Invalid seat")

        pos = ids.index(before_player) if before_player else len(ids)
        # Reassign every seat in one transaction to keep a stable physical order.
        db.execute(
            "UPDATE players SET seat_order=seat_order+1000 WHERE session_id=?", (sid,)
        )
        for i, pid in enumerate(ids):
            db.execute(
                "UPDATE players SET seat_order=? WHERE id=?",
                (i if i < pos else i + 1, pid),
            )
        db.execute(
            "INSERT INTO players(session_id,name,seat_order) VALUES (?,?,?)",
            (sid, name, pos),
        )
        db.execute("UPDATE sessions SET is_club=0 WHERE id=?", (sid,))
        data = view_data(db, sid)
    return render(request, "partials/session_content.html", **data)


@router.post("/sessions/{sid}/players/{pid}/toggle", response_class=HTMLResponse)
def toggle_player(request: Request, sid: int, pid: int):
    with connect() as db:
        require_open(db, sid)
        p = db.execute(
            "SELECT * FROM players WHERE id=? AND session_id=?", (pid, sid)
        ).fetchone()
        if not p:
            raise HTTPException(404)
        db.execute(
            "UPDATE players SET active=? WHERE id=?", (0 if p["active"] else 1, pid)
        )
        data = view_data(db, sid)
    return render(request, "partials/session_content.html", **data)


@router.post("/sessions/{sid}/rotation", response_class=HTMLResponse)
def change_rotation(request: Request, sid: int, rotation_mode: str = Form(...)):
    if rotation_mode not in ("consecutive", "gap"):
        raise HTTPException(400, "Invalid rotation")
    with connect() as db:
        require_open(db, sid)
        db.execute(
            "UPDATE sessions SET rotation_mode=? WHERE id=?", (rotation_mode, sid)
        )
        data = view_data(db, sid)
    return render(request, "partials/session_content.html", **data)


@router.post("/sessions/{sid}/hands", response_class=HTMLResponse)
async def create_hand(request: Request, sid: int):
    """Atomic hand + result submission. Canceling a draft never creates a row."""
    form = await request.form()
    key = str(form.get("bid_key", ""))
    # The night's snapshot decides which bids may be played and at which values.
    with connect() as db:
        session = get_session(db, sid)
    bid = load_rules(session_bid_settings(db, session))["bids"].get(key)
    if not bid or key not in enabled_bid_keys(session_bid_settings(db, session)):
        raise HTTPException(400, "Unknown bid")

    try:
        target = target_for(bid, form.get("target"))
        declarer = int(form["declarer"])
        active = [int(v) for v in form.getlist("participants")]
        if len(active) != 4 or len(set(active)) != 4 or declarer not in active:
            raise ValueError("Select four players")

        partner = None
        actual = None
        won = None
        contract_points = None

        if bid["target"] == "variable":
            actual = int(form["actual"])
            partner = int(form["partner"])
            if partner not in active:
                raise ValueError("Partner not active")
            result = get_bid(key).score(
                bid,
                active=active,
                declarer=declarer,
                target=target,
                actual=actual,
                partner=partner,
                **({"level": int(key[-1])} if key.startswith("vip") else {}),
            )
            won, contract_points, scores = (
                result.won,
                result.contract_points,
                result.scores,
            )
            specials = []
        else:
            if bid["target"] == "fixed":
                going = [int(v) for v in form.getlist("going")]
                bidders = list(dict.fromkeys([declarer] + going))
            else:
                bidders = active
            if not set(bidders) <= set(active):
                raise ValueError("Invalid bidder")
            specials = []
            outcomes = {}
            actuals = {}
            for pid in bidders:
                n = int(form[f"actual_{pid}"])
                if not 0 <= n <= 13:
                    raise ValueError("Invalid tricks")
                actuals[pid] = n
                if bid["target"] == "fixed":
                    result, stake = special_outcome(bid, n)
                    outcomes[pid] = (result, stake)
                    specials.append((pid, n, int(result)))
                else:
                    specials.append((pid, n, None))
            scores = (
                get_bid(key)
                .score(bid, active=active, declarer=declarer, actuals=actuals)
                .scores
            )
    except (ValueError, TypeError, KeyError, IndexError) as exc:
        raise HTTPException(400, f"Invalid hand or result: {exc}")

    with connect() as db:
        # Take the write lock before checking, so two devices cannot both pass the check.
        db.execute("BEGIN IMMEDIATE")
        require_open(db, sid)
        # A form built before another device saved a hand is stale: it shows the old
        # declarer and rotation. Refuse it and send back a fresh form instead.
        base = str(form.get("base_hand_id", "")).strip()
        if base:
            latest = db.execute(
                "SELECT COALESCE(MAX(id),0) FROM hands WHERE session_id=?", (sid,)
            ).fetchone()[0]
            if not base.isdigit() or int(base) != latest:
                data = view_data(db, sid)
                return render(request, "partials/session_content.html", hand_conflict=True, **data)
        allowed = {
            r[0]
            for r in db.execute(
                "SELECT id FROM players WHERE session_id=? AND active=1", (sid,)
            )
        }
        if not set(active) <= allowed:
            raise HTTPException(400, "Player not active in this session")

        dealer, round_no, round_end = hand_round(db, sid, active)
        hid = db.execute(
            "INSERT INTO hands(session_id,bid_key,bid_label,target,actual,won,contract_points,rules_snapshot,partner_id,dealer_id,round_no,round_end) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                sid,
                key,
                bid["label"],
                target,
                actual,
                int(won) if won is not None else None,
                float(contract_points) if contract_points is not None else None,
                rules_snapshot(bid),
                partner,
                dealer,
                round_no,
                round_end,
            ),
        ).lastrowid

        origin = str(form.get("origin_client_id", ""))[:80]
        db.execute(
            "UPDATE hands SET origin_client_id=? WHERE id=?", (origin or None, hid)
        )
        db.execute(
            "INSERT INTO hand_declarers(hand_id,player_id) VALUES (?,?)",
            (hid, declarer),
        )
        db.executemany(
            "INSERT INTO hand_participants(hand_id,player_id) VALUES (?,?)",
            [(hid, p) for p in active],
        )
        db.executemany(
            "INSERT INTO special_bids(hand_id,player_id,actual,won) VALUES (?,?,?,?)",
            [(hid, *item) for item in specials],
        )
        db.executemany(
            "INSERT INTO hand_scores(hand_id,player_id,numerator,denominator) VALUES (?,?,?,?)",
            [
                (hid, p, Fraction(str(v)).numerator, Fraction(str(v)).denominator)
                for p, v in scores.items()
            ],
        )
        data = view_data(db, sid)

    return render(request, "partials/session_content.html", **data)


def hand_result(db, session, hand, get, getlist):
    """Score a correction of a saved hand. `get`/`getlist` read the submitted fields.

    Shared by the live night's correction form and by editing a closed night, so both
    score a hand the same way. Raises ValueError (and friends) on an invalid hand.
    """
    hid = hand["id"]
    rules = load_rules(session_bid_settings(db, session))["bids"]
    participants = [r[0] for r in db.execute(
        "SELECT player_id FROM hand_participants WHERE hand_id=?", (hid,))]
    # An imported hand may have no declarer recorded.
    row = db.execute("SELECT player_id FROM hand_declarers WHERE hand_id=?", (hid,)).fetchone()
    original_declarer = row[0] if row else None
    key = str(get("bid_key") or hand["bid_key"])
    # A correction may keep the hand's bid even if the club has since disabled it.
    if key != hand["bid_key"] and key not in enabled_bid_keys(session_bid_settings(db, session)):
        raise ValueError("Meldingen er ikke slået til i klubben")
    if key not in rules:
        raise ValueError("Ukendt melding")
    bid = rules[key]
    if not (get("declarer") or original_declarer):
        raise ValueError("Vælg en melder")
    declarer = int(get("declarer") or original_declarer)
    if declarer not in participants:
        raise ValueError("Melder skal deltage i hånden")
    target = target_for(bid, get("target") if bid["target"] == "variable" else None)
    if bid["target"] == "variable":
        actual = int(get("actual"))
        partner = int(get("partner"))
        if partner not in participants or not 0 <= actual <= 13:
            raise ValueError("Ugyldig makker eller stik")
        result = get_bid(key).score(
            bid, active=participants, declarer=declarer, target=target,
            actual=actual, partner=partner,
            **({"level": int(key[-1])} if key.startswith("vip") else {}),
        )
        won, value, scores = result.won, result.contract_points, result.scores
        specials = []
    else:
        partner = None
        actual = None
        won = value = None
        previous = {r["player_id"]: r["actual"] for r in db.execute(
            "SELECT player_id,actual FROM special_bids WHERE hand_id=?", (hid,))}
        if bid["target"] == "fixed":
            bidders = [int(v) for v in getlist("going")]
            bidders = list(dict.fromkeys([declarer] + bidders))
        else:
            bidders = participants
        if not set(bidders) <= set(participants):
            raise ValueError("Ugyldige meldere")
        actuals = {}
        specials = []
        for pid in bidders:
            n = int(get(f"actual_{pid}", previous.get(pid) if previous.get(pid) is not None else 0))
            if not 0 <= n <= 13:
                raise ValueError("Ugyldige stik")
            actuals[pid] = n
            outcome = special_outcome(bid, n)[0] if bid["target"] == "fixed" else None
            specials.append((pid, n, int(outcome) if outcome is not None else None))
        scores = get_bid(key).score(
            bid, active=participants, declarer=declarer, actuals=actuals).scores
    return dict(key=key, bid=bid, target=target, actual=actual, won=won, value=value,
                partner=partner, declarer=declarer, specials=specials, scores=scores)


def write_hand(db, hid, r):
    """Store a scored hand from hand_result()."""
    db.execute("""UPDATE hands SET bid_key=?,bid_label=?,target=?,actual=?,won=?,
                  contract_points=?,rules_snapshot=?,partner_id=? WHERE id=?""",
               (r["key"], r["bid"]["label"], r["target"], r["actual"],
                int(r["won"]) if r["won"] is not None else None,
                float(r["value"]) if r["value"] is not None else None,
                rules_snapshot(r["bid"]), r["partner"], hid))
    db.execute("DELETE FROM hand_declarers WHERE hand_id=?", (hid,))
    db.execute("INSERT INTO hand_declarers(hand_id,player_id) VALUES (?,?)", (hid, r["declarer"]))
    db.execute("DELETE FROM special_bids WHERE hand_id=?", (hid,))
    db.executemany("INSERT INTO special_bids(hand_id,player_id,actual,won) VALUES (?,?,?,?)",
                   [(hid, *item) for item in r["specials"]])
    db.execute("DELETE FROM hand_scores WHERE hand_id=?", (hid,))
    db.executemany("INSERT INTO hand_scores(hand_id,player_id,numerator,denominator) VALUES (?,?,?,?)",
                   [(hid, pid, Fraction(str(v)).numerator, Fraction(str(v)).denominator)
                    for pid, v in r["scores"].items()])


@router.post("/sessions/{sid}/hands/{hid}/result", response_class=HTMLResponse)
async def set_result(request: Request, sid: int, hid: int):
    form = await request.form()
    with connect() as db:
        session = require_open(db, sid)
        hand = db.execute("SELECT * FROM hands WHERE id=? AND session_id=?", (hid, sid)).fetchone()
        if not hand:
            raise HTTPException(404)
        try:
            result = hand_result(db, session, hand, form.get, form.getlist)
        except (ValueError, TypeError, KeyError, IndexError) as exc:
            raise HTTPException(400, f"Ugyldig hånd: {exc}")
        write_hand(db, hid, result)
        data = view_data(db, sid)
    return render(request, "partials/session_content.html", **data)


@router.post("/sessions/{sid}/hands/{hid}/delete", response_class=HTMLResponse)
def delete_hand(request: Request, sid: int, hid: int):
    with connect() as db:
        require_open(db, sid)
        db.execute("DELETE FROM hands WHERE id=? AND session_id=?", (hid, sid))
        data = view_data(db, sid)
    return render(request, "partials/session_content.html", **data)


@router.post("/sessions/{sid}/penalties", response_class=HTMLResponse)
def add_penalty(request: Request, sid: int, player_id: int = Form(...)):
    with connect() as db:
        require_open(db, sid)
        if not db.execute(
            "SELECT 1 FROM players WHERE id=? AND session_id=?", (player_id, sid)
        ).fetchone():
            raise HTTPException(400, "Invalid player")
        db.execute(
            "INSERT INTO penalty_payments(session_id,player_id,amount) VALUES (?,?,?)",
            (sid, player_id, night_rules(db, sid)["fejl40"]),
        )
        data = view_data(db, sid)
    return render(request, "partials/session_content.html", **data)


@router.post("/sessions/{sid}/penalties/{penalty_id}/delete", response_class=HTMLResponse)
def remove_penalty(request: Request, sid: int, penalty_id: int):
    with connect() as db:
        require_open(db, sid)
        db.execute(
            "DELETE FROM penalty_payments WHERE id=? AND session_id=?",
            (penalty_id, sid),
        )
        data = view_data(db, sid)
    return render(request, "partials/session_content.html", **data)


def close_error(request: Request, sid: int, message: str):
    # Render the failure in the POST response instead of relying on a redirect.
    # The failed transaction has already been rolled back by connect()'s context.
    with connect() as db:
        data = view_data(db, sid)
    return templates.TemplateResponse(
        request=request,
        name="session.html",
        context={**data, "close_error": message},
        status_code=409,
    )


STALE_NIGHT = ("Spilaftenen er blevet ændret på en anden enhed, siden siden blev vist. "
               "Se den nye stilling, og prøv igen.")


def _stale(db, sid, form_state):
    """True if the page's form was built before another device changed the night."""
    return bool(form_state) and form_state != night_state_token(db, sid)


def _settlement(db, sid):
    return {r[0]: Fraction(r[1], r[2]) for r in db.execute(
        "SELECT member_id,amount_numerator,amount_denominator FROM club_member_settlements WHERE session_id=?", (sid,))}


def _settlement_change(db, before, after):
    """Per member, what the night charged before and after, for the evening log."""
    changed = sorted(m for m in set(before) | set(after) if before.get(m, 0) != after.get(m, 0))
    if not changed:
        return "afregningen er uændret" if before else ""
    names = {r[0]: r[1] for r in db.execute(
        "SELECT id,name FROM club_members WHERE id IN (%s)" % ",".join("?" * len(changed)), changed)}
    return "afregning ændret: " + ", ".join(
        f"{names.get(m, f'#{m}')} {fmt(before.get(m, Fraction()))} → {fmt(after.get(m, Fraction()))} kr" for m in changed)


def settle_night(db, sid, collect):
    """Replace a night's settlement with one worked out from its hands and rule version.

    The new settlement is computed before the old one is removed, all in the caller's
    transaction, so a failure leaves the previous settlement in place. Returns what
    changed per member, for the evening log.
    """
    entries = []
    if collect:
        night = night_rules(db, sid)
        entries = settlement_entries(db, sid, night["money_mode"], night["placements"], night["point_rate"])
    before = _settlement(db, sid)
    db.execute("DELETE FROM club_member_settlements WHERE session_id=?", (sid,))
    db.execute("DELETE FROM session_settlements WHERE session_id=?", (sid,))
    db.executemany(
        """INSERT INTO club_member_settlements
        (session_id,member_id,place,amount_numerator,amount_denominator)
        VALUES (?,?,?,?,?)""",
        [(sid, *entry) for entry in entries],
    )
    if len(_settlement(db, sid)) != len(entries):
        raise ValueError("Incomplete settlement")
    return _settlement_change(db, before, _settlement(db, sid))


@router.post("/sessions/{sid}/reopen")
def reopen_night(request: Request, sid: int, night_state: str = Form("")):
    """Reopen a night closed less than REOPEN_HOURS ago. Its settlement stays until it is closed again."""
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")  # two devices cannot both pass the checks below
        session = get_session(db, sid)
        if session["ended_at"] is None:
            db.rollback()
            return RedirectResponse(f"/sessions/{sid}", status_code=303)
        if not can_reopen(session):
            db.rollback()
            return close_error(request, sid, f"Spilaftenen kan kun genåbnes i {REOPEN_HOURS} timer efter, "
                                             "den blev afsluttet. Nu er den endelig.")
        if _stale(db, sid, night_state):
            db.rollback()
            return close_error(request, sid, STALE_NIGHT)
        # The kasserer is told; what the night charges afterwards is filled in when it is closed again.
        before = kasse.snapshot(db, sid)
        db.execute("UPDATE sessions SET ended_at=NULL WHERE id=?", (sid,))
        if before:
            kasse.add_alert(db, sid, "reopen", before, None)
        db.execute(
            "INSERT INTO evening_log(evening_id,user_id,description) VALUES (?,?,?)",
            (sid, request.session.get("user", "admin"),
             "Genåbnet; afregningen gælder, indtil aftenen afsluttes igen"),
        )
    return RedirectResponse(f"/sessions/{sid}", status_code=303)


@router.post("/sessions/{sid}/end")
def end_night(request: Request, sid: int, collect_club_money: bool = Form(False), night_state: str = Form("")):
    try:
        with connect() as db:
            db.execute("BEGIN IMMEDIATE")  # two devices cannot both pass the checks below
            if get_session(db, sid)["ended_at"] is not None or _stale(db, sid, night_state):
                db.rollback()
                return close_error(request, sid, STALE_NIGHT)
            if not db.execute(
                "SELECT 1 FROM hands WHERE session_id=?", (sid,)
            ).fetchone():
                db.rollback()
                return close_error(
                    request, sid, "Registrer mindst én hånd, før du afslutter."
                )

            # Recheck identity; a guest, or a member not on the night's medlemsliste,
            # makes this a non-club night.
            is_club = is_club_night(db, sid)
            collect = bool(is_club and collect_club_money)

            change = settle_night(db, sid, collect)
            db.execute(
                "UPDATE sessions SET is_club=?,collect_club_money=?,ended_at=CURRENT_TIMESTAMP WHERE id=?",
                (int(is_club), int(collect), sid),
            )
            kasse.complete_reopen_alert(db, sid)
            if change:
                db.execute(
                    "INSERT INTO evening_log(evening_id,user_id,description) VALUES (?,?,?)",
                    (sid, request.session.get("user", "admin"), f"Afsluttet; {change}"),
                )
    except (ValueError, TypeError, KeyError) as exc:
        logging.getLogger(__name__).warning("Cannot close session %s: %s", sid, exc)
        return close_error(request, sid, f"Spilaftenen blev ikke afsluttet: {exc}")
    except sqlite3.Error:
        logging.getLogger(__name__).exception("Failed to close session %s", sid)
        return close_error(
            request,
            sid,
            "Spilaftenen blev ikke afsluttet: resultatet kunne ikke gemmes. Se serverloggen.",
        )
    return RedirectResponse(f"/sessions/{sid}", status_code=303)


@router.get("/sessions/{sid}/export.xlsx")
def export_session(sid: int):
    with connect() as db:
        data = view_data(db, sid)
        try:
            payload = export_workbook(db, data)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
    return StreamingResponse(
        BytesIO(payload),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="whist-session-{sid}.xlsx"'
        },
    )


@router.post("/sessions/{sid}/seats", response_class=HTMLResponse)
async def reorder_seats(request: Request, sid: int):
    form = await request.form()
    try:
        ids = [int(v) for v in form.getlist("order")]
    except ValueError:
        raise HTTPException(400, "Invalid order")
    with connect() as db:
        require_open(db, sid)
        actual = [
            r[0]
            for r in db.execute("SELECT id FROM players WHERE session_id=?", (sid,))
        ]
        if len(ids) != len(actual) or set(ids) != set(actual):
            raise HTTPException(400, "Missing or duplicate player")
        db.executemany(
            "UPDATE players SET seat_order=? WHERE id=?",
            [(i, pid) for i, pid in enumerate(ids)],
        )
        data = view_data(db, sid)
    return render(request, "partials/session_content.html", **data)


@router.get("/bids/{key}/rules", response_class=HTMLResponse)
def show_bid_rules(request: Request, key: str):
    try:
        bid = get_bid(key)
    except KeyError:
        raise HTTPException(404)

    cls = type(bid)
    code = inspect.getsource(cls)

    # The source is the implementation; the club's values are what it is fed with.
    with connect() as db:
        settings = club_settings(db)
    chosen = json.loads(settings["bid_settings"] or "{}").get(bid.key, {}) if settings else {}
    values = bid.option_values(chosen.get("options"))
    rows = "".join(
        f"<tr><th>{escape(label)}</th><td><code>{escape(name)}</code></td>"
        f"<td>{escape(fmt(values[name]))}</td><td>{escape(fmt(Fraction(str(default))))}</td></tr>"
        for name, (label, default) in bid.options.items()
    )
    club_values = (
        '<h4>Klubbens værdier</h4>'
        '<table class="bid-rule-values"><tr><th>Værdi</th><th>Navn i koden</th><th>Klubben</th><th>Standard</th></tr>'
        f"{rows}</table>"
        if rows else ""
    )

    return HTMLResponse(
        '<section class="bid-rule-source">'
        f"<h3>{escape(cls.__name__)} — beregningsregler</h3>"
        f"{club_values}"
        f"<pre><code>{escape(code)}</code></pre>"
        "</section>"
    )
