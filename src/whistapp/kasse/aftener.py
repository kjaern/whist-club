"""What club nights charge each member in the kasse, and when a night's charge is final.

A night charges its settlement (from the night's own rule version, fx Pladsbetaling efter
placering) plus its Fejl40 penalties. In the money mode where players settle with each other,
the settlement does not go to the kasse; Fejl40 always does. Only nights on or after the kasse's
start date count.

Afventer: an open (or reopened) club night charges what it would if it were closed now with
Klubkasse ticked, as the close form does by default. A closed night with Klubkasse chosen
(collect_club_money) charges its stored settlement.
Bogført: REOPEN_HOURS after a night was last closed it can no longer be reopened, and
ledger.finalize_due() posts what it charges, once. Reopening clears the closing time, so an old
deadline never posts a reopened night; closing again starts a new window.
Amounts here are charges: positive means the member pays the club. ledger.py turns a charge
into a negative saldo.
"""

import logging
from datetime import datetime, timedelta, timezone
from fractions import Fraction
from zoneinfo import ZoneInfo

from ..finance import settlement_entries
from ..rule_versions import is_club_night, night_rules
from ..web import REOPEN_HOURS

DANISH_TIME = ZoneInfo("Europe/Copenhagen")
STAMP = "%Y-%m-%d %H:%M:%S"  # SQLite CURRENT_TIMESTAMP, UTC

# Players pay each other in this mode; the club is owed no settlement.
NO_KASSE_MODES = {"score_settle"}


def _fejl40(db, sid):
    fejl40 = {}
    for r in db.execute("""SELECT p.member_id,pp.amount FROM penalty_payments pp
            JOIN players p ON p.id=pp.player_id WHERE pp.session_id=? AND p.member_id IS NOT NULL""", (sid,)):
        fejl40[r[0]] = fejl40.get(r[0], Fraction()) + r[1]
    return fejl40


def night_parts(db, sid):
    """({member_id: settlement}, {member_id: Fejl40}) that one closed night charges. Empty if it charges nothing."""
    night = db.execute("SELECT ended_at,collect_club_money FROM sessions WHERE id=?", (sid,)).fetchone()
    if not night or night["ended_at"] is None or not night["collect_club_money"]:
        return {}, {}
    settlement = {}
    if night_rules(db, sid)["money_mode"] not in NO_KASSE_MODES:
        for r in db.execute("""SELECT member_id,amount_numerator,amount_denominator
                FROM club_member_settlements WHERE session_id=?""", (sid,)):
            settlement[r[0]] = settlement.get(r[0], Fraction()) + Fraction(r[1], r[2])
    return settlement, _fejl40(db, sid)


def open_night_parts(db, sid):
    """({member_id: settlement}, {member_id: Fejl40}) an open night would charge if it were closed
    now with Klubkasse, the close form's default. Empty for a night without hands or with guests.
    Read only: the calculation closing uses, and nothing is stored."""
    if not db.execute("SELECT 1 FROM hands WHERE session_id=?", (sid,)).fetchone() or not is_club_night(db, sid):
        return {}, {}
    rules = night_rules(db, sid)
    settlement = {}
    if rules["money_mode"] not in NO_KASSE_MODES:
        for mid, _place, numerator, denominator in settlement_entries(
                db, sid, rules["money_mode"], rules["placements"], rules["point_rate"]):
            settlement[mid] = settlement.get(mid, Fraction()) + Fraction(numerator, denominator)
    return settlement, _fejl40(db, sid)


def night_amounts(db, sid):
    """{member_id: Fraction}: everything one closed night charges, settlement and Fejl40 together."""
    total = {}
    for part in night_parts(db, sid):
        for mid, amount in part.items():
            total[mid] = total.get(mid, Fraction()) + amount
    return {mid: amount for mid, amount in total.items() if amount}


def closed_at(night):
    """UTC time the night's result was last settled; None while it is open or when the time is unknown.

    A live night: when it was last closed (reopening clears ended_at). An imported night: when it
    was last saved (saved_at), since its ended_at is the date it was played. An imported night
    saved before saved_at was recorded, or an unreadable time, gives None: the kasse never guesses
    when such a night became final (see migrations/kasse_post_history.py).
    """
    if night["ended_at"] is None:
        return None
    stamp = night["saved_at"] if night["origin"] == "imported" else night["ended_at"]
    try:
        return datetime.strptime(str(stamp), STAMP).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def final_at(night):
    """When the kasse posts the night (UTC): REOPEN_HOURS after it was last closed, or None."""
    closed = closed_at(night)
    return closed + timedelta(hours=REOPEN_HOURS) if closed else None


def pending_nights(db, club_id, kasse_start):
    """Nights that count but are not posted yet, oldest first, each with what it charges now.

    Open club nights and closed nights with Klubkasse that the kasse has not posted. A night whose
    amounts cannot be worked out right now is listed with error set and charges nothing yet.
    """
    rows = db.execute("""SELECT id,title,created_at,ended_at,origin,saved_at FROM sessions s
        WHERE club_id=? AND (ended_at IS NULL OR collect_club_money=1)
        AND NOT EXISTS (SELECT 1 FROM kasse_night_postings p WHERE p.session_id=s.id) ORDER BY id""",
                      (club_id,)).fetchall()
    result = []
    for night in rows:
        if before_start(night, kasse_start):
            continue
        error = False
        try:
            parts = night_parts(db, night["id"]) if night["ended_at"] else open_night_parts(db, night["id"])
        except (ValueError, KeyError, TypeError, ZeroDivisionError, IndexError):
            logging.getLogger(__name__).warning("Kasse cannot work out what night %s charges", night["id"], exc_info=True)
            parts, error = ({}, {}), True
        if not error and not any(parts[0].values()) and not any(parts[1].values()):
            continue
        result.append(dict(id=night["id"], title=night["title"], date=night_date(night), open=night["ended_at"] is None,
                           final_at=final_at(night), settlement=parts[0], fejl40=parts[1], error=error))
    return result


def night_date(night):
    """The Danish date a night was played: a live night's created_at is UTC, an imported night's
    is the date typed in, already Danish. A live night started at 00:30 belongs to that day."""
    stamp = str(night["created_at"] or "")
    if night["origin"] == "imported":
        return stamp[:10]
    try:
        utc = datetime.strptime(stamp, STAMP).replace(tzinfo=timezone.utc)
    except ValueError:
        return stamp[:10]
    return utc.astimezone(DANISH_TIME).date().isoformat()


def before_start(night, kasse_start):
    """True if the night was played before the kasse started, so it never counts."""
    return bool(kasse_start) and night_date(night) < kasse_start
