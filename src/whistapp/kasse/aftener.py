"""What closed club nights charge each member in the kasse.

A night counts once it is closed with Klubkasse chosen (collect_club_money), on or after the
kasse's start date. It charges its settlement (from the night's own rule version, fx Pladsbetaling
efter placering) plus its Fejl40 penalties. In the money mode where players settle with each other,
the settlement does not go to the kasse; Fejl40 always does.
Amounts here are charges: positive means the member pays the club. ledger.py turns a charge
into a negative saldo.
"""

from datetime import datetime, timezone
from fractions import Fraction
from zoneinfo import ZoneInfo

from ..rule_versions import night_rules

DANISH_TIME = ZoneInfo("Europe/Copenhagen")

# Players pay each other in this mode; the club is owed no settlement.
NO_KASSE_MODES = {"score_settle"}


def night_parts(db, sid):
    """({member_id: settlement}, {member_id: Fejl40}) that one closed night charges. Empty if it charges nothing."""
    night = db.execute("SELECT ended_at,collect_club_money FROM sessions WHERE id=?", (sid,)).fetchone()
    if not night or night["ended_at"] is None or not night["collect_club_money"]:
        return {}, {}
    settlement, fejl40 = {}, {}
    if night_rules(db, sid)["money_mode"] not in NO_KASSE_MODES:
        for r in db.execute("""SELECT member_id,amount_numerator,amount_denominator
                FROM club_member_settlements WHERE session_id=?""", (sid,)):
            settlement[r[0]] = settlement.get(r[0], Fraction()) + Fraction(r[1], r[2])
    for r in db.execute("""SELECT p.member_id,pp.amount FROM penalty_payments pp
            JOIN players p ON p.id=pp.player_id WHERE pp.session_id=? AND p.member_id IS NOT NULL""", (sid,)):
        fejl40[r[0]] = fejl40.get(r[0], Fraction()) + r[1]
    return settlement, fejl40


def night_amounts(db, sid):
    """{member_id: Fraction}: everything one closed night charges, settlement and Fejl40 together."""
    total = {}
    for part in night_parts(db, sid):
        for mid, amount in part.items():
            total[mid] = total.get(mid, Fraction()) + amount
    return {mid: amount for mid, amount in total.items() if amount}


def counted_nights(db, club_id, kasse_start):
    """Ids of the club's nights that count in the kasse."""
    rows = db.execute("""SELECT id,created_at,origin FROM sessions WHERE club_id=? AND ended_at IS NOT NULL
        AND collect_club_money=1 ORDER BY id""", (club_id,))
    return [r["id"] for r in rows if not before_start(r, kasse_start)]


def night_date(night):
    """The Danish date a night was played: a live night's created_at is UTC, an imported night's
    is the date typed in, already Danish. A live night started at 00:30 belongs to that day."""
    stamp = str(night["created_at"] or "")
    if night["origin"] == "imported":
        return stamp[:10]
    try:
        utc = datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return stamp[:10]
    return utc.astimezone(DANISH_TIME).date().isoformat()


def before_start(night, kasse_start):
    """True if the night was played before the kasse started, so it never counts."""
    return bool(kasse_start) and night_date(night) < kasse_start


def charged(db, club_id, kasse_start):
    """({member_id: settlement}, {member_id: Fejl40}) summed over every counted night."""
    settlement, fejl40 = {}, {}
    for sid in counted_nights(db, club_id, kasse_start):
        for total, part in zip((settlement, fejl40), night_parts(db, sid)):
            for mid, amount in part.items():
                total[mid] = total.get(mid, Fraction()) + amount
    return settlement, fejl40
