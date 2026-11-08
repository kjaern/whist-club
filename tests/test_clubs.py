import json
import re
import sqlite3

import pytest
from fastapi.testclient import TestClient

from whistapp import database
from whistapp.rule_versions import stamp_night
from whistapp.database import connect, init_db
from whistapp.main import app


def _new_login(admin, username, member_name, club_id=1):
    with connect() as db:
        member_id = db.execute(
            "INSERT INTO club_members(name,status,club_id) VALUES (?,'active',?)",
            (member_name, club_id),
        ).lastrowid
    response = admin.post("/admin/accounts", data={"username": username, "member_id": member_id})
    temporary = re.findall(r'<input readonly value="([A-Za-z0-9_-]+)"', response.text)[1]
    return member_id, temporary


def _activate(client, username, temporary, password="club-pass-123"):
    client.post("/login", data={"username": username, "password": temporary})
    client.post("/account/password", data={
        "current_password": temporary, "new_password": password, "confirm_password": password})
    return password


@pytest.fixture
def cleanup_clubs():
    """Remove clubs created by a test so the shared test home stays single-club."""
    yield
    with connect() as db:
        extra = [r[0] for r in db.execute("SELECT id FROM clubs WHERE id<>1")]
        for club_id in extra:
            db.execute("DELETE FROM sessions WHERE club_id=?", (club_id,))
            db.execute("DELETE FROM club_member_aliases WHERE club_id=?", (club_id,))
            db.execute("DELETE FROM club_members WHERE club_id=?", (club_id,))
            db.execute("DELETE FROM clubs WHERE id=?", (club_id,))


def test_member_creates_club_and_chooses_between_clubs(cleanup_clubs):
    with TestClient(app) as admin:
        admin.post("/login", data={"username": "admin", "password": "a$$word"})
        founder_member, temporary = _new_login(admin, "founder", "Grundlægger")
        with connect() as db:
            first_club_night = db.execute(
                "INSERT INTO sessions(title,club_id) VALUES ('Klub 1 aften',1)").lastrowid
            stamp_night(db, first_club_night)

        with TestClient(app) as founder:
            password = _activate(founder, "founder", temporary)
            page = founder.get("/clubs")
            assert "Opret ny klub" in page.text
            created = founder.post("/clubs/new", data={"name": "Torsdagsklubben"}, follow_redirects=False)
            assert created.status_code == 303 and created.headers["location"] == "/club"
            with connect() as db:
                club = db.execute("SELECT id FROM clubs WHERE name='Torsdagsklubben'").fetchone()
                new_member = db.execute(
                    "SELECT * FROM club_members WHERE club_id=?", (club["id"],)).fetchone()
                settings = db.execute(
                    "SELECT placements,fejl40 FROM club_settings WHERE id=?", (club["id"],)).fetchone()
            # The founder keeps their name, administers the new club and starts on default rates.
            assert new_member["name"] == "Grundlægger" and new_member["is_club_admin"] == 1
            assert settings["placements"] == "[50, 75, 125, 200, 250]"
            assert founder.get("/club").status_code == 200

            # The new club cannot see the first club's nights.
            assert "Klub 1 aften" not in founder.get("/").text
            assert founder.get(f"/sessions/{first_club_night}/live").status_code == 404

            # Same alias may exist in both clubs.
            with connect() as db:
                db.execute("INSERT INTO club_member_aliases(member_id,club_id,alias) VALUES (?,?,?)",
                           (founder_member, 1, "Grundy"))
                db.execute("INSERT INTO club_member_aliases(member_id,club_id,alias) VALUES (?,?,?)",
                           (new_member["id"], club["id"], "Grundy"))
                with pytest.raises(sqlite3.IntegrityError):
                    db.execute("INSERT INTO club_member_aliases(member_id,club_id,alias) VALUES (?,?,?)",
                               (new_member["id"], club["id"], "grundy"))

            founder.post("/logout")
            founder.post("/login", data={"username": "founder", "password": password})
            landing = founder.get("/", follow_redirects=False)
            assert landing.status_code == 303 and landing.headers["location"] == "/clubs"
            picker = founder.get("/clubs").text
            assert "Torsdagsklubben" in picker and "Opret ny klub" in picker
            founder.post("/clubs/select", data={"club_id": 1})
            assert "Klub 1 aften" in founder.get("/").text
            assert founder.get(f"/sessions/{first_club_night}/live").status_code == 200

        # A member who only belongs to the new club cannot found clubs or enter club 1.
        outsider_member, outsider_temp = _new_login(admin, "outsider", "Udefra", club["id"])
        with TestClient(app) as outsider:
            _activate(outsider, "outsider", outsider_temp)
            assert outsider.post("/clubs/new", data={"name": "Forbudt"}).status_code == 403
            assert outsider.post("/clubs/select", data={"club_id": 1}).status_code == 403
            assert outsider.get(f"/sessions/{first_club_night}/live").status_code == 404
            assert outsider.get("/admin/accounts").status_code == 403

        # The server admin links an existing login into another club, once per club.
        admin.post("/clubs/select", data={"club_id": club["id"]})
        with connect() as db:
            second = db.execute("INSERT INTO club_members(name,club_id) VALUES ('Anden',?)",
                                (club["id"],)).lastrowid
            third = db.execute("INSERT INTO club_members(name,club_id) VALUES ('Tredje',?)",
                               (club["id"],)).lastrowid
            founder_id = db.execute("SELECT id FROM user_accounts WHERE username='founder'").fetchone()[0]
        assert admin.post(f"/club/members/{second}/link", data={"account_id": founder_id}).status_code == 409
        with connect() as db:
            outsider_id = db.execute("SELECT id FROM user_accounts WHERE username='outsider'").fetchone()[0]
        assert admin.post(f"/club/members/{third}/link", data={"account_id": outsider_id}).status_code == 409
        with connect() as db:
            db.execute("DELETE FROM sessions WHERE id=?", (first_club_night,))


def test_single_club_users_enter_their_club_directly():
    with TestClient(app) as admin:
        admin.post("/login", data={"username": "admin", "password": "a$$word"})
        response = admin.get("/", follow_redirects=False)
        assert response.status_code == 200
        assert "Skift klub" not in response.text


def test_single_club_database_migrates_without_changing_ids(tmp_path, monkeypatch):
    path = tmp_path / "old.sqlite3"
    old = sqlite3.connect(path)
    old.executescript("""
        CREATE TABLE sessions (id INTEGER PRIMARY KEY, title TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, owner_subject TEXT);
        CREATE TABLE players (id INTEGER PRIMARY KEY, session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            name TEXT NOT NULL);
        CREATE TABLE club_members (id INTEGER PRIMARY KEY AUTOINCREMENT, legacy_id TEXT UNIQUE, name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','paused','left')),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE club_settings (id INTEGER PRIMARY KEY CHECK(id=1), placements TEXT NOT NULL, fejl40 INTEGER NOT NULL);
        CREATE TABLE club_member_aliases (member_id INTEGER NOT NULL REFERENCES club_members(id) ON DELETE CASCADE,
            alias TEXT NOT NULL COLLATE NOCASE UNIQUE, PRIMARY KEY(member_id, alias));
        INSERT INTO club_members(id,name) VALUES (7,'Syv'),(9,'Ni');
        INSERT INTO club_settings VALUES (1,'[1, 2]',33);
        INSERT INTO club_member_aliases VALUES (9,'Nina');
        INSERT INTO sessions(id,title) VALUES (42,'Gammel aften');
    """)
    old.commit()
    old.close()

    monkeypatch.setattr(database, "DB_PATH", path)
    init_db()
    init_db()  # Idempotent on an already migrated database.

    with connect() as db:
        assert [tuple(r) for r in db.execute("SELECT id,name FROM clubs")][0][0] == 1
        assert [tuple(r) for r in db.execute("SELECT id,club_id FROM club_members WHERE id IN (7,9) ORDER BY id")] == [(7, 1), (9, 1)]
        assert tuple(db.execute("SELECT id,placements,fejl40,allow_ai_import FROM club_settings").fetchone()) == (1, "[1, 2]", 33, 0)
        assert [tuple(r) for r in db.execute("SELECT member_id,club_id,alias FROM club_member_aliases")] == [(9, 1, "Nina")]
        assert tuple(db.execute("SELECT id,title,club_id FROM sessions").fetchone()) == (42, "Gammel aften", 1)
        # A second club can now have its own settings row.
        db.execute("INSERT INTO clubs(name) VALUES ('Ny')")
        db.execute("INSERT INTO club_settings(id,placements,fejl40) VALUES (2,'[]',40)")


def _club_form(db, club_id, **extra):
    members = db.execute("SELECT id,name,status FROM club_members WHERE club_id=?", (club_id,)).fetchall()
    settings = db.execute("SELECT placements,fejl40 FROM club_settings WHERE id=?", (club_id,)).fetchone()
    data = {"placements": ",".join(str(x) for x in json.loads(settings["placements"])),
            "fejl40": str(settings["fejl40"]), "money_mode": "score_settle", "point_rate": "1"}
    for m in members:
        data[f"name_{m['id']}"] = m["name"]
        data[f"status_{m['id']}"] = "active" if m["status"] == "active" else "paused"
    data.update(extra)
    return data


def test_club_can_be_renamed_but_names_stay_unique(cleanup_clubs):
    with connect() as db:
        original = db.execute("SELECT name FROM clubs WHERE id=1").fetchone()[0]
        settings = tuple(db.execute("SELECT money_mode,point_rate FROM club_settings WHERE id=1").fetchone())
        db.execute("INSERT INTO clubs(name) VALUES ('Fredagsklubben')")
    try:
        with TestClient(app) as admin:
            admin.post("/login", data={"username": "admin", "password": "a$$word"})
            admin.post("/clubs/select", data={"club_id": 1})
            assert f'name="club_name" value="{original}"' in admin.get("/club").text

            with connect() as db:
                form = _club_form(db, 1, club_name="  Mandags   Whist ")
            assert admin.post("/club/save", data=form, follow_redirects=False).status_code == 303
            with connect() as db:
                assert db.execute("SELECT name FROM clubs WHERE id=1").fetchone()[0] == "Mandags Whist"
            assert "Mandags Whist" in admin.get("/clubs").text

            with connect() as db:
                form = _club_form(db, 1, club_name="fredagsklubben", fejl40="55")
            refused = admin.post("/club/save", data=form)
            assert "Der findes allerede en klub med navnet fredagsklubben." in refused.text
            assert 'value="fredagsklubben"' in refused.text
            with connect() as db:
                assert db.execute("SELECT name FROM clubs WHERE id=1").fetchone()[0] == "Mandags Whist"
                assert db.execute("SELECT fejl40 FROM club_settings WHERE id=1").fetchone()[0] != 55
                with pytest.raises(sqlite3.IntegrityError):
                    db.execute("UPDATE clubs SET name='FREDAGSKLUBBEN' WHERE id=1")
    finally:
        with connect() as db:
            db.execute("UPDATE clubs SET name=? WHERE id=1", (original,))
            db.execute("UPDATE club_settings SET money_mode=?,point_rate=? WHERE id=1", settings)


def test_only_the_server_admin_can_set_ai_import_and_notes(monkeypatch, tmp_path):
    from whistapp import vision_import
    monkeypatch.setattr(vision_import, "DATA", tmp_path)
    monkeypatch.setenv("WHIST_LLM_BASE_URL", "https://llm.example/v1")
    monkeypatch.setenv("WHIST_LLM_MODEL", "vision-model")
    with TestClient(app) as admin:
        admin.post("/login", data={"username": "admin", "password": "a$$word"})
        member_id, temporary = _new_login(admin, "aiclubadmin", "AI Klubadmin")
        with connect() as db:
            db.execute("UPDATE club_members SET is_club_admin=1 WHERE id=?", (member_id,))
        try:
            with TestClient(app) as club_admin:
                _activate(club_admin, "aiclubadmin", temporary)
                page = club_admin.get("/club")
                assert page.status_code == 200 and 'name="ai_notes"' not in page.text
                assert club_admin.get("/admin/ai").status_code == 403
                refused = club_admin.post("/admin/ai/clubs/1", data={"allow_ai_import": "true", "ai_notes": "Hemmeligt"},
                                          follow_redirects=False)
                assert refused.status_code == 403
                with connect() as db:
                    assert tuple(db.execute("SELECT allow_ai_import,ai_notes FROM club_settings WHERE id=1").fetchone()) == (0, "")

            allowed = admin.post("/admin/ai/clubs/1", data={"allow_ai_import": "true", "ai_notes": " JF = Jens Fup\r\n"},
                                 follow_redirects=False)
            assert allowed.status_code == 303
            with connect() as db:
                assert tuple(db.execute("SELECT allow_ai_import,ai_notes FROM club_settings WHERE id=1").fetchone()) == (1, "JF = Jens Fup")
            assert "JF = Jens Fup</textarea>" in admin.get("/admin/ai").text
            assert "AI-import er slået til for klubben" in admin.get("/club").text
            assert admin.post("/admin/ai/clubs/9999", data={}).status_code == 404
        finally:
            with connect() as db:
                db.execute("UPDATE club_settings SET allow_ai_import=0, ai_notes='' WHERE id=1")
                db.execute("UPDATE club_members SET is_club_admin=0, status='paused' WHERE id=?", (member_id,))
