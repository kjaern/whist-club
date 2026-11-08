"""Versioned club rules: one active version per club, made only by a club administrator."""
import json

import pytest
from fastapi.testclient import TestClient

import whistapp.database as database
from whistapp.database import connect, init_db
from whistapp.main import app
from whistapp.rule_versions import get_version, latest_version, night_rules, save_version, stamp_night
from test_bid_settings import _bid_fields, _club_form, restore_club_rules  # noqa: F401

FIVE = json.dumps([50, 75, 125, 200, 250])


@pytest.fixture(autouse=True)
def _shared_home():
    init_db()  # the shared test home exists even when this file runs on its own


@pytest.fixture
def own_db(tmp_path, monkeypatch):
    """A private, brand-new database with a second club of five active and one paused member."""
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "versions.db")
    init_db()
    with connect() as db:
        club = db.execute("INSERT INTO clubs(name) VALUES ('Versionsklub')").lastrowid
        db.execute("INSERT INTO club_settings(id,placements,fejl40) VALUES (?,?,40)", (club, FIVE))
        members = [db.execute("INSERT INTO club_members(name,status,club_id) VALUES (?,?,?)",
                              (f"M{i}", "active" if i < 5 else "paused", club)).lastrowid for i in range(6)]
    return club, members


def _version_of(db, sid):
    return get_version(db, db.execute("SELECT rule_version_id FROM sessions WHERE id=?", (sid,)).fetchone()[0])


def test_a_new_database_starts_the_first_club_on_version_1(own_db):
    with connect() as db:
        first = latest_version(db, 1)
        assert first["number"] == 1 and first["created_by"] == "opsætning"
    init_db()  # starting again makes nothing new
    with connect() as db:
        assert db.execute("SELECT COUNT(*) FROM club_rule_versions WHERE club_id=1").fetchone()[0] == 1


def test_new_version_only_when_rules_or_medlemsliste_change(own_db):
    club, members = own_db
    with connect() as db:
        first = save_version(db, club)
        db.execute("UPDATE club_members SET name='Nyt navn' WHERE id=?", (members[0],))
        db.execute("UPDATE club_settings SET allow_ai_import=1, ai_notes='noter' WHERE id=?", (club,))
        assert save_version(db, club) == first  # names and non-rule settings are not rules
        db.execute("UPDATE club_members SET status='active' WHERE id=?", (members[5],))
        db.execute("UPDATE club_settings SET placements=? WHERE id=?", (json.dumps([1, 2, 3, 4, 5, 6]), club))
        second = save_version(db, club)
        assert second != first and json.loads(get_version(db, second)["member_ids"]) == members
        db.execute("UPDATE club_settings SET fejl40=50 WHERE id=?", (club,))
        third = save_version(db, club)
        assert third != second and get_version(db, third)["number"] == get_version(db, second)["number"] + 1
        assert get_version(db, first)["fejl40"] == 40  # old versions never change


def test_a_night_gets_the_active_version_and_never_makes_one(own_db):
    """Even if the club's settings changed outside Klubadministration, creating a night only
    points it at the active version; a new version needs a club administrator's save."""
    club, members = own_db
    with connect() as db:
        active = save_version(db, club)
        db.execute("UPDATE club_settings SET fejl40=99 WHERE id=?", (club,))
        sid = db.execute("INSERT INTO sessions(title,club_id) VALUES ('Aften',?)", (club,)).lastrowid
        stamp_night(db, sid)
        assert _version_of(db, sid)["id"] == active and night_rules(db, sid)["fejl40"] == 40
        assert latest_version(db, club)["id"] == active


def test_a_night_without_a_version_is_an_error_not_a_guess(own_db):
    club, _ = own_db
    with connect() as db:
        sid = db.execute("INSERT INTO sessions(title,club_id) VALUES ('Uden regler',?)", (club,)).lastrowid
        with pytest.raises(ValueError, match="ingen regelversion"):
            night_rules(db, sid)
        with pytest.raises(ValueError, match="ingen regler"):
            stamp_night(db, sid)  # the club has never saved its rules


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
