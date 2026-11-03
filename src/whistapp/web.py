"""Shared web helpers: templates, permission checks and night lookups used by the route modules."""

import json
import os
import re
import subprocess

from fractions import Fraction
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path

from fastapi import HTTPException
from fastapi.templating import Jinja2Templates
from whistapp.bids.registry import registered_bids

from .auth import current_account, is_server_admin
from .club import active_club_id, club_settings
from .database import connect
from .finance import MONEY_MODES
from .rules import enabled_bid_keys, load_rules
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
    if path in ("/statistics", "/account", "/") or re.fullmatch(r"/statistics/\d+", path) or re.fullmatch(r"/bids/[a-z_]+/rules", path):
        return True
    return bool(re.fullmatch(r"/sessions/\d+/(live|live/scoreboard|export\.xlsx|history)", path))


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
                bool(re.fullmatch(r"/sessions/\d+/(players|players/\d+/toggle|rotation|hands|hands/\d+/(result|delete)|penalties|penalties/\d+/delete|seats|photos)", path)))
    return False


templates = Jinja2Templates(directory=ROOT / "templates")


def application_version():
    try:
        return package_version("whistapp")
    except PackageNotFoundError:
        return "dev"


def application_commit():
    commit = os.environ.get("WHIST_GIT_COMMIT", "").strip()
    if not commit:
        try:
            commit = subprocess.check_output(
                ["git", "rev-parse", "--short=7", "HEAD"],
                cwd=ROOT.parent,
                stderr=subprocess.DEVNULL,
                timeout=2,
                text=True,
            ).strip()
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return "unknown"
    return commit[:7]


templates.env.globals["app_version"] = application_version()


templates.env.globals["app_commit"] = application_commit()


def bid_rule_options():
    seen = set()
    options = []
    # Only the bids the active club plays.
    with connect() as db:
        settings = club_settings(db)
    enabled = set(enabled_bid_keys(json.loads(settings["bid_settings"] or "{}") if settings else {}))

    for key, bid in registered_bids().items():
        cls = type(bid)

        if cls in seen or key not in enabled:
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


def session_bid_settings(session):
    # Nights started before bid settings existed use the original house rules.
    return json.loads(session["bid_settings"]) if session["bid_settings"] else {}


def session_rules(db, sid):
    return load_rules(session_bid_settings(get_session(db, sid)))["bids"]


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


def view_data(db, sid):
    session = get_session(db, sid)
    players = db.execute(
        "SELECT * FROM players WHERE session_id=? ORDER BY seat_order, id", (sid,)
    ).fetchall()
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
    mode = session["rotation_mode"]
    offsets = [0, 1, 2, 3] if mode == "consecutive" else [0, 1, 3, 4]
    shift = len(hands)
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
            "SELECT pp.*,p.name FROM penalty_payments pp JOIN players p ON p.id=pp.player_id WHERE pp.session_id=? ORDER BY pp.id",
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
    money_mode = session["money_mode"] or "placement"

    return dict(
        session=session,
        players=players,
        active=active,
        rows=rows,
        totals={k: fmt(v) for k, v in totals.items()},
        score_colors=score_colors,
        suggested=suggested,
        rules=load_rules(session_bid_settings(session)),
        enabled_bids=enabled_bid_keys(session_bid_settings(session)),
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
        fee=session["fejl40_rate"]
        if session["fejl40_rate"] is not None
        else club_settings(db)["fejl40"],
    )
