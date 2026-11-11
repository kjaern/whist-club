"""backfill-dealers gives live nights from before dealers were recorded the dealers and rounds
the app would have saved, and leaves imported and half-filled nights alone."""
import pytest
from fastapi.testclient import TestClient

import whistapp.database as database
from whistapp import migrations
from whistapp.database import connect, init_db
from whistapp.main import app
from whistapp.migrations import backfill_dealers
from test_phone_editor import _five, _play_suggested


def _saved(sid):
    with connect() as db:
        return [tuple(r) for r in db.execute(
            "SELECT id,dealer_id,round_no,round_end FROM hands WHERE session_id=? ORDER BY id", (sid,))]


def _replayed_after_wiping(sid):
    with connect() as db:
        db.execute("UPDATE hands SET dealer_id=NULL,round_no=NULL,round_end=NULL WHERE session_id=?", (sid,))
        return backfill_dealers.replay(db, sid)


def test_replay_gives_back_what_the_live_app_saved_for_a_full_table():
    """With everyone playing the hands the rotation suggests, the old rotation and today's rule agree."""
    with TestClient(app) as client:
        client.post("/login", data={"username": "admin", "password": "a$$word"})
        sid = _five(client)
        try:
            for _ in range(12):
                _play_suggested(client, sid)
            live = _saved(sid)
            assert all(dealer is not None for _, dealer, _, _ in live)
            assert _replayed_after_wiping(sid) == live
        finally:
            client.post(f"/sessions/{sid}/delete")


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "data" / "whist.sqlite3")
    monkeypatch.setenv("WHIST_BACKUP_DIR", str(tmp_path / "backups"))
    init_db()
    return tmp_path / "backups"


def _night(db, origin, hands, dealers=None, gone=""):
    """A night with players A–E in seat order; `hands` lists who played each hand, by letter, and
    `gone` who has left the table."""
    sid = db.execute("INSERT INTO sessions(title,club_id,origin) VALUES (?,1,?)", (origin, origin)).lastrowid
    ids = {n: db.execute("INSERT INTO players(session_id,name,active,seat_order) VALUES (?,?,?,?)",
                         (sid, n, int(n not in gone), i)).lastrowid for i, n in enumerate("ABCDE")}
    for k, letters in enumerate(hands):
        dealer = dealers and dealers[k] and ids[dealers[k]]
        hid = db.execute("""INSERT INTO hands(session_id,bid_key,bid_label,target,won,rules_snapshot,dealer_id,round_no)
            VALUES (?,'sol','Sol',0,1,'{}',?,?)""", (sid, dealer, 1 if dealer else None)).lastrowid
        for n in letters:
            db.execute("INSERT INTO hand_participants(hand_id,player_id) VALUES (?,?)", (hid, ids[n]))
    return sid, {v: k for k, v in ids.items()}


def test_migration_fills_old_live_nights_only(tmp_db):
    rotation = ["ABCD", "BCDE", "CDEA", "DEAB", "EABC", "ABCD"]
    with connect() as db:
        old, names = _night(db, "live", rotation)
        imported, _ = _night(db, "imported", rotation)
        half, _ = _night(db, "live", rotation[:2], dealers=["A", None])
    assert "backfill-dealers: needed" in migrations.status()

    result = migrations.run("backfill-dealers")
    assert f"6 hands in 1 nights: #{old}" in result
    assert f"aften #{half} 'live': kun 1 af 2 hænder mangler giver" in result
    assert len(list(tmp_db.glob("whist-migration-*.sqlite3"))) == 1
    with connect() as db:
        rows = db.execute("SELECT dealer_id,round_no,round_end FROM hands WHERE session_id=? ORDER BY id", (old,)).fetchall()
        assert "".join(names[r[0]] for r in rows) == "ABCDEA"
        assert [(r[1], r[2]) for r in rows] == [(1, 0), (1, 0), (1, 0), (1, 0), (1, 1), (2, 0)]
        assert db.execute("SELECT COUNT(*) FROM hands WHERE session_id=? AND dealer_id IS NOT NULL",
                          (imported,)).fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM hands WHERE session_id=? AND dealer_id IS NULL",
                          (half,)).fetchone()[0] == 1

    assert "backfill-dealers: done" in migrations.status()
    assert "already done" in migrations.run("backfill-dealers")


def test_starting_the_app_never_fills_in_dealers(tmp_db):
    with connect() as db:
        sid, _ = _night(db, "live", ["ABCD", "BCDE"])
    init_db()
    with connect() as db:
        assert db.execute("SELECT COUNT(*) FROM hands WHERE session_id=? AND dealer_id IS NULL", (sid,)).fetchone()[0] == 2


def _replayed(db, sid, names):
    return [(names[dealer], round_no, end) for _, dealer, round_no, end in backfill_dealers.replay(db, sid)]


def test_replay_follows_the_rotation_the_old_app_showed(tmp_db):
    with connect() as db:
        # B, the rotation's second, sits hand 2 out: C deals it, and hand 3 is still C's, as the
        # old app counted hands (today's rule would pass hand 3 on to D).
        sid, names = _night(db, "live", ["ABCD", "ACDE", "CDEA"])
        assert _replayed(db, sid, names) == [("A", 1, 0), ("C", 1, 0), ("C", 1, 0)]
        # B leaves after hand 2: the old app counted on among A, C, D, E.
        sid, names = _night(db, "live", ["ABCD", "BCDE", "CDEA", "ACDE"], gone="B")
        assert _replayed(db, sid, names) == [("A", 1, 0), ("B", 1, 0), ("D", 1, 0), ("E", 1, 0)]


def test_nights_with_a_hand_without_known_players_are_left_alone(tmp_db):
    with connect() as db:
        empty, _ = _night(db, "live", ["ABCD", ""])
        stranger, _ = _night(db, "live", ["ABCD"])
        other = db.execute("SELECT id FROM players WHERE session_id=?", (empty,)).fetchone()[0]
        db.execute("INSERT INTO hand_participants(hand_id,player_id) SELECT id,? FROM hands WHERE session_id=?",
                   (other, stranger))
        assert backfill_dealers.replay(db, empty) is None and backfill_dealers.replay(db, stranger) is None
    assert "backfill-dealers: done" in migrations.status()  # nothing it can fill in
    with connect() as db:
        _, review = backfill_dealers._plan(db)
    assert [line.split(":")[0] for line in review] == [f"aften #{empty} 'live'", f"aften #{stranger} 'live'"]
