"""Rule versions for members to read: the history, one version, a night's rules, and import's choice."""
import json

import pytest
from fastapi.testclient import TestClient

from whistapp.database import connect, init_db
from whistapp.main import app
from whistapp.rule_versions import save_version
from test_clubs import _activate, _new_login

RATES = [100, 200, 300, 400]


@pytest.fixture(autouse=True)
def _shared_home():
    init_db()


@pytest.fixture
def club():
    """A club of its own with four members and two versions: 2020 and, with Fejl40 50 and D paused, 2025."""
    with connect() as db:
        club_id = db.execute("INSERT INTO clubs(name) VALUES ('Regelklubben')").lastrowid
        db.execute("INSERT INTO club_settings(id,placements,fejl40) VALUES (?,?,40)", (club_id, json.dumps(RATES)))
        members = [db.execute("INSERT INTO club_members(name,status,club_id) VALUES (?,'active',?)",
                              (name, club_id)).lastrowid for name in ("Anna", "Bo", "Cille", "Dan")]
        first = save_version(db, club_id)
        db.execute("UPDATE club_settings SET fejl40=50,placements=? WHERE id=?", (json.dumps(RATES[:3]), club_id))
        db.execute("UPDATE club_members SET status='paused' WHERE id=?", (members[3],))
        second = save_version(db, club_id)
        db.execute("UPDATE club_rule_versions SET created_at='2020-01-01 12:00:00' WHERE id=?", (first,))
        db.execute("UPDATE club_rule_versions SET created_at='2025-01-01 12:00:00' WHERE id=?", (second,))
    with TestClient(app) as admin:
        admin.post("/login", data={"username": "admin", "password": "a$$word"})
        admin.post("/clubs/select", data={"club_id": club_id})
        yield dict(id=club_id, members=members, first=first, second=second, admin=admin)
    with connect() as db:
        db.execute("DELETE FROM sessions WHERE club_id=?", (club_id,))
        db.execute("DELETE FROM club_rule_versions WHERE club_id=?", (club_id,))
        accounts = [r[0] for r in db.execute("""SELECT ma.user_id FROM member_accounts ma
            JOIN club_members cm ON cm.id=ma.member_id WHERE cm.club_id=?""", (club_id,))]
        db.execute("DELETE FROM member_accounts WHERE member_id IN (SELECT id FROM club_members WHERE club_id=?)", (club_id,))
        db.executemany("DELETE FROM user_accounts WHERE id=?", [(a,) for a in accounts])
        db.execute("DELETE FROM club_members WHERE club_id=?", (club_id,))
        db.execute("DELETE FROM club_settings WHERE id=?", (club_id,))
        db.execute("DELETE FROM clubs WHERE id=?", (club_id,))


def _night(club, version):
    with connect() as db:
        sid = db.execute("INSERT INTO sessions(title,club_id,rule_version_id) VALUES ('Gammel aften',?,?)",
                         (club["id"], version)).lastrowid
    return sid


def test_history_says_what_each_version_changed(club):
    page = club["admin"].get("/rules").text
    assert "Version 2 · fra 1. januar 2025" in page and "Version 1 · fra 1. januar 2020" in page
    assert "Fejl40 40 → 50 kr" in page and "Pladsbetaling 100, 200, 300, 400 → 100, 200, 300 kr" in page
    assert "Dan gik af medlemslisten" in page and "Første version" in page
    assert page.index("Version 2") < page.index("Version 1")  # newest first


def test_a_night_shows_its_own_rules_after_the_club_changed_them(club):
    sid = _night(club, club["first"])
    page = club["admin"].get(f"/sessions/{sid}/rules").text
    assert "Regler for aftenen" in page and "Version 1 · fra 1. januar 2020" in page
    assert "Fejl40: 40 kr" in page and "<td>400 kr</td>" in page and "<li>Dan</li>" in page
    assert 'href="/rules/2"' in page  # points on to the club's current rules
    # A renamed member shows under the new name; the version itself is unchanged.
    with connect() as db:
        db.execute("UPDATE club_members SET name='Daniel' WHERE id=?", (club["members"][3],))
    assert "<li>Daniel</li>" in club["admin"].get(f"/sessions/{sid}/rules").text
    assert f"/sessions/{sid}/rules" in club["admin"].get(f"/sessions/{sid}/live").text
    assert club["admin"].get("/rules/99").status_code == 404


def test_the_new_night_form_names_the_current_rules(club):
    page = club["admin"].get("/").text
    assert "Spilles efter klubbens regler" in page and 'href="/rules/2">Version 2 · fra 1. januar 2025' in page


def test_short_names_and_member_names_make_no_new_version(club):
    with connect() as db:
        settings = json.loads(db.execute("SELECT bid_settings FROM club_settings WHERE id=?", (club["id"],)).fetchone()[0] or "{}")
        settings.setdefault("sol", {})["short"] = "S"
        db.execute("UPDATE club_settings SET bid_settings=? WHERE id=?", (json.dumps(settings), club["id"]))
        db.execute("UPDATE club_members SET name='Annabel' WHERE id=?", (club["members"][0],))
        assert save_version(db, club["id"]) == club["second"]


def test_members_can_read_the_rules(club):
    member_id, temporary = _new_login(club["admin"], "rulereader", "Læser", club["id"])
    sid = _night(club, club["first"])
    with TestClient(app) as member:
        _activate(member, "rulereader", temporary)
        for path in ("/rules", "/rules/1", f"/sessions/{sid}/rules"):
            assert member.get(path).status_code == 200, path


def _final_import(admin, **fields):
    form = {"title": "Glemt aften", "date_precision": "day", "game_date": "2024-06-01",
            **{f"name_{i}": n for i, n in enumerate(("W", "X", "Y", "Z"))},
            **{f"score_{i}": s for i, s in enumerate(("10", "-10", "5", "-5"))}}
    form.update(fields)
    return admin.post("/import/final", data=form, follow_redirects=False)


def _version_of_newest_night(club):
    with connect() as db:
        return db.execute("SELECT rule_version_id FROM sessions WHERE club_id=? ORDER BY id DESC LIMIT 1",
                          (club["id"],)).fetchone()[0]


def test_import_suggests_the_current_rules_and_any_version_can_be_chosen(club):
    page = club["admin"].get("/import").text
    current = f'<option value="{club["second"]}" selected>Version 2 · fra 1. januar 2025 (nuværende)</option>'
    assert current in page and f'<option value="{club["first"]}" >Version 1' in page
    # Without a choice the night gets the current rules, whatever its date.
    assert _final_import(club["admin"]).status_code == 303
    assert _version_of_newest_night(club) == club["second"]
    _final_import(club["admin"], game_date="", date_precision="unknown")
    assert _version_of_newest_night(club) == club["second"]
    _final_import(club["admin"], rule_version=str(club["first"]))
    assert _version_of_newest_night(club) == club["first"]
    with connect() as db:
        other = db.execute("SELECT id FROM club_rule_versions WHERE club_id=1 LIMIT 1").fetchone()[0]
    assert _final_import(club["admin"], rule_version=str(other)).status_code == 400


def test_opening_pages_never_makes_a_version(club):
    """A version is made when rules are saved or a night is created, never by viewing a page,
    even if the club's settings have changed outside Klubadministration."""
    def versions():
        with connect() as db:
            return db.execute("SELECT COUNT(*) FROM club_rule_versions WHERE club_id=?", (club["id"],)).fetchone()[0]
    before = versions()
    with connect() as db:
        db.execute("UPDATE club_settings SET fejl40=60 WHERE id=?", (club["id"],))
    for path in ("/", "/rules", "/rules/2", "/import"):
        assert club["admin"].get(path).status_code == 200, path
    assert versions() == before
    assert "Version 2 · fra 1. januar 2025" in club["admin"].get("/").text
