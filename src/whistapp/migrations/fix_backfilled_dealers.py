"""Correct the dealers that the first version of backfill-dealers gave early nights.

That version (commit 3b9d2a2) counted the dealer as (hands so far) modulo (players at the table),
in seat order. When a player left, that jumped to the wrong seat: the deal should pass to the
next player at the table after the last dealer. `backfill-dealers` now does that; this migration
replays the nights the first version filled in with the corrected rule.

A night is corrected only if it is a live night from before dealers were recorded (created by a
server older than commit ae0f25f, or every hand saved before that commit) and its stored dealers
and rounds are exactly what the first version wrote. A night recorded live, or one whose dealers
someone has changed since, is never touched. Running it again changes nothing.
"""

from . import backfill_dealers

NAME = "fix-backfilled-dealers"
DESCRIPTION = ("Correct Giver and rounds that the first backfill-dealers wrote on early nights where a player "
               "left: the deal passes to the next player at the table after the last dealer.")

# The commits the server ran before it recorded dealers (`git rev-list ae0f25f^`), as stamped on
# sessions.server_commit, and the moment ae0f25f was made, in UTC like hands.created_at.
PRE_DEALER_COMMITS = {
    "fe8c18d", "e33261b", "f0f3080", "1ea0b15", "01383a9", "6bf838d", "74eb04b", "dcfda43", "668737b",
    "9bae0e7", "25e26ec", "f24ce1d", "f329dc5", "38b04e0", "b5a82c8", "4756a1c", "788f324", "687307f",
    "b7670fa", "338dfb5", "2bd1377", "f36f399",
}
DEALERS_RECORDED = "2026-11-07 13:19:46"


def first_version_replay(db, sid):
    """What the first backfill-dealers wrote: the dealer counted from the first seat each hand."""
    return backfill_dealers._replay(db, sid, lambda players, last, k, table: table[k % len(table)])


def _plan(db):
    todo = {}
    for sid, commit, latest in db.execute(
            """SELECT s.id,s.server_commit,MAX(h.created_at) FROM sessions s JOIN hands h ON h.session_id=s.id
               WHERE s.origin='live' GROUP BY s.id HAVING SUM(h.dealer_id IS NULL)=0 ORDER BY s.id""").fetchall():
        if (commit or "")[:7] not in PRE_DEALER_COMMITS and latest >= DEALERS_RECORDED:
            continue  # recorded live
        stored = [tuple(r) for r in db.execute(
            "SELECT id,dealer_id,round_no,round_end FROM hands WHERE session_id=? ORDER BY id", (sid,))]
        if stored != first_version_replay(db, sid):
            continue  # not what the first version wrote
        if (rows := backfill_dealers.replay(db, sid)) != stored:
            todo[sid] = rows
    return todo


def needed(db):
    return bool(_plan(db))


def blockers(db):
    return []


def run(db):
    todo = _plan(db)
    hands = backfill_dealers.write(db, todo)
    return f"corrected dealers and rounds on {hands} hands in {len(todo)} nights: " + ", ".join(f"#{sid}" for sid in todo)
