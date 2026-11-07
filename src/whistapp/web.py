"""Shared web helpers: templates, permission checks and night lookups used by the route modules."""

import hashlib
import json
import re

from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from fastapi.templating import Jinja2Templates
from whistapp.bids.registry import registered_bids

from .auth import current_account, is_server_admin
from .buildinfo import application_commit, application_version
from .club import active_club_id, club_settings
from .database import connect
from .finance import MONEY_MODES
from .rule_versions import night_rules
from .rules import bid_short_names, enabled_bid_keys, load_rules
from .vision_import import admin_llm_settings


ROOT = Path(__file__).parent


def user_clubs(request):
    """Clubs the signed-in user may enter: every club for the server admin, else active memberships."""
    with connect() as db:
        if is_server_admin(request):
            rows = db.execute("SELECT id,name FROM clubs ORDER BY id").fetchall()
        else:
            account = current_account(request)
            if not account:
                return []
            rows = db.execute("""SELECT DISTINCT c.id,c.name FROM clubs c
                JOIN club_members cm ON cm.club_id=c.id AND cm.status='active'
                JOIN member_accounts ma ON ma.member_id=cm.id
                WHERE ma.user_id=? ORDER BY c.id""", (account["id"],)).fetchall()
    return [dict(r) for r in rows]


def resolve_active_club(request):
    # A stale or foreign club id in the cookie is dropped; a single club is chosen automatically.
    clubs = user_clubs(request)
    chosen = request.session.get("club_id")
    if chosen in {c["id"] for c in clubs}:
        return chosen
    request.session.pop("club_id", None)
    return clubs[0]["id"] if len(clubs) == 1 else None


def in_active_club(path):
    """Night and member URLs only resolve inside the chosen club."""
    with connect() as db:
        if m := re.match(r"/sessions/(\d+)(?:/|$)", path):
            return db.execute("SELECT 1 FROM sessions WHERE id=? AND club_id=?",
                              (int(m.group(1)), active_club_id())).fetchone() is not None
        if m := re.match(r"/club/members/(\d+)/", path):
            return db.execute("SELECT 1 FROM club_members WHERE id=? AND club_id=?",
                              (int(m.group(1)), active_club_id())).fetchone() is not None
    return True


def active_club(request):
    if active_club_id() is None:
        return None
    clubs = user_clubs(request)
    current = next((c for c in clubs if c["id"] == active_club_id()), None)
    return dict(current, choices=len(clubs)) if current else None


# Any active member of the original club may found a club, at least for now.
FOUNDING_CLUB_ID = 1


def can_create_club(request):
    if is_server_admin(request):
        return True
    account = current_account(request)
    if not account or account["must_change_password"]:
        return False
    with connect() as db:
        return db.execute("""SELECT 1 FROM club_members cm JOIN member_accounts ma ON ma.member_id=cm.id
            WHERE ma.user_id=? AND cm.club_id=? AND cm.status='active'""",
            (account["id"], FOUNDING_CLUB_ID)).fetchone() is not None


def linked_member(request):
    account = current_account(request)
    if not account or account["must_change_password"]:
        return None
    with connect() as db:
        member = db.execute("""SELECT cm.* FROM club_members cm
            JOIN member_accounts ma ON ma.member_id=cm.id
            WHERE ma.user_id=? AND cm.status='active' AND cm.club_id=?""",
            (account["id"], active_club_id())).fetchone()
    return dict(member) if member else None


def can_manage_club(request):
    member = linked_member(request)
    return is_server_admin(request) or bool(member and member["is_club_admin"])


# Ordinary members can view results, not mutate club data.
def member_read_allowed(path):
    if path in ("/statistics", "/account", "/") or re.fullmatch(r"/statistics/\d+(/export\.xlsx)?", path) or re.fullmatch(r"/bids/[a-z_]+/rules", path) or re.fullmatch(r"/statistics/rules/[a-z_]+", path):
        return True
    return bool(re.fullmatch(r"/sessions/\d+/(live|live/scoreboard|export\.xlsx|history|statistics)", path))


def ai_import_allowed():
    if not admin_llm_settings().get("configured"):
        return False
    with connect() as db:
        settings = club_settings(db)
    return bool(settings and settings["allow_ai_import"])


def member_operation_allowed(path, method):
    if method in ("GET", "HEAD"):
        return (member_read_allowed(path) or path == "/import" or
                path == "/import/current" or
                bool(re.fullmatch(r"/import/current/[a-f0-9]+", path)) or
                bool(re.fullmatch(r"/sessions/\d+", path)) or
                path.startswith("/import/") or
                bool(re.fullmatch(r"/sessions/\d+/(editor/.*|bid-fields|photos/\d+|source-files/\d+)", path)))
    if method == "POST":
        return (path == "/sessions" or
                (path.startswith("/import/") and not path.startswith("/import/ai") or
                 path == "/import/ai" or path.startswith("/import/current/")) or
                # Any active member runs the night, including closing and reopening it.
                bool(re.fullmatch(r"/sessions/\d+/(players|players/\d+/toggle|rotation|hands|hands/\d+/(result|delete)|penalties|penalties/\d+/delete|seats|photos|end|reopen)", path)))
    return False


templates = Jinja2Templates(directory=ROOT / "templates")


templates.env.globals["app_version"] = application_version()


templates.env.globals["app_commit"] = application_commit()


def bid_rule_options():
    seen = set()
    options = []
    # Only the bids the active club plays.
    with connect() as db:
        settings = club_settings(db)
    bids = registered_bids()
    # In the club's bid order; VIP appears once.
    for key in enabled_bid_keys(json.loads(settings["bid_settings"] or "{}") if settings else {}):
        cls = type(bids[key])

        if cls in seen:
            continue

        seen.add(cls)
        options.append(
            {
                "key": key,
                "label": cls.__name__,
            }
        )

    return options


templates.env.globals["bid_rule_options"] = bid_rule_options


templates.env.globals["can_manage_club"] = can_manage_club


templates.env.globals["ai_import_allowed"] = ai_import_allowed


templates.env.globals["active_club"] = active_club


def render(request, template, **data):
    return templates.TemplateResponse(request=request, name=template, context=data)


def get_session(db, sid):
    session = db.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    if session is None:
        raise HTTPException(404, "Session not found")
    return session


def session_bid_settings(db, session):
    """The bid settings of the rule version the night is played under."""
    return night_rules(db, session["id"])["bid_settings"]


def session_rules(db, sid):
    return load_rules(session_bid_settings(db, get_session(db, sid)))["bids"]


def night_state_token(db, sid):
    """What a page showed of a night: hands, scores, Fejl40, players and whether it is closed.

    Close and reopen forms send it back; if another device changed the night in between,
    the request is refused rather than closing a result the person did not see.
    """
    parts = [
        [tuple(r) for r in db.execute(
            "SELECT id,bid_key,target,actual,won,partner_id FROM hands WHERE session_id=? ORDER BY id", (sid,))],
        [tuple(r) for r in db.execute(
            """SELECT hs.hand_id,hs.player_id,hs.numerator,hs.denominator FROM hand_scores hs
            JOIN hands h ON h.id=hs.hand_id WHERE h.session_id=? ORDER BY hs.hand_id,hs.player_id""", (sid,))],
        [tuple(r) for r in db.execute(
            "SELECT id,player_id,amount FROM penalty_payments WHERE session_id=? ORDER BY id", (sid,))],
        [tuple(r) for r in db.execute(
            "SELECT id,member_id,active FROM players WHERE session_id=? ORDER BY id", (sid,))],
        db.execute("SELECT ended_at FROM sessions WHERE id=?", (sid,)).fetchone()[0],
    ]
    return hashlib.sha256(repr(parts).encode()).hexdigest()[:32]


def require_open(db, sid):
    s = get_session(db, sid)
    if s["ended_at"] is not None:
        raise HTTPException(409, "Spilaftenen er afsluttet")
    return s


def fmt(value):
    return (
        str(value.numerator)
        if value.denominator == 1
        else f"{float(value):.2f}".rstrip("0").rstrip(".").replace(".", ",")
    )


templates.env.globals["fmt"] = fmt


DISPLAY_TZ = ZoneInfo("Europe/Copenhagen")


def local_time(value, imported=False):
    """Show a stored UTC timestamp (SQLite CURRENT_TIMESTAMP) in Danish time.

    Imported nights store the date typed by the user, which is already Danish
    wall-clock time, so those are shown unchanged. Independent of the server's TZ."""
    if not value or imported: return value
    try:
        stamp = datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return value
    return stamp.replace(tzinfo=timezone.utc).astimezone(DISPLAY_TZ).strftime("%Y-%m-%d %H:%M:%S")


def night_time(session, field="created_at"):
    """Danish display time of a night's created_at/ended_at; an imported night's own date stays as typed."""
    value = session[field]
    # Imported nights get ended_at=created_at (typed date); a later close stamps UTC like a live night.
    imported = session["origin"] == "imported" and (field == "created_at" or value == session["created_at"])
    return local_time(value, imported)


templates.env.filters["local_time"] = local_time
templates.env.globals["night_time"] = night_time


def static_url(name):
    """Static file URL whose version follows the file, so phones never keep a stale copy."""
    try:
        version = int((ROOT / "static" / name).stat().st_mtime)
    except OSError:
        version = 0
    return f"/static/{name}?v={version}"


templates.env.globals["static_url"] = static_url


# A linked player shows the member's current club name; players.name is only the
# snapshot from when the night was created (and the name of guests/unlinked players).
PLAYER_COLUMNS = """p.id,p.session_id,COALESCE(cm.name,p.name) AS name,p.active,p.club_id,
    p.seat_order,p.member_id FROM players p JOIN sessions ps ON ps.id=p.session_id
    LEFT JOIN club_members cm ON cm.id=p.member_id AND cm.club_id=ps.club_id"""


def session_players(db, sid, active_only=False):
    return db.execute(
        f"SELECT {PLAYER_COLUMNS} WHERE p.session_id=?"
        + (" AND p.active=1" if active_only else "") + " ORDER BY p.seat_order, p.id",
        (sid,),
    ).fetchall()


def next_dealer(players, last_dealer, hand_count):
    """Who deals the next hand: the next player at the table after the last dealer, in seat
    order, so the deal moves on round the table even when someone joins or leaves.
    Nights saved before dealers were recorded fall back to counting hands."""
    seats = [p["id"] for p in players]
    active = [p["id"] for p in players if p["active"]]
    if not active:
        return None
    if last_dealer in seats:
        i = seats.index(last_dealer)
        return next(pid for pid in seats[i + 1:] + seats[:i + 1] if pid in active)
    return active[hand_count % len(active)]


def hand_dealer(players, dealer, participants):
    """The rotation's dealer if they play the hand, else the first chosen player after them."""
    active = [p["id"] for p in players if p["active"]]
    i = active.index(dealer)
    return next(pid for pid in active[i:] + active[:i] if pid in participants)


def hand_round(db, sid, participants):
    """The dealer of the hand about to be saved, its round, and whether it ends the round.

    A round is over when everyone at the table has dealt in it: whoever left before their
    deal is not waited for, and whoever joins must deal too. All three are stored with the
    hand, so later changes to the table never move an earlier round."""
    players = session_players(db, sid)
    ids = {p["id"] for p in players if p["active"]}
    last = db.execute("SELECT dealer_id,round_no,round_end FROM hands WHERE session_id=? ORDER BY id DESC LIMIT 1", (sid,)).fetchone()
    count = db.execute("SELECT COUNT(*) FROM hands WHERE session_id=?", (sid,)).fetchone()[0]
    dealer = hand_dealer(players, next_dealer(players, last[0] if last else None, count), participants)
    if not last or last[1] is None:
        round_no, dealt = 1, set()
    elif last[2]:
        round_no, dealt = last[1] + 1, set()
    else:
        round_no = last[1]
        dealt = {r[0] for r in db.execute(
            "SELECT dealer_id FROM hands WHERE session_id=? AND round_no=?", (sid, round_no))}
        # Whoever had still to deal has left the table: that round is over, a new one starts.
        if ids <= dealt:
            round_no, dealt = round_no + 1, set()
    return dealer, round_no, int(ids <= dealt | {dealer})


def view_data(db, sid):
    session = get_session(db, sid)
    players = session_players(db, sid)
    hands = list(
        db.execute("SELECT * FROM hands WHERE session_id=? ORDER BY id", (sid,))
    )
    totals = {p["id"]: Fraction(0) for p in players}
    rows = []

    for i, h in enumerate(hands, 1):
        participants = [
            r[0]
            for r in db.execute(
                "SELECT player_id FROM hand_participants WHERE hand_id=?", (h["id"],)
            )
        ]
        declarer_row = db.execute(
            "SELECT player_id FROM hand_declarers WHERE hand_id=? ORDER BY player_id LIMIT 1",
            (h["id"],),
        ).fetchone()
        declarer = declarer_row[0] if declarer_row else None
        special_results = {
            r["player_id"]: dict(r)
            for r in db.execute(
                "SELECT * FROM special_bids WHERE hand_id=?", (h["id"],)
            )
        }
        changes = {}
        for r in db.execute(
            "SELECT player_id,numerator,denominator FROM hand_scores WHERE hand_id=?",
            (h["id"],),
        ):
            changes[r[0]] = Fraction(r[1], r[2])
            totals[r[0]] += changes[r[0]]
        rows.append(
            dict(
                hand=h,
                number=i,
                participants=participants,
                declarer=declarer,
                partner=h["partner_id"],
                special_results=special_results,
                changes={k: fmt(v) for k, v in changes.items()},
                totals={k: fmt(v) for k, v in totals.items()},
            )
        )

    # Final-only imports have no hand rows; display their known results.
    for r in db.execute(
        "SELECT player_id,numerator,denominator FROM historical_final_scores WHERE session_id=?",
        (sid,),
    ):
        totals[r["player_id"]] += Fraction(r["numerator"], r["denominator"])

    active = [p for p in players if p["active"]]
    ids = [p["id"] for p in active]
    # A line goes under the hand that ended a round: the next hand began a new one, or the
    # latest round is complete for the players at the table now.
    round_progress = None
    for row, after in zip(rows, rows[1:] + [None]):
        number = row["hand"]["round_no"]
        row["round_no"] = number
        row["round_end"] = number is not None and bool(
            row["hand"]["round_end"] or (after is not None and after["hand"]["round_no"] != number))
    # How far the round in progress has come, counted against who is at the table now.
    if rows and rows[-1]["round_no"] is not None and not rows[-1]["round_end"]:
        dealt = {r["hand"]["dealer_id"] for r in rows if r["round_no"] == rows[-1]["round_no"]}
        round_progress = (len(dealt & set(ids)), len(ids))
    mode = session["rotation_mode"]
    offsets = [0, 1, 2, 3] if mode == "consecutive" else [0, 1, 3, 4]
    # The rotation starts at the next dealer, who also comes first as Melder.
    dealer = next_dealer(players, hands[-1]["dealer_id"] if hands else None, len(hands))
    shift = ids.index(dealer) if dealer in ids else 0
    suggested = (
        [ids[(shift + i) % len(ids)] for i in offsets]
        if len(ids) >= max(offsets) + 1
        else (ids[:4] if len(ids) >= 4 else [])
    )
    low = min(totals.values(), default=Fraction(0))
    high = max(totals.values(), default=Fraction(0))

    # Interpolate hue from red (0) to green (120), lowest to highest.
    score_colors = {
        p[
            "id"
        ]: f"hsl({round(120 * float((totals[p['id']] - low) / (high - low))) if high != low else 60}, 48%, 29%)"
        for p in players
    }

    penalties = list(
        db.execute(
            """SELECT pp.*,COALESCE(cm.name,p.name) AS name FROM penalty_payments pp
            JOIN players p ON p.id=pp.player_id JOIN sessions ps ON ps.id=p.session_id
            LEFT JOIN club_members cm ON cm.id=p.member_id AND cm.club_id=ps.club_id
            WHERE pp.session_id=? ORDER BY pp.id""",
            (sid,),
        )
    )
    settled = [
        dict(r)
        for r in db.execute(
            """SELECT cs.place,cm.name,cs.amount_numerator,cs.amount_denominator
        FROM club_member_settlements cs JOIN club_members cm ON cm.id=cs.member_id
        WHERE cs.session_id=? ORDER BY cs.place,cm.id""",
            (sid,),
        )
    ]
    # A negative amount is money the member receives (players settling between themselves).
    settled = [
        dict(
            r,
            placement_amount=fmt(
                abs(Fraction(r["amount_numerator"], r["amount_denominator"]))
            ),
            receives=r["amount_numerator"] < 0,
        )
        for r in settled
    ]
    night = night_rules(db, sid)
    money_mode = night["money_mode"]

    return dict(
        session=session,
        players=players,
        active=active,
        rows=rows,
        round_progress=round_progress,
        totals={k: fmt(v) for k, v in totals.items()},
        score_colors=score_colors,
        suggested=suggested,
        dealer=dealer,
        # The club's short bid names for the compact table, as set now on Klubadministration.
        bid_short=bid_short_names(json.loads((db.execute(
            "SELECT bid_settings FROM club_settings WHERE id=?", (session["club_id"],)).fetchone() or ["{}"])[0] or "{}")),
        # The new-hand form remembers which hand it was built after, to refuse stale saves.
        last_hand_id=hands[-1]["id"] if hands else 0,
        night_state=night_state_token(db, sid),
        rules=load_rules(night["bid_settings"]),
        enabled_bids=enabled_bid_keys(night["bid_settings"]),
        money_mode=money_mode,
        money_mode_label=MONEY_MODES.get(money_mode, money_mode),
        penalties=penalties,
        settled=settled,
        penalty_total=sum(x["amount"] for x in penalties),
        placement_total=fmt(
            sum(
                (
                    Fraction(x["amount_numerator"], x["amount_denominator"])
                    for x in settled
                ),
                Fraction(),
            )
        ),
        club_total=fmt(
            sum(
                (
                    Fraction(x["amount_numerator"], x["amount_denominator"])
                    for x in settled
                ),
                Fraction(),
            )
            + sum(x["amount"] for x in penalties)
        ),
        fee=night["fejl40"],
    )
