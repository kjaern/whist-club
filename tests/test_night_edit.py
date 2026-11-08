"""Editing a closed night: nothing changes until the edit is saved, then all of it at once."""
import json
import re
from fractions import Fraction

import pytest
from fastapi.testclient import TestClient

from whistapp.database import connect, init_db
from whistapp.main import app

RATES = [100, 200, 300, 400]


@pytest.fixture(autouse=True)
def _shared_home():
    init_db()


@pytest.fixture
def client():
    with TestClient(app) as c:
        c.post("/login", data={"username": "admin", "password": "a$$word"})
        yield c


@pytest.fixture
def night(client):
    """A closed club night of four members on its own rule version, with two hands.

    Hand 1: G0 and G1 bid 8 and take 8. Hand 2: G2 and G3 bid 8 and take 6.
    """
    with connect() as db:
        sid = db.execute("INSERT INTO sessions(title,is_club,club_id) VALUES ('Redigering',1,1)").lastrowid
        members = [db.execute("INSERT INTO club_members(name,status,club_id) VALUES (?,'paused',1)",
                              (f"Redigerer {sid}-{i}",)).lastrowid for i in range(4)]
        players = [db.execute("INSERT INTO players(session_id,name,member_id,seat_order) VALUES (?,?,?,?)",
                              (sid, f"G{i}", m, i)).lastrowid for i, m in enumerate(members)]
        rules = db.execute("SELECT fejl40,bid_settings FROM club_settings WHERE id=1").fetchone()
        version = db.execute("""INSERT INTO club_rule_versions(club_id,number,created_by,placements,fejl40,
            money_mode,point_rate,bid_settings,member_ids)
            VALUES (1,(SELECT MAX(number)+1 FROM club_rule_versions WHERE club_id=1),'test',?,?,'placement','1',?,?)""",
                             (json.dumps(RATES), rules["fejl40"], rules["bid_settings"], json.dumps(members))).lastrowid
        db.execute("UPDATE sessions SET rule_version_id=? WHERE id=?", (version, sid))
    for declarer, partner, actual in ((0, 1, 8), (2, 3, 6)):
        saved = client.post(f"/sessions/{sid}/hands", data={
            "bid_key": "almindelige", "target": "8", "actual": str(actual), "declarer": str(players[declarer]),
            "partner": str(players[partner]), "participants": [str(p) for p in players]})
        assert saved.status_code == 200
    assert client.post(f"/sessions/{sid}/end", data={"collect_club_money": "true"},
                       follow_redirects=False).status_code == 303
    yield sid, members, players
    with connect() as db:
        db.execute("DELETE FROM sessions WHERE id=?", (sid,))
        db.execute("DELETE FROM club_rule_versions WHERE id=?", (version,))
        db.executemany("DELETE FROM club_members WHERE id=?", [(m,) for m in members])


def _hands(sid):
    with connect() as db:
        return [dict(h, declarer=db.execute("SELECT player_id FROM hand_declarers WHERE hand_id=?", (h["id"],)).fetchone()[0])
                for h in db.execute("SELECT * FROM hands WHERE session_id=? ORDER BY id", (sid,))]


def _form(client, sid, **changes):
    """The edit page's form as the browser would send it, with some hands changed."""
    page = client.get(f"/sessions/{sid}/edit").text
    form = {"night_state": re.search(r'name="night_state" value="([0-9a-f]+)"', page).group(1)}
    for h in _hands(sid):
        p = f"h{h['id']}_"
        form.update({p + "bid_key": h["bid_key"], p + "declarer": h["declarer"], p + "target": h["target"],
                     p + "partner": h["partner_id"], p + "actual": h["actual"]})
    form.update(changes)
    return form


def _state(sid):
    """Everything a save may change: hands, scores, Fejl40, settlement, closing time."""
    with connect() as db:
        return [
            [tuple(r) for r in db.execute("SELECT * FROM hands WHERE session_id=? ORDER BY id", (sid,))],
            [tuple(r) for r in db.execute("""SELECT hs.* FROM hand_scores hs JOIN hands h ON h.id=hs.hand_id
                WHERE h.session_id=? ORDER BY 1,2""", (sid,))],
            [tuple(r) for r in db.execute("SELECT id,player_id,amount FROM penalty_payments WHERE session_id=?", (sid,))],
            [tuple(r) for r in db.execute("SELECT * FROM club_member_settlements WHERE session_id=? ORDER BY 2", (sid,))],
            db.execute("SELECT ended_at,collect_club_money FROM sessions WHERE id=?", (sid,)).fetchone()[:],
        ]


def _settlement(sid, members):
    with connect() as db:
        paid = {r[0]: Fraction(r[1], r[2]) for r in db.execute(
            "SELECT member_id,amount_numerator,amount_denominator FROM club_member_settlements WHERE session_id=?", (sid,))}
    return [paid[m] for m in members]


def _log(sid):
    with connect() as db:
        return [r[0] for r in db.execute("SELECT description FROM evening_log WHERE evening_id=? ORDER BY id", (sid,))]


def test_starting_an_edit_only_logs(client, night):
    sid, members, _ = night
    before = _state(sid)
    assert "/edit/start" in client.get(f"/sessions/{sid}").text
    assert client.post(f"/sessions/{sid}/edit/start", follow_redirects=False).headers["location"] == f"/sessions/{sid}/edit"
    assert _state(sid) == before
    assert _log(sid)[-1] == "Begyndte at redigere den afsluttede spilaften"
    page = client.get(f"/sessions/{sid}/edit").text
    assert 'hx-put="/sessions/' in page and "Gem ændringer" in page and "Beregn" in page


def test_calculate_shows_the_outcome_and_saves_nothing(client, night):
    sid, members, _ = night
    hand2 = _hands(sid)[1]["id"]
    before, log = _state(sid), _log(sid)
    with connect() as db:
        audit = db.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
    page = client.post(f"/sessions/{sid}/edit/calculate", data=_form(client, sid, **{f"h{hand2}_actual": 9})).text
    assert "Hvis du gemmer" in page and "Rettede hånd 2" in page and "Afregning ændret: Redigerer " in page
    assert _state(sid) == before and _log(sid) == log
    with connect() as db:
        assert db.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] == audit


def test_saving_applies_the_edit_and_resettles_the_closed_night(client, night):
    sid, members, _ = night
    # G0/G1 won hand 1 and G2/G3 lost hand 2: the tied pairs share 100+200 and 300+400.
    assert _settlement(sid, members) == [150, 150, 350, 350]
    hand2 = _hands(sid)[1]["id"]
    before = _state(sid)
    saved = client.put(f"/sessions/{sid}", data=_form(client, sid, **{f"h{hand2}_actual": 9}))
    assert saved.headers.get("HX-Redirect") == f"/sessions/{sid}"
    after = _state(sid)
    assert after[4] == before[4]  # still closed, at the same time, still counting
    assert after[0][0] == before[0][0] and after[0][1] != before[0][1]  # only hand 2 changed, same ids
    resettled = _settlement(sid, members)
    assert resettled != [150, 150, 350, 350] and sum(resettled) == sum(RATES)
    assert _log(sid)[-1].startswith("Redigeret: rettede hånd 2; afregning ændret: ")
    history = client.get(f"/sessions/{sid}/history").text
    assert "rettede hånd" in history


def test_an_unchanged_form_saves_nothing(client, night):
    sid, _, _ = night
    before, log = _state(sid), _log(sid)
    page = client.put(f"/sessions/{sid}", data=_form(client, sid)).text
    assert "Der er ingen ændringer." in page and _state(sid) == before and _log(sid) == log


def test_one_invalid_hand_saves_nothing_at_all(client, night):
    sid, _, players = night
    first, second = (h["id"] for h in _hands(sid))
    before = _state(sid)
    page = client.put(f"/sessions/{sid}", data=_form(client, sid, **{
        f"h{first}_actual": 10, f"h{second}_partner": 999999})).text
    assert "Kan ikke gemmes" in page and "Hånd 2: Ugyldig makker eller stik" in page
    assert _state(sid) == before


def test_a_page_from_before_another_change_is_refused(client, night):
    sid, _, players = night
    form = _form(client, sid, **{f"h{_hands(sid)[1]['id']}_actual": 9})
    with connect() as db:  # another device saves an edit meanwhile
        db.execute("INSERT INTO penalty_payments(session_id,player_id,amount) VALUES (?,?,40)", (sid, players[0]))
    before = _state(sid)
    page = client.put(f"/sessions/{sid}", data=form).text
    assert "ændret på en anden enhed" in page and "Intet blev gemt." in page
    assert _state(sid) == before


def test_deleting_a_hand_and_changing_fejl40(client, night):
    sid, members, players = night
    with connect() as db:
        kept = db.execute("INSERT INTO penalty_payments(session_id,player_id,amount) VALUES (?,?,40)", (sid, players[0])).lastrowid
        db.execute("INSERT INTO penalty_payments(session_id,player_id,amount) VALUES (?,?,40)", (sid, players[1])).lastrowid
    first, second = (h["id"] for h in _hands(sid))
    form = _form(client, sid, **{f"h{second}_delete": "1", f"add_penalty_{players[3]}": "2"})
    form["keep_penalty"] = [str(kept)]
    client.put(f"/sessions/{sid}", data=form)
    assert [h["id"] for h in _hands(sid)] == [first]
    with connect() as db:
        fejl40 = sorted(r[0] for r in db.execute("SELECT player_id FROM penalty_payments WHERE session_id=?", (sid,)))
    assert fejl40 == sorted([players[0], players[3], players[3]])  # players[1]'s was removed
    assert _log(sid)[-1].startswith("Redigeret: slettede hånd 2, fjernede Fejl40 for Redigerer")
    # A closed night keeps at least one hand.
    page = client.put(f"/sessions/{sid}", data=_form(client, sid, **{f"h{first}_delete": "1"})).text
    assert "mindst én hånd" in page and [h["id"] for h in _hands(sid)] == [first]


def test_an_open_night_is_not_edited_this_way(client, night):
    sid, _, _ = night
    with connect() as db:
        db.execute("UPDATE sessions SET ended_at=NULL WHERE id=?", (sid,))
    assert client.post(f"/sessions/{sid}/edit/start", follow_redirects=False).status_code == 409
    assert client.put(f"/sessions/{sid}", data={}).status_code == 409


def test_closing_refuses_a_stale_page_and_a_second_close(client, night):
    sid, _, players = night
    with connect() as db:
        db.execute("UPDATE sessions SET ended_at=NULL WHERE id=?", (sid,))
    page = client.get(f"/sessions/{sid}").text
    seen = re.search(r'name="night_state" value="([0-9a-f]+)"', page).group(1)
    with connect() as db:  # another device registers a Fejl40 meanwhile
        db.execute("INSERT INTO penalty_payments(session_id,player_id,amount) VALUES (?,?,40)", (sid, players[0]))
    response = client.post(f"/sessions/{sid}/end", data={"collect_club_money": "true", "night_state": seen})
    assert response.status_code == 409 and "ændret på en anden enhed" in response.text
    with connect() as db:
        assert db.execute("SELECT ended_at FROM sessions WHERE id=?", (sid,)).fetchone()[0] is None
    client.post(f"/sessions/{sid}/end", data={"collect_club_money": "true"})
    assert client.post(f"/sessions/{sid}/end", data={"collect_club_money": "true"}).status_code == 409
