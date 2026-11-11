"""Give live nights from before 7 Nov 2026 their dealers and rounds.

Since commit ae0f25f every live hand stores its dealer (`hands.dealer_id`), its round and whether
it ends the round; hands saved before that have all three NULL, so those nights show no Giver
and no round lines. The app of the time had no dealer, but it put a player first in its rotation
for every hand: the player at position (hands so far) modulo (players at the table), in seat
order, and that is the player who dealt. This migration replays each such night with that rule;
a dealer who sat the hand out passes it to the next player who played (`web.hand_dealer`). Rounds
follow the app's round rule (`web.next_round`).

Who sat at the table at each hand was not stored, only who played it, so a player counts as at
the table from the first hand they played; one who has left (`players.active = 0`) counts until
the last hand they played, one still at the table until the end of the night. Someone who sat
out the night's first hands is counted from their first hand; that only moves a dealer or a round
line if they sat out longer than the rotation would have them, which the data cannot tell apart
from arriving late.

Only live nights whose hands all lack dealer and round are filled in. Imported nights are left
alone (their seat order is the order of the columns on the sheet, not the table), and so is a
night with only some hands filled in, or a hand without players or with a player who is not
among the night's players; those are listed. Running it again changes nothing.
"""

from ..web import hand_dealer, next_round

NAME = "backfill-dealers"
DESCRIPTION = ("Fill in Giver and rounds on live nights saved before dealers were recorded (7 Nov 2026), "
               "with the rotation the app showed then. Imported nights are left alone.")


def replay(db, sid):
    """[(hand id, dealer id, round no, round end)] for the night, or None if a hand has no players
    or a player who is not one of the night's players."""
    seats = db.execute("SELECT id,active FROM players WHERE session_id=? ORDER BY seat_order,id", (sid,)).fetchall()
    seat_ids = {p["id"] for p in seats}
    hands = [r[0] for r in db.execute("SELECT id FROM hands WHERE session_id=? ORDER BY id", (sid,))]
    played = []
    for hid in hands:
        played.append({r[0] for r in db.execute("SELECT player_id FROM hand_participants WHERE hand_id=?", (hid,))})
        if not played[-1] or not played[-1] <= seat_ids:
            return None
    first, last = {}, {}
    for k, ids in enumerate(played):
        for pid in ids:
            first.setdefault(pid, k); last[pid] = k
    out = []
    for k, hid in enumerate(hands):
        table = [p["id"] for p in seats
                 if p["id"] in first and first[p["id"]] <= k and (p["active"] or k <= last[p["id"]])]
        players = [{"id": p["id"], "active": p["id"] in table} for p in seats]
        dealer = hand_dealer(players, table[k % len(table)], played[k])
        prev = out[-1] if out else None
        dealt = {h[1] for h in out if prev and h[2] == prev[2]}
        out.append((hid, dealer, *next_round(prev[2:] if prev else None, dealt, set(table), dealer)))
    return out


def _plan(db):
    """({night id: replayed hands} to fill in, Danish lines about live nights that are left alone)."""
    todo, review = {}, []
    for sid, title, total, missing in db.execute(
            """SELECT s.id,s.title,COUNT(h.id),SUM(h.dealer_id IS NULL AND h.round_no IS NULL)
               FROM sessions s JOIN hands h ON h.session_id=s.id WHERE s.origin='live'
               GROUP BY s.id ORDER BY s.id""").fetchall():
        if not missing:
            continue
        if missing < total:
            review.append(f"aften #{sid} {title!r}: kun {missing} af {total} hænder mangler giver; rørt ikke")
        elif (rows := replay(db, sid)) is None:
            review.append(f"aften #{sid} {title!r}: en hånd har ingen spillere eller en ukendt spiller; rørt ikke")
        else:
            todo[sid] = rows
    return todo, review


def needed(db):
    return bool(_plan(db)[0])


def blockers(db):
    return []


def run(db):
    todo, review = _plan(db)
    hands = sum(db.execute("UPDATE hands SET dealer_id=?,round_no=?,round_end=? WHERE id=? AND dealer_id IS NULL",
                           (dealer, round_no, round_end, hid)).rowcount
                for rows in todo.values() for hid, dealer, round_no, round_end in rows)
    lines = [f"filled in dealers and rounds on {hands} hands in {len(todo)} nights: "
             + ", ".join(f"#{sid}" for sid in todo)]
    if review:
        lines.append("left alone:\n    " + "\n    ".join(review))
    return "\n".join(lines)
