"""A closed night's settlement must depend on that night's medlemsliste, not today's.

These reproduce what phase 2 of the rule-version plan fixes: re-closing a night ranks the
members who are active *now*. They are strict xfails until that lands; run
`pytest tests/test_night_roster.py --runxfail` to see them fail on the current code.
"""
import json

import pytest

import whistapp.database as database
from whistapp.database import connect, init_db
from whistapp.finance import settlement_entries

RATES = [50, 75, 125, 200, 250]  # five placement payments for five active members


def _club_night(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "roster.db")
    init_db()
    with connect() as db:
        club = db.execute("INSERT INTO clubs(name) VALUES ('Rosterklub')").lastrowid
        members = [db.execute("INSERT INTO club_members(name,status,club_id) VALUES (?,?,?)",
                              (f"M{i}", "active" if i < 5 else "paused", club)).lastrowid for i in range(6)]
        sid = db.execute("""INSERT INTO sessions(title,is_club,club_id,placement_rates,money_mode)
            VALUES ('Aften',1,?,?,'placement')""", (club, json.dumps(RATES))).lastrowid
    return sid, members


def _close(db, sid):
    """What /sessions/{sid}/end computes for a placement night."""
    session = db.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    return settlement_entries(db, sid, session["money_mode"], json.loads(session["placement_rates"]), "1")


@pytest.mark.xfail(strict=True, raises=ValueError, reason="phase 2: settlement ranks today's active members")
def test_returning_member_does_not_break_reclosing_an_old_night(tmp_path, monkeypatch):
    sid, members = _club_night(tmp_path, monkeypatch)
    with connect() as db:
        first = _close(db, sid)
        # The paused member returns. The night was played with five members and five payments.
        db.execute("UPDATE club_members SET status='active' WHERE id=?", (members[5],))
        assert _close(db, sid) == first  # reopen + close: today this raises ValueError


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="phase 2: settlement ranks today's active members")
def test_roster_change_does_not_silently_change_an_old_night(tmp_path, monkeypatch):
    sid, members = _club_night(tmp_path, monkeypatch)
    with connect() as db:
        first = _close(db, sid)
        # One member pauses and the other returns: still five, so nothing fails...
        db.execute("UPDATE club_members SET status='paused' WHERE id=?", (members[0],))
        db.execute("UPDATE club_members SET status='active' WHERE id=?", (members[5],))
        again = _close(db, sid)
        # ...but the night now charges someone who wasn't on its medlemsliste.
        assert {m for m, *_ in again} == {m for m, *_ in first}
