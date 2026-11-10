"""What club nights charge each member in the kasse, and when a night's charge is final.

A night charges its settlement (from the night's own rule version, fx Pladsbetaling efter
placering) plus its Fejl40 penalties. In the money mode where players settle with each other,
the settlement does not go to the kasse; Fejl40 always does. Only nights on or after the kasse's
start date count.

Afventer: an open club night charges what it would if it were closed now with Klubkasse ticked,
as the close form does by default; a night reopened after it was closed without Klubkasse
(collect_club_money 0, which reopening keeps) charges nothing, as it did while closed. A closed night with Klubkasse chosen
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
from ..rule_versions import REOPEN_HOURS, is_club_night, night_rules

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


def open_night_parts(db, night):
    """({member_id: settlement}, {member_id: Fejl40}) an open night (a row from unposted_nights)
    would charge if it were closed now with Klubkasse, the close form's default. Empty for a night
    without hands or with guests, and for a night reopened after it was closed without Klubkasse
    (collect_club_money 0): that explicit no stands, and the close form keeps it unticked.
    Read only: the calculation closing uses, and nothing is stored."""
    sid = night["id"]
    if night["collect_club_money"] == 0:
        return {}, {}
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


# What working out a night's charges raises when its data is broken (fx a night without a rule
# version). Such a night is never posted and charges nothing in Afventer until a person looks at it.
NIGHT_ERRORS = (ValueError, KeyError, TypeError, ZeroDivisionError, IndexError)

# An open night started longer ago than this is flagged: it may have been forgotten.
STALE_OPEN_HOURS = 24


def unposted_nights(db, club_id, closed_only=False, kasse_start=None):
    """The club's nights the kasse has not posted, oldest first: closed nights with Klubkasse and,
    unless closed_only, open nights. The one place that says which nights may still be posted.

    kasse_start: leave out in SQL the nights clearly before it, so nights that can never count are
    not loaded on every page. Coarse on purpose, and callers still check before_start: a live
    night's created_at is UTC and its Danish date (night_date) can be a day later, so the cut is a
    day early. An unreadable created_at (fx 'ukendt') compares greater than a date and is kept."""
    which = "ended_at IS NOT NULL AND collect_club_money=1" if closed_only else "(ended_at IS NULL OR collect_club_money=1)"
    since, args = "", [club_id]
    if kasse_start:
        since, args = "AND created_at >= date(?,'-1 day')", args + [kasse_start]
    return db.execute(f"""SELECT id,title,created_at,ended_at,origin,saved_at,collect_club_money FROM sessions s
        WHERE club_id=? AND {which} {since}
        AND NOT EXISTS (SELECT 1 FROM kasse_night_postings p WHERE p.session_id=s.id) ORDER BY id""",
                      args).fetchall()


def _started_at(night):
    """UTC time a live night was started, or None (an imported night's created_at is a typed date)."""
    if night["origin"] == "imported":
        return None
    try:
        return datetime.strptime(str(night["created_at"]), STAMP).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def pending_nights(db, club_id, kasse_start, now=None):
    """Nights that count but are not posted yet, oldest first, each with what it charges now.

    Open club nights and closed nights with Klubkasse that the kasse has not posted. A night whose
    amounts cannot be worked out right now is listed with error set and charges nothing yet.
    stale: an open night started more than STALE_OPEN_HOURS ago, which may have been forgotten.
    """
    now = now or datetime.now(timezone.utc)
    result = []
    for night in unposted_nights(db, club_id, kasse_start=kasse_start):
        if before_start(night, kasse_start):
            continue
        error = False
        try:
            parts = night_parts(db, night["id"]) if night["ended_at"] else open_night_parts(db, night)
        except NIGHT_ERRORS:
            logging.getLogger(__name__).warning("Kasse cannot work out what night %s charges", night["id"], exc_info=True)
            parts, error = ({}, {}), True
        if not error and not any(parts[0].values()) and not any(parts[1].values()):
            continue
        is_open, started = night["ended_at"] is None, _started_at(night)
        result.append(dict(id=night["id"], title=night["title"], date=night_date(night), open=is_open,
                           stale=is_open and started is not None and now - started > timedelta(hours=STALE_OPEN_HOURS),
                           origin=night["origin"], saved_at=night["saved_at"],
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
