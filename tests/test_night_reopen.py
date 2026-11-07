"""Reopening a night keeps its settlement until it is closed again, and close/reopen refuse stale pages."""
import json
import re
from fractions import Fraction

import pytest
from fastapi.testclient import TestClient

import whistapp.routes.sessions as sessions_routes
from whistapp.database import connect, init_db
from whistapp.main import app

RATES = [100, 200, 300, 400]


@pytest.fixture(autouse=True)
def _shared_home():
    init_db()


@pytest.fixture
def night():
    """A club night of four members on its own rule version, with one hand: 6, 2, -3, -5."""
    with connect() as db:
        sid = db.execute("INSERT INTO sessions(title,is_club,club_id) VALUES ('Genåbning',1,1)").lastrowid
        members = [db.execute("INSERT INTO club_members(name,status,club_id) VALUES (?,'paused',1)",
                              (f"Genåbner {sid}-{i}",)).lastrowid for i in range(4)]
        players = [db.execute("INSERT INTO players(session_id,name,member_id,seat_order) VALUES (?,?,?,?)",
                              (sid, f"G{i}", m, i)).lastrowid for i, m in enumerate(members)]
        rules = db.execute("SELECT fejl40,bid_settings FROM club_settings WHERE id=1").fetchone()
        version = db.execute("""INSERT INTO club_rule_versions(club_id,number,created_by,placements,fejl40,
            money_mode,point_rate,bid_settings,member_ids)
            VALUES (1,(SELECT MAX(number)+1 FROM club_rule_versions WHERE club_id=1),'test',?,?,'placement','1',?,?)""",
                             (json.dumps(RATES), rules["fejl40"], rules["bid_settings"], json.dumps(members))).lastrowid
        db.execute("UPDATE sessions SET rule_version_id=? WHERE id=?", (version, sid))
        _hand(db, sid, players, [6, 2, -3, -5])
    yield sid, members, players
    with connect() as db:
        db.execute("DELETE FROM sessions WHERE id=?", (sid,))
        db.execute("DELETE FROM club_rule_versions WHERE id=?", (version,))
        db.executemany("DELETE FROM club_members WHERE id=?", [(m,) for m in members])


def _hand(db, sid, players, scores):
    hid = db.execute("""INSERT INTO hands(session_id,bid_key,bid_label,target,rules_snapshot)
                        VALUES (?,'almindelige','Almindelige',8,'{}')""", (sid,)).lastrowid
    for pid, score in zip(players, scores):
        db.execute("INSERT INTO hand_scores VALUES (?,?,?,?)", (hid, pid, score, 1))


def _settlement(sid):
    with connect() as db:
        return {r[0]: Fraction(r[1], r[2]) for r in db.execute(
            "SELECT member_id,amount_numerator,amount_denominator FROM club_member_settlements WHERE session_id=?",
            (sid,))}


def _ended(sid):
    with connect() as db:
        return db.execute("SELECT ended_at FROM sessions WHERE id=?", (sid,)).fetchone()[0] is not None


def _log(sid):
    with connect() as db:
        return [r[0] for r in db.execute("SELECT description FROM evening_log WHERE evening_id=? ORDER BY id", (sid,))]


def _state(client, sid):
    return re.search(r'name="night_state" value="([0-9a-f]+)"', client.get(f"/sessions/{sid}").text).group(1)


def _admin():
    client = TestClient(app)
    client.__enter__()
    client.post("/login", data={"username": "admin", "password": "a$$word"})
    return client


def test_reopen_keeps_the_settlement_until_the_night_is_closed_again(night):
    sid, members, players = night
    client = _admin()
    try:
        client.post(f"/sessions/{sid}/end", data={"collect_club_money": "true", "night_state": _state(client, sid)})
        first = _settlement(sid)
        assert [first[m] for m in members] == [100, 200, 300, 400]

        assert client.post(f"/sessions/{sid}/reopen", data={"night_state": _state(client, sid)},
                           follow_redirects=False).status_code == 303
        assert not _ended(sid) and _settlement(sid) == first
        with connect() as db:
            assert db.execute("SELECT collect_club_money FROM sessions WHERE id=?", (sid,)).fetchone()[0] == 1
        page = client.get(f"/sessions/{sid}").text
        assert "Den gælder, indtil aftenen afsluttes igen." in page

        # Closing again unchanged gives the same settlement, and the log says so.
        client.post(f"/sessions/{sid}/end", data={"collect_club_money": "true", "night_state": _state(client, sid)})
        assert _ended(sid) and _settlement(sid) == first
        assert _log(sid)[-1] == "Afsluttet; afregningen er uændret"

        # A corrected night: the new settlement replaces the old, with what changed per member.
        client.post(f"/sessions/{sid}/reopen", data={"night_state": _state(client, sid)})
        with connect() as db:
            _hand(db, sid, players, [0, 0, 0, 10])  # totals 6, 2, -3, 5
        client.post(f"/sessions/{sid}/end", data={"collect_club_money": "true", "night_state": _state(client, sid)})
        assert [_settlement(sid)[m] for m in members] == [100, 300, 400, 200]
        assert _log(sid)[-1] == (f"Afsluttet; afregning ændret: Genåbner {sid}-1 200 → 300 kr, "
                                 f"Genåbner {sid}-2 300 → 400 kr, Genåbner {sid}-3 400 → 200 kr")
        history = client.get(f"/sessions/{sid}/history").text
        assert "genåbnede spilaftenen" in history and "afsluttede spilaftenen og opdaterede afregningen" in history
        with connect() as db:
            audited = db.execute("""SELECT action,COUNT(*) FROM audit_events
                WHERE session_id=? AND entity='club_member_settlements' GROUP BY action""", (sid,)).fetchall()
        assert dict(audited) == {"insert": 12, "delete": 8}
    finally:
        client.__exit__(None, None, None)


def test_failed_reclose_leaves_the_previous_settlement(night, monkeypatch):
    sid, members, _ = night
    client = _admin()
    try:
        client.post(f"/sessions/{sid}/end", data={"collect_club_money": "true", "night_state": _state(client, sid)})
        first = _settlement(sid)
        client.post(f"/sessions/{sid}/reopen", data={"night_state": _state(client, sid)})

        def broken(*args):
            raise ValueError("test")
        monkeypatch.setattr(sessions_routes, "settlement_entries", broken)
        response = client.post(f"/sessions/{sid}/end", data={"collect_club_money": "true", "night_state": _state(client, sid)})
        assert response.status_code == 409 and not _ended(sid) and _settlement(sid) == first
    finally:
        client.__exit__(None, None, None)


def test_close_and_reopen_refuse_a_page_from_before_another_device_changed_the_night(night):
    sid, members, players = night
    client = _admin()
    try:
        seen = _state(client, sid)
        with connect() as db:
            _hand(db, sid, players, [1, 1, -1, -1])  # another device saves a hand meanwhile
        response = client.post(f"/sessions/{sid}/end", data={"collect_club_money": "true", "night_state": seen})
        assert response.status_code == 409 and "ændret på en anden enhed" in response.text
        assert not _ended(sid) and _settlement(sid) == {}

        client.post(f"/sessions/{sid}/end", data={"collect_club_money": "true", "night_state": _state(client, sid)})
        assert _ended(sid)
        # Two devices both press "Afslut": the second sees the night is already closed.
        assert client.post(f"/sessions/{sid}/end", data={"collect_club_money": "true", "night_state": seen}).status_code == 409

        closed = _state(client, sid)
        client.post(f"/sessions/{sid}/reopen", data={"night_state": closed})
        client.post(f"/sessions/{sid}/end", data={"collect_club_money": "true", "night_state": _state(client, sid)})
        # A page from before that reopen and re-close cannot reopen the night again.
        with connect() as db:
            db.execute("UPDATE sessions SET ended_at='2000-01-01 00:00:00' WHERE id=?", (sid,))
        response = client.post(f"/sessions/{sid}/reopen", data={"night_state": closed})
        assert response.status_code == 409 and _ended(sid)
    finally:
        client.__exit__(None, None, None)
