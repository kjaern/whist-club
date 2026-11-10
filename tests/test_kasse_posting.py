"""Klubkasse: Bogført, Afventer and I alt; posting nights once after their reopen window; the
historical migration; unknown entry types; CSRF tokens."""

import re
import threading
from datetime import datetime, timedelta, timezone
from fractions import Fraction
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import whistapp
import whistapp.database as database
from whistapp import migrations
from whistapp.database import connect, init_db
from whistapp.kasse import ledger
from whistapp.kasse.base import UnknownEntryType
from whistapp.main import app
from conftest import NO_CSRF, session_csrf
import test_kasse
from test_kasse import _closed_night, _version

# The Klubkasse fixtures of test_kasse.py (a test club, the admin in it, a kasserer and a member).
club, admin_in, logins = test_kasse.club, test_kasse.admin_in, test_kasse.logins

NOW = datetime.now(timezone.utc)


def _open_night(db, club_id, members, scores=(6, 2, -3, -5), title="Åben aften"):
    """An open club night with one hand; placements [0,25,75,115] charge 0, 25, 75 and 115."""
    version = _version(db, club_id, members)
    sid = db.execute("INSERT INTO sessions(title,club_id,is_club,rule_version_id) VALUES (?,?,1,?)",
                     (title, club_id, version)).lastrowid
    players = [db.execute("INSERT INTO players(session_id,name,member_id,seat_order) VALUES (?,?,?,?)",
                          (sid, f"P{i}", m, i)).lastrowid for i, m in enumerate(members)]
    hid = db.execute("""INSERT INTO hands(session_id,bid_key,bid_label,target,rules_snapshot)
                        VALUES (?,'almindelige','Almindelige',8,'{}')""", (sid,)).lastrowid
    for pid, score in zip(players, scores):
        db.execute("INSERT INTO hand_scores VALUES (?,?,?,?)", (hid, pid, score, 1))
    return sid


def _row(db, club_id, mid):
    return ledger.member_row(ledger.balances(db, club_id), mid)


def _parts(db, club_id, mid):
    row = _row(db, club_id, mid)
    return row["posted"], row["pending"], row["balance"]


def _ended_hours_ago(db, sid, hours):
    db.execute("UPDATE sessions SET ended_at=datetime('now',?) WHERE id=?", (f"-{hours} hours", sid))


def _postings(db, sid):
    return db.execute("SELECT COUNT(*) FROM kasse_night_postings WHERE session_id=?", (sid,)).fetchone()[0]


def _started(db, club_id):
    ledger.set_start(db, club_id, "", Fraction(0))


# 1. An open night counts only in Afventer, and follows its hands -----------------

def test_an_open_night_counts_only_in_afventer(club):
    club_id, members = club
    with connect() as db:
        _started(db, club_id)
        sid = _open_night(db, club_id, members)
        assert _parts(db, club_id, members[3]) == (0, -115, -115)
        assert _row(db, club_id, members[3])["pending_nights"][0]["id"] == sid
        assert ledger.finalize_due(db, club_id, now=NOW + timedelta(days=30)) == []
        # Another hand changes the pending amount: now members[0] is last.
        players = [r[0] for r in db.execute("SELECT id FROM players WHERE session_id=? ORDER BY seat_order", (sid,))]
        hid = db.execute("""INSERT INTO hands(session_id,bid_key,bid_label,target,rules_snapshot)
            VALUES (?,'almindelige','Almindelige',8,'{}')""", (sid,)).lastrowid
        for pid, score in zip(players, (-20, 0, 0, 20)):
            db.execute("INSERT INTO hand_scores VALUES (?,?,?,?)", (hid, pid, score, 1))
        assert _parts(db, club_id, members[0]) == (0, -115, -115)
        assert ledger.balances(db, club_id)["cash"] == 0


def test_an_open_night_with_a_guest_charges_nothing(club):
    club_id, members = club
    with connect() as db:
        sid = _open_night(db, club_id, members)
        db.execute("UPDATE players SET member_id=NULL WHERE session_id=? AND seat_order=0", (sid,))
        assert ledger.balances(db, club_id)["pending_nights"] == []


# 2-6, 9. Closing, reopening, closing again and posting, through the routes --------

def test_the_lifecycle_of_a_night_in_the_kasse(admin_in):
    admin, club_id, members = admin_in
    with connect() as db:
        _started(db, club_id)
        sid = _open_night(db, club_id, members)
    # B. Closed: still Afventer, nothing posted.
    assert admin.post(f"/sessions/{sid}/end", data={"collect_club_money": "true"}, follow_redirects=False).status_code == 303
    with connect() as db:
        assert ledger.finalize_due(db, club_id) == [] and _postings(db, sid) == 0
        assert _parts(db, club_id, members[3]) == (0, -115, -115)
        _ended_hours_ago(db, sid, 11)
    # C. Reopened within the window: the old deadline is gone.
    assert admin.post(f"/sessions/{sid}/reopen", follow_redirects=False).status_code == 303
    with connect() as db:
        # 5. Past the old deadline, the open night is not posted.
        assert ledger.finalize_due(db, club_id, now=NOW + timedelta(hours=2)) == []
        assert _parts(db, club_id, members[3]) == (0, -115, -115)
    # D. Closed again: a new window from this closing.
    assert admin.post(f"/sessions/{sid}/end", data={"collect_club_money": "true"}, follow_redirects=False).status_code == 303
    with connect() as db:
        assert ledger.finalize_due(db, club_id, now=NOW + timedelta(hours=11)) == []
        before = {m: _parts(db, club_id, m)[2] for m in members}
        # E. Twelve hours after the latest closing: posted once, moved from Afventer to Bogført.
        db.execute("BEGIN IMMEDIATE")
        assert ledger.finalize_due(db, club_id, now=NOW + timedelta(hours=12, minutes=1)) == [sid]
        assert _parts(db, club_id, members[3]) == (-115, 0, -115)
        assert {m: _parts(db, club_id, m)[2] for m in members} == before
        # 7. Repeating it posts nothing more.
        assert ledger.finalize_due(db, club_id, now=NOW + timedelta(days=3)) == []
        assert _postings(db, sid) == 1
        assert db.execute("""SELECT COUNT(*) FROM kasse_night_charges c JOIN kasse_night_postings p ON p.id=c.posting_id
            WHERE p.session_id=?""", (sid,)).fetchone()[0] == 3  # members[0] was charged 0
        posting = db.execute("SELECT * FROM kasse_night_postings WHERE session_id=?", (sid,)).fetchone()
        assert posting["club_id"] == club_id and posting["source"] == "spilaften" and posting["closed_at"]
        assert "bogførte spilaftenen" in ledger.log_lines(db, club_id)[0]["text"]
        assert ledger.log_rows(db, club_id)[0]["actor_username"] == "system"


def test_a_posted_night_does_not_change_when_its_data_changes(club):
    club_id, members = club
    with connect() as db:
        _started(db, club_id)
        sid = _closed_night(db, club_id, members, [0, 25, 75, 115])
        _ended_hours_ago(db, sid, 13)
        assert ledger.finalize_due(db, club_id) == [sid]
        db.execute("UPDATE club_member_settlements SET amount_numerator=999 WHERE session_id=?", (sid,))
        db.execute("DELETE FROM sessions WHERE id=?", (sid,))
        assert _parts(db, club_id, members[3]) == (-115, 0, -115)
        posting = db.execute("SELECT session_id,deleted_session_id FROM kasse_night_postings WHERE club_id=?",
                             (club_id,)).fetchone()
        # A later night that reuses the id is a night of its own.
        assert tuple(posting) == (None, sid)


def test_nothing_is_posted_before_the_kasse_start_is_saved(club):
    club_id, members = club
    with connect() as db:
        sid = _closed_night(db, club_id, members, [0, 25, 75, 115], created="2025-06-01 18:00:00")
        _ended_hours_ago(db, sid, 48)
        assert ledger.finalize_due(db, club_id) == []
        assert _parts(db, club_id, members[3]) == (0, -115, -115)
        # The start date leaves the night out, so it must not have been posted.
        ledger.set_start(db, club_id, "2026-01-01", Fraction(0))
        assert ledger.finalize_due(db, club_id) == [] and _parts(db, club_id, members[3]) == (0, 0, 0)


def test_an_imported_night_waits_from_when_it_was_saved(club):
    club_id, members = club
    with connect() as db:
        _started(db, club_id)
        sid = _closed_night(db, club_id, members, [0, 25, 75, 115], origin="imported", created="2026-09-01 20:00:00")
        # Saved before saved_at existed: the kasse does not guess, the night afventer.
        assert ledger.finalize_due(db, club_id, now=NOW + timedelta(days=30)) == []
        db.execute("UPDATE sessions SET saved_at=datetime('now','-1 hours') WHERE id=?", (sid,))
        assert ledger.finalize_due(db, club_id) == []
        assert ledger.finalize_due(db, club_id, now=NOW + timedelta(hours=12)) == [sid]


def test_saving_an_import_stamps_saved_at(admin_in):
    admin, club_id, members = admin_in
    with connect() as db:
        sid = _closed_night(db, club_id, members, [0, 25, 75, 115], origin="imported")
    admin.post(f"/sessions/{sid}/money", data={}, follow_redirects=False)
    with connect() as db:
        assert db.execute("SELECT saved_at FROM sessions WHERE id=?", (sid,)).fetchone()[0]


# 8. Two requests at once post a night once ----------------------------------------

def test_concurrent_finalization_posts_once(club):
    club_id, members = club
    with connect() as db:
        _started(db, club_id)
        sid = _closed_night(db, club_id, members, [0, 25, 75, 115])
        _ended_hours_ago(db, sid, 13)
    barrier, results, errors = threading.Barrier(4), [], []

    def worker():
        try:
            barrier.wait()
            with connect() as db:
                db.execute("BEGIN IMMEDIATE")
                results.append(ledger.finalize_due(db, club_id))
        except Exception as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == [] and sorted(results) == [[], [], [], [sid]]
    with connect() as db:
        assert _postings(db, sid) == 1 and _parts(db, club_id, members[3]) == (-115, 0, -115)


# 10-12. Cash, payments, reversals and corrections ------------------------------------

def test_posting_never_changes_the_cash_and_entries_keep_their_meaning(club):
    club_id, members = club
    anne, bent = members[0], members[3]
    with connect() as db:
        ledger.set_start(db, club_id, "", Fraction(500))
        sid = _closed_night(db, club_id, members, [0, 25, 75, 115])
        ledger.add_entry(db, club_id, "indbetaling", Fraction(100), member_id=bent)
        assert ledger.balances(db, club_id)["cash"] == 600
        assert _parts(db, club_id, bent) == (100, -115, -15)
        _ended_hours_ago(db, sid, 13)
        ledger.finalize_due(db, club_id)
        assert ledger.balances(db, club_id)["cash"] == 600
        assert _parts(db, club_id, bent) == (-15, 0, -15)
        ledger.add_entry(db, club_id, "indbetaling", Fraction(15), member_id=bent)
        assert _parts(db, club_id, bent) == (0, 0, 0) and ledger.balances(db, club_id)["cash"] == 615
        eid = ledger.add_entry(db, club_id, "korrektion", Fraction(-10), member_id=anne, note="Fejl i aften")
        assert _parts(db, club_id, anne)[0] == -10 and ledger.balances(db, club_id)["cash"] == 615
        ledger.reverse_entry(db, club_id, eid)
        assert _parts(db, club_id, anne)[0] == 0
        ledger.add_entry(db, club_id, "udbetaling", Fraction(5), member_id=anne)
        assert ledger.balances(db, club_id)["cash"] == 610 and _parts(db, club_id, anne)[0] == -5


# 13-14. The historical migration ------------------------------------------------------

@pytest.fixture
def history_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "data" / "whist.sqlite3")
    init_db()
    with connect() as db:
        club_id = 1
        members = [db.execute("INSERT INTO club_members(name,status,club_id) VALUES (?,'active',1)",
                              (f"Historisk {i}",)).lastrowid for i in range(4)]
        final = _closed_night(db, club_id, members, [0, 25, 75, 115])
        _ended_hours_ago(db, final, 72)
        recent = _closed_night(db, club_id, members, [0, 10, 20, 30])
        _ended_hours_ago(db, recent, 2)
        imported = _closed_night(db, club_id, members, [0, 1, 2, 3], origin="imported", created="2024-03-05")
        unknown = _closed_night(db, club_id, members, [0, 0, 0, 7], origin="imported", created="ukendt")
        db.execute("UPDATE sessions SET ended_at='ukendt' WHERE id=?", (unknown,))
        ledger.add_entry(db, club_id, "indbetaling", Fraction(50), member_id=members[3])
        eid = ledger.add_entry(db, club_id, "korrektion", Fraction(5), member_id=members[2], note="Start")
        ledger.reverse_entry(db, club_id, eid)
        ledger.set_start(db, club_id, "", Fraction(0))
    return members, dict(final=final, recent=recent, imported=imported, unknown=unknown)


def test_the_migration_posts_final_nights_and_keeps_every_total(history_db):
    members, nights = history_db
    # The old saldo: every closed night with Klubkasse counted at once, plus the entries.
    old = {members[0]: 0, members[1]: -36, members[2]: -97, members[3]: -155 + 50}
    with connect() as db:
        assert {m: _parts(db, 1, m)[2] for m in members} == old
    assert "kasse-post-history: needed" in migrations.status()
    result = migrations.run("kasse-post-history")
    assert "posted 2 nights" in result and f"aften #{nights['unknown']}" in result
    with connect() as db:
        assert {m: _parts(db, 1, m)[2] for m in members} == old
        assert _postings(db, nights["final"]) == _postings(db, nights["imported"]) == 1
        # Not yet final, or not classifiable: still Afventer, never guessed.
        assert _postings(db, nights["recent"]) == _postings(db, nights["unknown"]) == 0
        assert _parts(db, 1, members[3]) == (-115 - 3 + 50, -30 - 7, -105)
        assert db.execute("SELECT source FROM kasse_night_postings WHERE session_id=?",
                          (nights["imported"],)).fetchone()[0] == "historisk"
        assert ledger.balances(db, 1)["cash"] == 50
    # Running it again posts nothing twice; the night a person must look at keeps it needed and is listed again.
    assert "kasse-post-history: needed" in migrations.status()
    again = migrations.run("kasse-post-history")
    assert "posted 0 nights" in again and f"aften #{nights['unknown']}" in again
    with connect() as db:
        assert {m: _parts(db, 1, m)[2] for m in members} == old
        db.execute("DELETE FROM sessions WHERE id=?", (nights["unknown"],))
    # Once it is dealt with, the migration is done.
    assert "already done" in migrations.run("kasse-post-history")


def test_starting_the_app_posts_no_history(history_db):
    _, nights = history_db
    init_db()
    with connect() as db:
        assert _postings(db, nights["imported"]) == 0


# 15. Unknown entry types fail loudly --------------------------------------------------

def test_an_unknown_entry_type_is_never_counted_as_nothing(admin_in):
    admin, club_id, members = admin_in
    with connect() as db:
        db.execute("""INSERT INTO kasse_entries(club_id,kind,member_id,amount_numerator,amount_denominator,entry_date)
            VALUES (?,'gave',?,100,1,'2026-11-01')""", (club_id, members[0]))
        with pytest.raises(UnknownEntryType, match="gave"):
            ledger.balances(db, club_id)
        with pytest.raises(UnknownEntryType):
            ledger.buckets(db, club_id)
    page = admin.get("/kasse")
    # Explicit, in Danish, without internal details such as the entry's kind.
    assert page.status_code == 500 and "Klubkassen kan ikke vises" in page.text and "gave" not in page.text
    home = admin.get("/")
    assert home.status_code == 200 and "Klubkassen kan ikke vises lige nu" in home.text


# 16. CSRF -----------------------------------------------------------------------------

def test_every_post_form_renders_the_csrf_field():
    templates = Path(whistapp.__file__).parent / "templates"
    form = re.compile(r'<form\b[^>]*\bmethod="post"[^>]*>(.{0,40})', re.I | re.S)
    missing = [(p.name, m.group(0)[:60]) for p in templates.rglob("*.html")
               for m in form.finditer(p.read_text(encoding="utf-8"))
               if 'action="/login"' not in m.group(0) and "csrf_field(request)" not in m.group(1)]
    assert missing == []


def test_csrf_tokens_are_required_for_changes(logins):
    _, kasserer, member, _, member_id = logins
    page = kasserer.get("/kasse").text
    token = session_csrf(kasserer)
    assert f'name="csrf_token" value="{token}"' in page and f'<meta name="csrf-token" content="{token}">' in page
    assert len(token) >= 32 and token not in kasserer.get("/kasse").url.query.decode()
    # Valid: as a form field (urlencoded and multipart), or as the htmx/fetch header.
    assert kasserer.post("/kasse/buckets", data={"name": "Felt", "csrf_token": token}, headers={NO_CSRF: "1"},
                         follow_redirects=False).status_code == 303
    assert kasserer.post("/kasse/buckets", data={"name": "Multipart", "csrf_token": token}, headers={NO_CSRF: "1"},
                         files={"ignored": ("x.txt", b"x")}, follow_redirects=False).status_code == 303
    assert kasserer.post("/kasse/entries", data={"kind": "indbetaling", "member_id": member_id, "amount": "5",
                                                 "csrf_token": token}, headers={NO_CSRF: "1"},
                         follow_redirects=False).status_code == 303
    assert kasserer.post("/kasse/buckets", data={"name": "Header"}, headers={"X-CSRF-Token": token},
                         follow_redirects=False).status_code == 303
    # Missing, wrong, or another session's token: refused before anything is written.
    other = session_csrf(member)
    for headers, data in (({NO_CSRF: "1"}, {}), ({"X-CSRF-Token": "forkert"}, {}),
                          ({NO_CSRF: "1"}, {"csrf_token": "forkert"}), ({"X-CSRF-Token": other}, {})):
        response = kasserer.post("/kasse/buckets", data={"name": "Afvist", **data}, headers=headers)
        assert response.status_code == 403 and "Sikkerhedsnøglen" in response.text
    with connect() as db:
        names = [r[0] for r in db.execute("SELECT name FROM kasse_buckets WHERE club_id=1")]
    assert "Afvist" not in names and {"Felt", "Multipart", "Header"} <= set(names)


def test_logging_in_gives_a_new_token_and_login_needs_none():
    with TestClient(app) as client:
        assert client.post("/login", data={"username": "admin", "password": "a$$word"}, headers={NO_CSRF: "1"},
                           follow_redirects=False).status_code == 303
        first = session_csrf(client)
        client.post("/logout", headers={NO_CSRF: "1"})
        client.post("/login", data={"username": "admin", "password": "a$$word"}, headers={NO_CSRF: "1"})
        assert first and session_csrf(client) and session_csrf(client) != first


# 17-18. Rights and clubs ---------------------------------------------------------------

def test_members_see_their_own_three_figures_and_cannot_book(logins):
    _, kasserer, member, kasserer_id, member_id = logins
    with connect() as db:
        others = [db.execute("INSERT INTO club_members(name,status,club_id) VALUES (?,'paused',1)",
                             (f"Medspiller {i} {member_id}",)).lastrowid for i in range(2)]
        sid = _open_night(db, 1, [kasserer_id, others[0], others[1], member_id], title="Torsdag")
    try:
        page = member.get("/kasse").text
        assert "<h2>Min saldo</h2>" in page and "Bogført" in page and "Afventer" in page and "I alt" in page
        assert f'<a href="/sessions/{sid}">Torsdag' in page and "-115 kr" in page
        assert "afventer -115 kr" in member.get("/").text
        assert member.post("/kasse/entries", data={"kind": "indbetaling", "member_id": member_id,
                                                   "amount": "5"}).status_code == 403
        assert member.post("/kasse/buckets", data={"name": "Nej"}).status_code == 403
    finally:
        with connect() as db:
            db.execute("DELETE FROM sessions WHERE id=?", (sid,))
            db.executemany("DELETE FROM club_members WHERE id=?", [(m,) for m in others])


# Review fixes -----------------------------------------------------------------------------

def test_a_broken_night_is_skipped_and_the_others_still_post(admin_in):
    admin, club_id, members = admin_in
    with connect() as db:
        _started(db, club_id)
        good = _closed_night(db, club_id, members, [0, 25, 75, 115])
        broken = _closed_night(db, club_id, members, [0, 1, 2, 3])
        db.execute("UPDATE sessions SET rule_version_id=NULL WHERE id=?", (broken,))
        _ended_hours_ago(db, good, 13)
        _ended_hours_ago(db, broken, 13)
    # The pages post what they can and never fail because of the broken night.
    assert admin.get("/kasse").status_code == 200 and admin.get("/").status_code == 200
    with connect() as db:
        assert _postings(db, good) == 1 and _postings(db, broken) == 0
        # No half-written posting: no charges and no log line for the broken night.
        assert db.execute("SELECT COUNT(*) FROM kasse_night_charges WHERE club_id=?", (club_id,)).fetchone()[0] == 3
        assert db.execute("SELECT COUNT(*) FROM kasse_log WHERE club_id=? AND entity='night' AND entity_id=?",
                          (club_id, broken)).fetchone()[0] == 0
        assert [n["id"] for n in ledger.balances(db, club_id)["pending_nights"] if n["error"]] == [broken]
        db.execute("BEGIN IMMEDIATE")
        assert ledger.finalize_due(db, club_id) == []
    # Posting outside a transaction is refused rather than committed early.
    with connect() as db:
        with pytest.raises(RuntimeError, match="BEGIN IMMEDIATE"):
            ledger.finalize_due(db, club_id)


def test_the_migration_lists_a_history_night_it_cannot_work_out(history_db):
    members, nights = history_db
    with connect() as db:
        db.execute("UPDATE sessions SET rule_version_id=NULL WHERE id=?", (nights["imported"],))
    result = migrations.run("kasse-post-history")
    assert "posted 1 nights" in result and f"aften #{nights['imported']}" in result
    with connect() as db:
        assert _postings(db, nights["imported"]) == 0
    # It stays needed while the broken night waits, and every run lists it.
    assert "kasse-post-history: needed" in migrations.status()
    assert f"aften #{nights['imported']}" in migrations.run("kasse-post-history")
    with connect() as db:
        db.execute("DELETE FROM sessions WHERE id IN (?,?)", (nights["imported"], nights["unknown"]))
    assert "kasse-post-history: done" in migrations.status()


def test_a_delete_alert_after_an_id_was_reused_is_not_posted(club):
    club_id, members = club
    with connect() as db:
        _started(db, club_id)
        old = _closed_night(db, club_id, members, [0, 25, 75, 115], origin="imported")
        db.execute("UPDATE sessions SET saved_at=datetime('now','-13 hours') WHERE id=?", (old,))
        assert ledger.finalize_due(db, club_id) == [old]
        with ledger.watch(db, old, "delete"):
            db.execute("DELETE FROM sessions WHERE id=?", (old,))
        new = _closed_night(db, club_id, members, [0, 1, 2, 3], origin="imported")
        assert new == old  # SQLite reused the id
        with ledger.watch(db, new, "delete"):
            db.execute("DELETE FROM sessions WHERE id=?", (new,))
        first, second = sorted((a for a in ledger.alerts(db, club_id) if a["kind"] == "delete"), key=lambda a: a["id"])
        assert first["posted"] and not second["posted"]


def test_an_alert_about_a_night_that_reuses_a_posted_id_is_not_posted(club):
    club_id, members = club
    with connect() as db:
        _started(db, club_id)
        old = _closed_night(db, club_id, members, [0, 25, 75, 115])
        _ended_hours_ago(db, old, 13)
        assert ledger.finalize_due(db, club_id) == [old]
        with ledger.watch(db, old, "delete"):
            db.execute("DELETE FROM sessions WHERE id=?", (old,))
        new = _closed_night(db, club_id, members, [0, 1, 2, 3])
        assert new == old  # SQLite reused the id
        ledger.add_alert(db, new, "reopen", ledger.snapshot(db, new), None)
        posted = {a["kind"]: a["posted"] for a in ledger.alerts(db, club_id)}
        # The deleted night was posted; the new night with the same id was not.
        assert posted == {"delete": True, "reopen": False}


def test_the_history_migration_is_needed_only_for_imported_history(history_db):
    from whistapp.migrations import kasse_post_history
    _, nights = history_db
    with connect() as db:
        assert kasse_post_history.needed(db)
        # An unclassifiable night needs a person, so it keeps the migration needed.
        db.execute("UPDATE sessions SET saved_at=CURRENT_TIMESTAMP WHERE id=?", (nights["imported"],))
        assert kasse_post_history.needed(db)
        # Only a due live night is left, which the app posts by itself.
        db.execute("DELETE FROM sessions WHERE id=?", (nights["unknown"],))
        assert ledger.due_nights(db, 1) and not kasse_post_history.needed(db)
    assert "kasse-post-history: done" in migrations.status()


def test_a_live_night_with_an_unreadable_closing_time_is_explained(admin_in):
    admin, club_id, members = admin_in
    with connect() as db:
        _started(db, club_id)
        sid = _closed_night(db, club_id, members, [0, 25, 75, 115])
        db.execute("UPDATE sessions SET ended_at='ukendt' WHERE id=?", (sid,))
    page = admin.get("/kasse").text
    assert "tidspunktet for afslutningen kan ikke læses" in page and "en serveradministrator skal se på aftenen" in page
    assert "kasse-post-history" not in page


def test_an_open_night_started_long_ago_is_flagged(admin_in):
    admin, club_id, members = admin_in
    with connect() as db:
        _started(db, club_id)
        fresh = _open_night(db, club_id, members, title="Frisk")
        old = _open_night(db, club_id, members, title="Glemt")
        db.execute("UPDATE sessions SET created_at=datetime('now','-2 days') WHERE id=?", (old,))
        pending = {n["id"]: n for n in ledger.balances(db, club_id)["pending_nights"]}
        assert pending[old]["stale"] and not pending[fresh]["stale"]
        assert ledger.member_row(ledger.balances(db, club_id), members[3])["pending_nights"][1]["stale"]
        date = pending[old]["date"]
    page = admin.get("/kasse").text
    assert page.count(f"åben siden {date} – mangler den at blive afsluttet?") == 1


def test_posted_nights_sum_their_charges_as_fractions(club):
    club_id, members = club
    with connect() as db:
        _started(db, club_id)
        first = _closed_night(db, club_id, members, ["1/3", "1/3", "1/3", 0], penalty_member=members[3])
        nothing = _closed_night(db, club_id, members, [0, 0, 0, 0])
        _ended_hours_ago(db, first, 13)
        _ended_hours_ago(db, nothing, 13)
        assert ledger.finalize_due(db, club_id) == [first, nothing]
        # Newest first; a posting without charges totals 0.
        totals = [(p["session_id"], p["total"]) for p in ledger.posted_nights(db, club_id)]
        assert totals == [(nothing, Fraction(0)), (first, Fraction(41))]
        assert [p["session_id"] for p in ledger.posted_nights(db, club_id, limit=1)] == [nothing]


def test_posting_belongs_to_one_club(club):
    club_id, members = club
    with connect() as db:
        _started(db, club_id)
        sid = _closed_night(db, club_id, members, [0, 25, 75, 115])
        _ended_hours_ago(db, sid, 13)
        # Another club's posting and balances never see this club's night.
        assert sid not in [n["id"] for n in ledger.due_nights(db, 1)]
        ledger.finalize_due(db, 1)
        assert _postings(db, sid) == 0
        assert sid not in [n["id"] for n in ledger.balances(db, 1)["pending_nights"]]
        assert ledger.finalize_due(db, club_id) == [sid]
        assert db.execute("SELECT club_id FROM kasse_night_charges WHERE posting_id=(SELECT id FROM kasse_night_postings "
                          "WHERE session_id=?) GROUP BY club_id", (sid,)).fetchall()[0][0] == club_id
