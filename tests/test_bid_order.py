"""The club's bid order: set on Klubadministration, used wherever bids are listed."""
import json
import re

import pytest
from fastapi.testclient import TestClient

from whistapp.database import connect, init_db
from whistapp.main import app
from whistapp.rules import DEFAULT_BID_ORDER, bid_type_order, enabled_bid_keys, ordered_bid_keys
from whistapp.stats.bids_played import BidsPlayed
from whistapp.stats.data import Scope, load
from test_bid_settings import _bid_fields, _club_form, restore_club_rules  # noqa: F401
from test_stats_meldinger import _seed, _texts

CUSTOM = ["sol", "vip", "almindelige"]  # moved to the top; the rest keep the default order
EXPECTED = CUSTOM + [t for t in DEFAULT_BID_ORDER if t not in CUSTOM]


@pytest.fixture(autouse=True)
def _shared_home():
    init_db()  # the shared test home exists even when this file runs on its own


def test_order_helpers():
    assert bid_type_order() == DEFAULT_BID_ORDER
    saved = {t: {"order": i} for i, t in enumerate(EXPECTED)}
    assert bid_type_order(saved) == EXPECTED
    # A bid the club's saved order does not mention (e.g. new in the app) goes last.
    del saved["halve"]
    assert bid_type_order(saved) == [t for t in EXPECTED if t != "halve"] + ["halve"]
    assert ordered_bid_keys({"vip": {"order": 0}})[:3] == ["vip1", "vip2", "vip3"]
    assert enabled_bid_keys({"sol": {"order": 0}, "vip": {"order": 1, "enabled": False}})[:2] == ["sol", "almindelige"]


def _options(html, select):
    part = html.split(select, 1)[1].split("</select>", 1)[0]
    return re.findall(r'<option value="([a-z_0-9]+)"', part)


def test_club_order_is_saved_and_used_everywhere(restore_club_rules):  # noqa: F811
    with TestClient(app) as client:
        client.post("/login", data={"username": "admin", "password": "a$$word"})
        with connect() as db:
            form = _club_form(db, money_mode="score_settle", point_rate="1", placements="1", **_bid_fields())
        # Unknown and repeated keys are ignored; the bids left out are appended in default order.
        form["bid_order"] = CUSTOM + ["nonsense", "sol"]
        saved_page = client.post("/club/save", data=form, follow_redirects=False)
        assert saved_page.status_code == 303, re.findall(r'class="error">([^<]*)', saved_page.text)
        with connect() as db:
            saved = json.loads(db.execute("SELECT bid_settings FROM club_settings WHERE id=1").fetchone()[0])
        assert sorted(saved, key=lambda t: saved[t]["order"]) == EXPECTED

        page = client.get("/club").text
        assert re.findall(r'name="bid_order" value="([a-z_]+)"', page) == EXPECTED
        rules = json.loads(client.get("/").text.split("const rules = ")[1].split(";\n")[0])
        assert [r["key"] for r in rules] == ordered_bid_keys(saved)[:1] + ["vip1"] + [
            k for k in ordered_bid_keys(saved) if k not in ("sol", "vip1", "vip2", "vip3")]

        created = client.post("/sessions", data={"title": "Rækkefølge", "guests": "A\nB\nC\nD"},
                              follow_redirects=False)
        sid = int(created.headers["location"].split("/")[-1])
        try:
            night = client.get(f"/sessions/{sid}").text
            assert _options(night, 'name="bid_key"')[:4] == ["sol", "vip1", "almindelige", "halve"]
            with connect() as db:
                players = [r[0] for r in db.execute("SELECT id FROM players WHERE session_id=? ORDER BY id", (sid,))]
            saved_hand = client.post(f"/sessions/{sid}/hands", data={
                "bid_key": "almindelige", "target": "8", "actual": "8", "declarer": str(players[0]),
                "partner": str(players[1]), "participants": [str(p) for p in players]})
            assert _options(saved_hand.text, 'class="edit-bid-key"')[:5] == ["sol", "vip1", "vip2", "vip3", "almindelige"]

            # The night keeps the order it was created with when the club changes its order.
            form["bid_order"] = list(reversed(EXPECTED))
            client.post("/club/save", data=form, follow_redirects=False)
            assert _options(client.get(f"/sessions/{sid}").text, 'name="bid_key"')[:2] == ["sol", "vip1"]
            with connect() as db:
                db.execute("UPDATE sessions SET is_club=1 WHERE id=?", (sid,))
                night_bids = [key for key, _ in load(db, Scope("night", session_id=sid)).bids]
                club_bids = [key for key, _ in load(db, Scope()).bids]
            assert night_bids[:2] == ["sol", "vip1"] and club_bids[0] == "paskrig"
        finally:
            client.post(f"/sessions/{sid}/delete")


def test_rejected_form_keeps_the_posted_order(restore_club_rules):  # noqa: F811
    with TestClient(app) as client:
        client.post("/login", data={"username": "admin", "password": "a$$word"})
        with connect() as db:
            form = _club_form(db, money_mode="score_settle", point_rate="1", placements="1", **_bid_fields())
        for key in [k for k in form if k.startswith("bid_enabled_")]:
            del form[key]  # nothing enabled: the form is shown again with an error
        form["bid_order"] = list(reversed(DEFAULT_BID_ORDER))
        page = client.post("/club/save", data=form).text
        assert "Mindst én melding skal være slået til." in page
        assert re.findall(r'name="bid_order" value="([a-z_]+)"', page) == list(reversed(DEFAULT_BID_ORDER))


def test_statistics_follow_the_club_order(tmp_path, monkeypatch):
    import whistapp.database as database
    monkeypatch.setattr(database, 'DB_PATH', tmp_path / 'test.sqlite3')
    init_db()
    with connect() as db:
        _seed(db)
        db.execute("UPDATE club_settings SET bid_settings=? WHERE id=1",
                   (json.dumps({t: {"order": i} for i, t in enumerate(EXPECTED)}),))
        rows = [r['bid'] for r in _texts(BidsPlayed().compute(load(db, Scope())))]
    assert rows[:6] == ['Sol', 'VIP', 'VIP1', 'VIP2', 'VIP3', 'Almindelige']
