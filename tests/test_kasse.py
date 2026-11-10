"""Klubkasse: entry types, balances from nights and entries, buckets, the kasserer's rights and alerts."""

import itertools
import json
from datetime import datetime, timedelta, timezone
from fractions import Fraction

import pytest
from fastapi.testclient import TestClient

from whistapp.database import connect, init_db
from whistapp.kasse import ledger
from whistapp.kasse.base import parse_amount
from whistapp.kasse.registry import ENTRY_TYPES, rule_sources
from whistapp.main import app
from test_clubs import _activate, _new_login


def _wipe_kasse(db, club_id):
    for table in ("kasse_night_charges", "kasse_night_postings", "kasse_entries", "kasse_buckets", "kasse_log",
                  "kasse_alerts"):
        db.execute(f"DELETE FROM {table} WHERE club_id=?", (club_id,))
    db.execute("""UPDATE clubs SET kasserer_member_id=NULL,kasse_start=NULL,kasse_start_cash_numerator=0,
        kasse_start_cash_denominator=1,kasse_started_at=NULL WHERE id=?""", (club_id,))


@pytest.fixture
def club():
    """A club of its own with four members; removed afterwards."""
    init_db()
    with connect() as db:
        club_id = db.execute("INSERT INTO clubs(name) VALUES ('Kasseklub')").lastrowid
        members = [db.execute("INSERT INTO club_members(name,status,club_id) VALUES (?,'active',?)",
                              (name, club_id)).lastrowid for name in ("Anne", "Bent", "Carl", "Dorte")]
    yield club_id, members
    with connect() as db:
        _wipe_kasse(db, club_id)
        # SQLite reuses session ids; leave no audit rows behind for a later night with the same id.
        db.execute("DELETE FROM audit_events WHERE session_id IN (SELECT id FROM sessions WHERE club_id=?)", (club_id,))
        db.execute("DELETE FROM sessions WHERE club_id=?", (club_id,))
        db.execute("DELETE FROM audit_events WHERE session_id NOT IN (SELECT id FROM sessions)")
        db.execute("DELETE FROM club_rule_versions WHERE club_id=?", (club_id,))
        db.execute("DELETE FROM club_members WHERE club_id=?", (club_id,))
        db.execute("DELETE FROM clubs WHERE id=?", (club_id,))


def _version(db, club_id, members, money_mode="placement"):
    number = db.execute("SELECT COALESCE(MAX(number),0)+1 FROM club_rule_versions WHERE club_id=?", (club_id,)).fetchone()[0]
    return db.execute("""INSERT INTO club_rule_versions(club_id,number,created_by,placements,fejl40,money_mode,
        point_rate,bid_settings,member_ids) VALUES (?,?,'test','[0,25,75,115]',40,?,'1','{}',?)""",
                      (club_id, number, money_mode, json.dumps(members))).lastrowid


def _closed_night(db, club_id, members, charges, money_mode="placement", created="2026-10-01 18:00:00",
                  penalty_member=None, origin="live"):
    """A closed club night that charged each member the given amount."""
    version = _version(db, club_id, members, money_mode)
    sid = db.execute("""INSERT INTO sessions(title,club_id,created_at,ended_at,is_club,collect_club_money,
        rule_version_id,origin) VALUES ('Aften',?,?,CURRENT_TIMESTAMP,1,1,?,?)""",
                     (club_id, created, version, origin)).lastrowid
    for place, (mid, amount) in enumerate(zip(members, charges), 1):
        amount = Fraction(amount)
        db.execute("""INSERT INTO club_member_settlements(session_id,member_id,place,amount_numerator,amount_denominator)
            VALUES (?,?,?,?,?)""", (sid, mid, place, amount.numerator, amount.denominator))
    if penalty_member:
        pid = db.execute("INSERT INTO players(session_id,name,member_id,seat_order) VALUES (?,'X',?,0)",
                         (sid, penalty_member)).lastrowid
        db.execute("INSERT INTO penalty_payments(session_id,player_id,amount) VALUES (?,?,40)", (sid, pid))
    return sid


def _balance(db, club_id, mid):
    return ledger.member_balance(ledger.balances(db, club_id), mid)


def test_each_entry_type_says_what_it_does():
    a = Fraction(100)
    # A saldo is negative when the member owes the club: paying in raises it.
    assert ENTRY_TYPES["indbetaling"].effect(a, 7, None).members == {7: 100}
    assert ENTRY_TYPES["indbetaling"].effect(a, 7, None).cash == 100
    assert ENTRY_TYPES["udbetaling"].effect(a, 7, None).members == {7: -100}
    assert ENTRY_TYPES["udbetaling"].effect(a, 7, None).cash == -100
    spent = ENTRY_TYPES["udgift"].effect(a, None, 3)
    assert spent.cash == -100 and spent.buckets == {3: 100} and not spent.members
    assert ENTRY_TYPES["korrektion"].effect(-a, 7, None).members == {7: -100}
    assert ENTRY_TYPES["korrektion"].effect(a, None, None).cash == 100
    # Vis regel shows the file the calculation lives in.
    assert "class Udgift" in rule_sources("udgift")[0][1]
    assert "NO_KASSE_MODES" in rule_sources("aftener")[0][1]


def test_amounts_are_fractions_with_at_most_two_decimals():
    assert parse_amount("125,50") == Fraction(251, 2)
    assert parse_amount("1.250,25") == Fraction(125025, 100)
    assert parse_amount("-10", signed=True) == -10
    for bad in ("1,234", "0", "abc", "-5"):
        with pytest.raises(ValueError):
            parse_amount(bad)


def test_balances_add_nights_and_entries(club):
    club_id, (anne, bent, carl, dorte) = club
    with connect() as db:
        _closed_night(db, club_id, [anne, bent, carl, dorte], [0, 25, 75, Fraction(231, 2)], penalty_member=bent)
        # Players settle with each other in this mode: nothing is owed to the club.
        _closed_night(db, club_id, [anne, bent, carl, dorte], [-10, 10, 20, -20], money_mode="score_settle")
        ledger.add_entry(db, club_id, "indbetaling", Fraction(50), member_id=bent)
        ledger.add_entry(db, club_id, "korrektion", Fraction(5), member_id=carl, note="Betalte for meget")
        ledger.add_entry(db, club_id, "korrektion", Fraction(3), note="Kasseoptælling")
        bucket = ledger.add_bucket(db, club_id, "Tur")
        ledger.add_entry(db, club_id, "udgift", Fraction(20), bucket_id=bucket)
        # Owing the club is a negative saldo; Pladsbetaling and Fejl40 are shown apart.
        assert _balance(db, club_id, bent) == -25 - 40 + 50
        assert _balance(db, club_id, carl) == -70
        assert _balance(db, club_id, dorte) == -Fraction(231, 2)
        books = ledger.balances(db, club_id)
        row = next(m for m in books["members"] if m["id"] == bent)
        # Closed just now: the night afventer; the entry is bogført.
        assert (row["nights"], row["fejl40"], row["entries"], row["pending"]) == (0, 0, 50, -65)
        ledger.set_start(db, club_id, "", Fraction(0))
        assert ledger.finalize_due(db, club_id, now=datetime.now(timezone.utc) + timedelta(hours=13))
        books = ledger.balances(db, club_id)
        row = next(m for m in books["members"] if m["id"] == bent)
        assert (row["nights"], row["fejl40"], row["entries"], row["pending"]) == (-25, -40, 50, 0)
        assert books["owed_to_club"] == 15 + 70 + Fraction(231, 2) and books["owed_by_club"] == 0
        assert books["cash"] == 50 + 3 - 20
        assert ledger.buckets(db, club_id)[0]["spent"] == 20
        assert all(isinstance(m["balance"], Fraction) for m in books["members"])


def test_kasse_start_leaves_out_earlier_nights(club):
    club_id, members = club
    with connect() as db:
        _closed_night(db, club_id, members, [0, 25, 75, 115], created="2025-12-31 20:00:00")
        _closed_night(db, club_id, members, [0, 25, 75, 115], created="2026-01-02 20:00:00")
        assert _balance(db, club_id, members[3]) == -230
        ledger.set_start(db, club_id, "2026-01-01", Fraction(2401, 2))
        assert _balance(db, club_id, members[3]) == -115
        # Startbeholdning: the club's cash on the start date.
        books = ledger.balances(db, club_id)
        assert books["cash"] == Fraction(2401, 2) and books["start_cash"] == Fraction(2401, 2)
        assert ledger.log_rows(db, club_id)[0]["entity"] == "kasse_start"
        assert "startbeholdning 1200,5 kr" in ledger.log_lines(db, club_id)[0]["text"]
        # Once saved, the start is locked: a later change would rewrite every saldo.
        with pytest.raises(ValueError, match="låst"):
            ledger.set_start(db, club_id, "2025-01-01", Fraction(0))
        assert ledger.balances(db, club_id)["kasse_start"] == "2026-01-01"


def test_a_bucket_is_renamed_once_the_trip_is_known(club):
    club_id, members = club
    with connect() as db:
        bucket = ledger.add_bucket(db, club_id, "Udlandstur 2027")
        ledger.add_entry(db, club_id, "udgift", Fraction(400), bucket_id=bucket, note="Hotel")
        ledger.add_entry(db, club_id, "indbetaling", Fraction(50), member_id=members[0], note="MobilePay")
        ledger.update_bucket(db, club_id, bucket, "Rom 2027", "Fly og hotel")
        assert ledger.buckets(db, club_id)[0]["name"] == "Rom 2027" and ledger.buckets(db, club_id)[0]["spent"] == 400
        # The ledger and the log show the bucket's current name; the rename itself is logged.
        assert ledger.entries(db, club_id)[1]["bucket_name"] == "Rom 2027"
        log = [line["text"] for line in ledger.log_lines(db, club_id)]
        assert log[0] == "ændrede puljen Rom 2027: navn Udlandstur 2027 → Rom 2027, note – → Fly og hotel"
        assert "Anne, 50 kr (MobilePay)" in log[1] and "Rom 2027, 400 kr (Hotel)" in log[2]


def test_entries_are_validated(club):
    club_id, members = club
    with connect() as db:
        with pytest.raises(ValueError):
            ledger.add_entry(db, club_id, "indbetaling", Fraction(10))  # no member
        with pytest.raises(ValueError):
            ledger.add_entry(db, club_id, "korrektion", Fraction(10), member_id=members[0])  # no note
        with pytest.raises(ValueError):
            ledger.add_entry(db, club_id, "indbetaling", Fraction(10), member_id=1)  # another club's member
        with pytest.raises(ValueError):
            ledger.add_entry(db, club_id, "ukendt", Fraction(10), member_id=members[0])


def test_a_reversal_undoes_an_entry_once_and_is_logged(club):
    club_id, members = club
    with connect() as db:
        eid = ledger.add_entry(db, club_id, "indbetaling", Fraction(80), member_id=members[0])
        reversal = ledger.reverse_entry(db, club_id, eid)
        assert _balance(db, club_id, members[0]) == 0 and ledger.balances(db, club_id)["cash"] == 0
        with pytest.raises(ValueError):
            ledger.reverse_entry(db, club_id, eid)
        with pytest.raises(ValueError):
            ledger.reverse_entry(db, club_id, reversal)
        actions = [r["action"] for r in ledger.log_rows(db, club_id)]
        assert actions[:2] == ["reverse", "create"]
        # Nothing is ever edited or deleted.
        assert db.execute("SELECT COUNT(*) FROM kasse_entries WHERE club_id=?", (club_id,)).fetchone()[0] == 2


def test_reopening_a_counted_night_alerts_with_member_ids(club):
    club_id, members = club
    with connect() as db:
        sid = _closed_night(db, club_id, members, [0, 25, 75, 115])
        before = ledger.snapshot(db, sid)
        assert before[0][0] == members[1]  # member ids, never names
        db.execute("UPDATE sessions SET ended_at=NULL WHERE id=?", (sid,))
        ledger.add_alert(db, sid, "reopen", before, None)
        assert ledger.alerts(db, club_id)[0]["pending"]
        db.execute("UPDATE club_member_settlements SET amount_numerator=50 WHERE session_id=? AND member_id=?",
                   (sid, members[1]))
        db.execute("UPDATE sessions SET ended_at=CURRENT_TIMESTAMP WHERE id=?", (sid,))
        ledger.complete_reopen_alert(db, sid)
        db.execute("UPDATE club_members SET name='Bent B' WHERE id=?", (members[1],))
        alert = ledger.alerts(db, club_id)[0]
        change = next(c for c in alert["changes"] if c["member_id"] == members[1])
        assert change["name"] == "Bent B" and change["diff"] == 25 and not alert["pending"]
        assert "Bent" not in alert["before_json"]


def test_an_unchanged_reopened_night_leaves_no_alert(club):
    club_id, members = club
    with connect() as db:
        sid = _closed_night(db, club_id, members, [0, 25, 75, 115])
        ledger.add_alert(db, sid, "reopen", ledger.snapshot(db, sid), None)
        ledger.complete_reopen_alert(db, sid)
        assert ledger.alerts(db, club_id) == []


def test_init_db_twice_keeps_the_books(club):
    club_id, members = club
    with connect() as db:
        ledger.add_entry(db, club_id, "indbetaling", Fraction(10), member_id=members[0])
    init_db()
    init_db()
    with connect() as db:
        assert ledger.balances(db, club_id)["cash"] == 10


_logins = itertools.count(1)


@pytest.fixture
def logins():
    """A kasserer and an ordinary member of club 1, both logged in."""
    n = next(_logins)
    with TestClient(app) as admin:
        admin.post("/login", data={"username": "admin", "password": "a$$word"})
        kasserer_id, kasserer_temp = _new_login(admin, f"kassereren{n}", f"Kassereren {n}")
        member_id, member_temp = _new_login(admin, f"kassemedlem{n}", f"Kassemedlem {n}")
        with connect() as db:
            versions = db.execute("SELECT COUNT(*) FROM club_rule_versions WHERE club_id=1").fetchone()[0]
        # Club admins (the server admin counts as one) choose the kasserer with a checkbox on /club.
        page = admin.get("/club").text
        assert f'form="kasserer-{kasserer_id}" name="member_id" value="{kasserer_id}"' in page
        assert admin.post("/club/kasserer", data={"member_id": kasserer_id}, follow_redirects=False).status_code == 303
        assert "checked onchange" in admin.get("/club").text.split(f'value="{kasserer_id}"', 2)[1][:80]
        # Unticking sends no member_id and leaves the club without a kasserer; ticking again restores it.
        admin.post("/club/kasserer", data={})
        with connect() as db:
            assert db.execute("SELECT kasserer_member_id FROM clubs WHERE id=1").fetchone()[0] is None
        admin.post("/club/kasserer", data={"member_id": kasserer_id})
        # Choosing the kasserer is not a new rule version.
        with connect() as db:
            assert db.execute("SELECT COUNT(*) FROM club_rule_versions WHERE club_id=1").fetchone()[0] == versions
            assert db.execute("SELECT kasserer_member_id FROM clubs WHERE id=1").fetchone()[0] == kasserer_id
        with TestClient(app) as kasserer, TestClient(app) as member:
            _activate(kasserer, f"kassereren{n}", kasserer_temp)
            _activate(member, f"kassemedlem{n}", member_temp)
            yield admin, kasserer, member, kasserer_id, member_id
    with connect() as db:
        _wipe_kasse(db, 1)
        db.execute("UPDATE club_members SET status='paused' WHERE id IN (?,?)", (kasserer_id, member_id))


def test_only_the_kasserer_writes_and_every_member_reads(logins):
    admin, kasserer, member, kasserer_id, member_id = logins
    # An ordinary member cannot choose the kasserer; a club admin can.
    assert member.post("/club/kasserer", data={"member_id": member_id}).status_code == 403
    with connect() as db:
        db.execute("UPDATE club_members SET is_club_admin=1 WHERE id=?", (member_id,))
    try:
        assert 'form="kasserer-' in member.get("/club").text
        assert member.post("/club/kasserer", data={"member_id": member_id}, follow_redirects=False).status_code == 303
        with connect() as db:
            assert ledger.kasserer(db, 1)["id"] == member_id
        assert member.post("/club/kasserer", data={"member_id": kasserer_id}, follow_redirects=False).status_code == 303
    finally:
        with connect() as db:
            db.execute("UPDATE club_members SET is_club_admin=0 WHERE id=?", (member_id,))
    assert kasserer.post("/kasse/entries", data={"kind": "indbetaling", "member_id": member_id, "amount": "125,50"},
                         follow_redirects=False).status_code == 303
    assert kasserer.post("/kasse/buckets", data={"name": "Udlandstur 2027"},
                         follow_redirects=False).status_code == 303
    # An ordinary member and the server admin cannot write.
    assert member.post("/kasse/entries", data={"kind": "indbetaling", "member_id": member_id, "amount": "5"}).status_code == 403
    assert admin.post("/kasse/entries", data={"kind": "indbetaling", "member_id": member_id, "amount": "5"}).status_code == 403
    assert member.post("/kasse/entries/1/reverse").status_code == 403
    # Bad input re-renders the page with the reason.
    bad = kasserer.post("/kasse/entries", data={"kind": "korrektion", "member_id": member_id, "amount": "5"})
    assert bad.status_code == 400 and "skal have en forklaring" in bad.text

    page = member.get("/kasse")
    assert page.status_code == 200 and "<strong>125,5</strong>" in page.text and "Udlandstur 2027" in page.text
    # Every member reads the log; only the kasserer gets the forms.
    assert "oprettede puljen Udlandstur 2027" in page.text and "Ny postering" not in page.text
    own = kasserer.get("/kasse")
    assert "Ny postering" in own.text and "<h2>Log</h2>" in own.text
    assert "Din saldo 125,5 kr (til gode)" in member.get("/").text
    # Kassens start is set once, then the form is gone and a second save is refused.
    assert "Gem og lås start" in own.text
    assert kasserer.post("/kasse/start", data={"kasse_start": "2026-01-01", "start_cash": "100"},
                         follow_redirects=False).status_code == 303
    assert "Gem og lås start" not in kasserer.get("/kasse").text
    again = kasserer.post("/kasse/start", data={"kasse_start": "2025-01-01", "start_cash": "0"})
    assert again.status_code == 400 and "låst" in again.text
    assert member.get("/kasse/rules/indbetaling").status_code == 200
    assert member.get("/kasse/rules/nope").status_code == 404


def test_the_kasse_belongs_to_the_active_club(logins, club):
    _, kasserer, _, _, _ = logins
    other_club, others = club
    with connect() as db:
        foreign = ledger.add_entry(db, other_club, "indbetaling", Fraction(10), member_id=others[0])
    # Another club's member and entry are unknown here.
    assert kasserer.post("/kasse/entries", data={"kind": "indbetaling", "member_id": others[0], "amount": "5"}).status_code == 400
    assert kasserer.post(f"/kasse/entries/{foreign}/reverse").status_code == 404
    with connect() as db:
        assert ledger.balances(db, other_club)["cash"] == 10


def test_amounts_read_danish_thousands_and_zero():
    # "1.250" is a thousand two hundred and fifty, not 1,25.
    assert parse_amount("1.250") == 1250 and parse_amount("1.250.000") == 1250000
    assert parse_amount("1.250,50") == Fraction(2501, 2) and parse_amount("12.50") == Fraction(25, 2)
    assert parse_amount("0,00", allow_zero=True) == 0


def test_a_live_night_is_dated_in_danish_time(club):
    club_id, members = club
    with connect() as db:
        # 23:30 UTC on 28 February is 00:30 on 1 March in Denmark.
        _closed_night(db, club_id, members, [0, 25, 75, 115], created="2026-02-28 23:30:00")
        # An imported night keeps the date that was typed in.
        _closed_night(db, club_id, members, [0, 25, 75, 115], created="2026-02-28 23:30:00", origin="imported")
        ledger.set_start(db, club_id, "2026-03-01")
        assert _balance(db, club_id, members[3]) == -115


def test_no_alert_for_a_night_from_before_the_kasse_start(club):
    club_id, members = club
    with connect() as db:
        old = _closed_night(db, club_id, members, [0, 25, 75, 115], created="2025-06-01 18:00:00")
        new = _closed_night(db, club_id, members, [0, 25, 75, 115], created="2026-06-01 18:00:00")
        ledger.set_start(db, club_id, "2026-01-01")
        ledger.add_alert(db, old, "money", ledger.snapshot(db, old), [])
        assert ledger.alerts(db, club_id) == []
        ledger.add_alert(db, new, "money", ledger.snapshot(db, new), [])
        assert [a["session_id"] for a in ledger.alerts(db, club_id)] == [new]


def test_a_paused_kasserer_loses_the_role(club):
    club_id, members = club
    with connect() as db:
        ledger.set_kasserer(db, club_id, members[0])
        db.execute("UPDATE club_members SET status='paused' WHERE id=?", (members[0],))
        ledger.drop_inactive_kasserer(db, club_id)
        db.execute("UPDATE club_members SET status='active' WHERE id=?", (members[0],))
        # Reactivated, but not kasserer again until a club admin ticks the box.
        assert ledger.kasserer(db, club_id) is None
        assert ledger.log_lines(db, club_id)[0]["text"] == "valgte ingen som kasserer"


@pytest.fixture
def admin_in(club):
    """The server admin, working in the test club."""
    club_id, members = club
    with TestClient(app) as admin:
        admin.post("/login", data={"username": "admin", "password": "a$$word"})
        admin.post("/clubs/select", data={"club_id": club_id})
        yield admin, club_id, members


def _change(alert, member_id):
    change = next(c for c in alert["changes"] if c["member_id"] == member_id)
    return change["before"], change["after"]


def test_reopening_then_deleting_a_night_alerts_through_the_routes(admin_in):
    admin, club_id, members = admin_in
    with connect() as db:
        sid = _closed_night(db, club_id, members, [0, 25, 75, 115])
    assert admin.post(f"/sessions/{sid}/reopen", follow_redirects=False).status_code == 303
    with connect() as db:
        alert = ledger.alerts(db, club_id)[0]
    assert alert["kind"] == "reopen" and alert["pending"]
    # Deleting the reopened night replaces the pending alert with one that says what was removed.
    assert admin.post(f"/sessions/{sid}/delete", follow_redirects=False).status_code == 303
    with connect() as db:
        alerts = ledger.alerts(db, club_id)
    assert [a["kind"] for a in alerts] == ["delete"] and _change(alerts[0], members[3]) == (115, 0)


def test_switching_off_klubkasse_on_an_imported_night_alerts(admin_in):
    admin, club_id, members = admin_in
    with connect() as db:
        sid = _closed_night(db, club_id, members, [0, 25, 75, 115], origin="imported")
    assert admin.post(f"/sessions/{sid}/money", data={}, follow_redirects=False).status_code == 303
    with connect() as db:
        alerts = ledger.alerts(db, club_id)
        assert ledger.balances(db, club_id)["owed_to_club"] == 0
    assert [a["kind"] for a in alerts] == ["money"] and _change(alerts[0], members[2]) == (75, 0)
