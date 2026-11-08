"""A night's settlement and rules come from its rule version, not from today's club settings.

Before rule versions, re-closing a night ranked the members who were active *now*: a
returning member broke re-closing old nights, and swapping one member for another
silently changed who an old night charged.
"""
import json

import pytest

import whistapp.database as database
from whistapp.database import connect, init_db
from whistapp.finance import settlement_entries
from whistapp.rule_versions import is_club_night, night_rules, save_version, stamp_night

RATES = [50, 75, 125, 200, 250]  # five placement payments for five active members


@pytest.fixture
def club_night(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "roster.db")
    init_db()
    with connect() as db:
        club = db.execute("INSERT INTO clubs(name) VALUES ('Rosterklub')").lastrowid
        db.execute("INSERT INTO club_settings(id,placements,fejl40) VALUES (?,?,40)", (club, json.dumps(RATES)))
        members = [db.execute("INSERT INTO club_members(name,status,club_id) VALUES (?,?,?)",
                              (f"M{i}", "active" if i < 5 else "paused", club)).lastrowid for i in range(6)]
        save_version(db, club)  # as Klubadministration would
        sid = db.execute("INSERT INTO sessions(title,is_club,club_id) VALUES ('Aften',1,?)", (club,)).lastrowid
        stamp_night(db, sid)
        for seat, mid in enumerate(members[:4]):
            db.execute("INSERT INTO players(session_id,name,member_id,seat_order) VALUES (?,?,?,?)",
                       (sid, f"M{seat}", mid, seat))
    return club, sid, members


def _close(db, sid):
    """What /sessions/{sid}/end computes for the night."""
    night = night_rules(db, sid)
    return settlement_entries(db, sid, night["money_mode"], night["placements"], night["point_rate"])


def _return_member(db, club, members):
    """Member 5 comes back: Klubadministration needs a sixth placement payment."""
    db.execute("UPDATE club_members SET status='active' WHERE id=?", (members[5],))
    db.execute("UPDATE club_settings SET placements=? WHERE id=?", (json.dumps(RATES + [300]), club))


def test_returning_member_does_not_break_reclosing_an_old_night(club_night):
    club, sid, members = club_night
    with connect() as db:
        first = _close(db, sid)
        _return_member(db, club, members)
        assert _close(db, sid) == first


def test_roster_change_does_not_silently_change_an_old_night(club_night):
    club, sid, members = club_night
    with connect() as db:
        first = _close(db, sid)
        db.execute("UPDATE club_members SET status='paused' WHERE id=?", (members[4],))
        db.execute("UPDATE club_members SET status='active' WHERE id=?", (members[5],))
        assert _close(db, sid) == first
        assert {m for m, *_ in first} == set(members[:5])


def test_open_night_keeps_its_rules_while_the_club_changes(club_night):
    club, sid, members = club_night
    with connect() as db:
        _return_member(db, club, members)
        db.execute("UPDATE club_settings SET fejl40=99,money_mode='score_settle' WHERE id=?", (club,))
        night = night_rules(db, sid)
        assert night["placements"] == RATES and night["fejl40"] == 40 and night["money_mode"] == "placement"
        assert night["member_ids"] == members[:5]


def test_member_not_on_the_medlemsliste_makes_a_non_club_night(club_night):
    club, sid, members = club_night
    with connect() as db:
        assert is_club_night(db, sid)
        # The paused member plays: the night was not started with them on its medlemsliste.
        db.execute("INSERT INTO players(session_id,name,member_id,seat_order) VALUES (?,'M5',?,4)", (sid, members[5]))
        assert not is_club_night(db, sid)
        db.execute("DELETE FROM players WHERE member_id=? AND session_id=?", (members[5], sid))
        db.execute("INSERT INTO players(session_id,name,seat_order) VALUES (?,'Gæst',4)", (sid,))
        assert not is_club_night(db, sid)
