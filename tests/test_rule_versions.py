"""Versioned club rules: each night points to the rules and medlemsliste it was played under."""
import json

import pytest
from fastapi.testclient import TestClient

import whistapp.database as database
from whistapp.database import connect, init_db
from whistapp.main import app
from whistapp.rule_versions import current_version_id, get_version, latest_version
from test_bid_settings import _bid_fields, _club_form, restore_club_rules  # noqa: F401

FIVE = json.dumps([50, 75, 125, 200, 250])
FOUR = json.dumps([50, 75, 125, 200])


@pytest.fixture(autouse=True)
def _shared_home():
    init_db()  # the shared test home exists even when this file runs on its own


@pytest.fixture
def own_db(tmp_path, monkeypatch):
    """A private database, so migration is tested from a known starting point."""
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "versions.db")
    init_db()
    with connect() as db:
        club = db.execute("INSERT INTO clubs(name) VALUES ('Versionsklub')").lastrowid
        db.execute("INSERT INTO club_settings(id,placements,fejl40) VALUES (?,?,40)", (club, FIVE))
        members = [db.execute("INSERT INTO club_members(name,status,club_id) VALUES (?,?,?)",
                              (f"M{i}", "active" if i < 5 else "paused", club)).lastrowid for i in range(6)]
    return club, members


def _night(db, club, placements=FIVE, created="2026-01-01 19:00:00", bid_settings="{}", **extra):
    sid = db.execute("""INSERT INTO sessions(title,club_id,is_club,created_at,placement_rates,fejl40_rate,
        money_mode,point_rate,bid_settings) VALUES ('Aften',?,1,?,?,40,'placement','1',?)""",
                     (club, created, placements, bid_settings)).lastrowid
    for column, value in extra.items():
        db.execute(f"UPDATE sessions SET {column}=? WHERE id=?", (value, sid))
    return sid


def _settle(db, sid, member_ids):
    db.executemany("""INSERT INTO club_member_settlements(session_id,member_id,place,amount_numerator,amount_denominator)
        VALUES (?,?,1,0,1)""", [(sid, m) for m in member_ids])


def _version_of(db, sid):
    return get_version(db, db.execute("SELECT rule_version_id FROM sessions WHERE id=?", (sid,)).fetchone()[0])


def test_migration_groups_nights_by_their_own_rules_and_medlemsliste(own_db):
    club, members = own_db
    with connect() as db:
        db.execute("UPDATE sessions SET rule_version_id=NULL")
        db.execute("DELETE FROM club_rule_versions")
        a1 = _night(db, club, created="2025-01-01 19:00:00")
        a2 = _night(db, club, created="2025-02-01 19:00:00")
        _settle(db, a1, members[:5])
        _settle(db, a2, members[:5])
        # A night where member 5 was on the list and member 0 was not.
        b = _night(db, club, created="2025-03-01 19:00:00")
        _settle(db, b, members[1:6])
        # Fewer placements: a different version even with the same members table.
        c = _night(db, club, placements=FOUR, created="2025-04-01 19:00:00")
        _settle(db, c, members[:4])
    init_db()
    with connect() as db:
        va1, va2, vb, vc = (_version_of(db, s) for s in (a1, a2, b, c))
        assert va1["id"] == va2["id"]
        assert json.loads(va1["member_ids"]) == members[:5]
        assert json.loads(vb["member_ids"]) == members[1:6] and vb["id"] != va1["id"]
        assert json.loads(vc["placements"]) == [50, 75, 125, 200] and json.loads(vc["member_ids"]) == members[:4]
        # Numbered in the order the nights were played, made by the migration.
        assert [va1["number"], vb["number"], vc["number"]] == [1, 2, 3]
        assert va1["created_by"] == "migrering" and va1["created_at"] == "2025-01-01 19:00:00"
        # Today's settings and active members differ from version 3, so the club gets a current version.
        assert latest_version(db, club)["number"] == 4
        assert json.loads(latest_version(db, club)["member_ids"]) == members[:5]
        before = db.execute("SELECT COUNT(*) FROM club_rule_versions").fetchone()[0]
        links = db.execute("SELECT id,rule_version_id FROM sessions ORDER BY id").fetchall()
    init_db()  # running the migration again changes nothing
    with connect() as db:
        assert db.execute("SELECT COUNT(*) FROM club_rule_versions").fetchone()[0] == before
        assert db.execute("SELECT id,rule_version_id FROM sessions ORDER BY id").fetchall() == links


def test_migration_fills_what_a_night_never_recorded_from_current_rules(own_db):
    club, members = own_db
    with connect() as db:
        sid = _night(db, club)
        db.execute("""UPDATE sessions SET rule_version_id=NULL,placement_rates=NULL,fejl40_rate=NULL,
            money_mode=NULL,point_rate=NULL,bid_settings=NULL WHERE id=?""", (sid,))
    init_db()
    with connect() as db:
        version = _version_of(db, sid)
        assert version["id"] == latest_version(db, club)["id"]  # exactly the current rules
        assert json.loads(version["placements"]) == json.loads(FIVE) and version["fejl40"] == 40
        assert version["money_mode"] == "placement" and json.loads(version["member_ids"]) == members[:5]


def test_unsettled_night_borrows_the_nearest_matching_medlemsliste(own_db):
    club, members = own_db
    with connect() as db:
        db.execute("DELETE FROM club_rule_versions")
        settled = _night(db, club, created="2025-01-01 19:00:00")
        _settle(db, settled, members[1:6])
        open_night = _night(db, club, created="2025-02-01 19:00:00")
        db.execute("INSERT INTO players(session_id,name,member_id,seat_order) VALUES (?,?,?,0)",
                   (open_night, "M5", members[5]))
        db.execute("UPDATE sessions SET rule_version_id=NULL")
    init_db()
    with connect() as db:
        assert _version_of(db, open_night)["id"] == _version_of(db, settled)["id"]


def test_new_version_only_when_rules_or_medlemsliste_change(own_db):
    club, members = own_db
    with connect() as db:
        first = current_version_id(db, club)
        db.execute("UPDATE club_members SET name='Nyt navn' WHERE id=?", (members[0],))
        db.execute("UPDATE club_settings SET allow_ai_import=1, ai_notes='noter' WHERE id=?", (club,))
        assert current_version_id(db, club) == first  # names and non-rule settings are not rules
        db.execute("UPDATE club_members SET status='active' WHERE id=?", (members[5],))
        db.execute("UPDATE club_settings SET placements=? WHERE id=?", (json.dumps([1, 2, 3, 4, 5, 6]), club))
        second = current_version_id(db, club)
        assert second != first and json.loads(get_version(db, second)["member_ids"]) == members
        db.execute("UPDATE club_settings SET fejl40=50 WHERE id=?", (club,))
        third = current_version_id(db, club)
        assert third != second and get_version(db, third)["number"] == get_version(db, second)["number"] + 1
        assert get_version(db, first)["fejl40"] == 40  # old versions never change


def test_nights_and_club_saves_use_versions(restore_club_rules):  # noqa: F811
    with TestClient(app) as client:
        client.post("/login", data={"username": "admin", "password": "a$$word"})
        with connect() as db:
            form = _club_form(db, money_mode="score_settle", point_rate="1", placements="1", **_bid_fields())
        assert client.post("/club/save", data=form, follow_redirects=False).status_code == 303
        with connect() as db:
            saved = latest_version(db, 1)
        assert saved["created_by"] == "admin" and saved["money_mode"] == "score_settle"

        # Saving again unchanged, or with only a new name, keeps the version.
        member = next(k for k in form if k.startswith("name_"))
        form[member] = form[member] + " X"
        assert client.post("/club/save", data=form, follow_redirects=False).status_code == 303
        with connect() as db:
            assert latest_version(db, 1)["id"] == saved["id"]

        created = client.post("/sessions", data={"title": "Version", "guests": "A\nB\nC\nD"}, follow_redirects=False)
        sid = int(created.headers["location"].split("/")[-1])
        try:
            with connect() as db:
                assert _version_of(db, sid)["id"] == saved["id"]
            form["point_rate"] = "2"
            assert client.post("/club/save", data=form, follow_redirects=False).status_code == 303
            with connect() as db:
                assert latest_version(db, 1)["number"] == saved["number"] + 1
                assert _version_of(db, sid)["id"] == saved["id"]  # the open night keeps its rules
        finally:
            form[member] = form[member][:-2]
            client.post("/club/save", data=form, follow_redirects=False)
            client.post(f"/sessions/{sid}/delete")
