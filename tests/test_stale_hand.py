"""A device whose new-hand form is out of date (eg a phone that slept while another
device saved a hand) must neither save a stale hand nor keep showing the old declarer."""
import asyncio
import json
import re

from fastapi.testclient import TestClient

from whistapp.database import connect
from whistapp.main import app
from whistapp.routes.sessions import editor_events


def _night(client):
    created = client.post("/sessions", data={"title": "Sovende telefon", "guests": "A\nB\nC\nD"},
                          follow_redirects=False)
    sid = int(created.headers["location"].split("/")[-1])
    with connect() as db:
        players = [r[0] for r in db.execute("SELECT id FROM players WHERE session_id=? ORDER BY id", (sid,))]
    return sid, players


def _hand(players, base, declarer=0):
    return {"bid_key": "sol", "declarer": str(players[declarer]), "participants": [str(p) for p in players],
            f"actual_{players[declarer]}": "0", "base_hand_id": base}


def _base(html):
    return re.search(r'name="base_hand_id" value="(\d+)"', html).group(1)


def _hand_count(sid):
    with connect() as db:
        return db.execute("SELECT COUNT(*) FROM hands WHERE session_id=?", (sid,)).fetchone()[0]


def test_stale_new_hand_form_is_refused_and_refreshed():
    with TestClient(app) as client:
        client.post("/login", data={"username": "admin", "password": "a$$word"})
        sid, players = _night(client)
        try:
            stale = _base(client.get(f"/sessions/{sid}").text)  # the phone's form
            assert stale == "0"
            # The PC saves a hand from an identical form.
            saved = client.post(f"/sessions/{sid}/hands", data=_hand(players, stale))
            assert saved.status_code == 200 and _hand_count(sid) == 1
            fresh = _base(saved.text)
            assert fresh != stale

            # The phone now submits its old form: nothing is saved, a fresh form comes back.
            refused = client.post(f"/sessions/{sid}/hands", data=_hand(players, stale, declarer=1))
            assert refused.status_code == 200
            assert "Hånden blev ikke gemt" in refused.text and _base(refused.text) == fresh
            assert _hand_count(sid) == 1

            # With the refreshed form it saves normally.
            assert client.post(f"/sessions/{sid}/hands", data=_hand(players, fresh, declarer=1)).status_code == 200
            assert _hand_count(sid) == 2
        finally:
            client.post(f"/sessions/{sid}/delete")


def _first_event(sid, hand):
    class Request:
        async def is_disconnected(self): return False

    async def run():
        response = await editor_events(Request(), sid, hand)
        chunks = response.body_iterator
        assert (await chunks.__anext__()).startswith(": connected")
        return await asyncio.wait_for(chunks.__anext__(), 5)
    return asyncio.run(run())


def test_reconnecting_stream_reports_hands_saved_while_away():
    with TestClient(app) as client:
        client.post("/login", data={"username": "admin", "password": "a$$word"})
        sid, players = _night(client)
        try:
            saved = client.post(f"/sessions/{sid}/hands", data=_hand(players, "0"))
            current = _base(saved.text)
            # A page whose form predates the hand is told at once to refresh the form.
            event = _first_event(sid, "0")
            assert event.startswith("event: results-changed")
            assert json.loads(event.split("data: ")[1]) == {"new_hand": True, "origin": None}
            # An up-to-date page gets only the usual heartbeat.
            assert _first_event(sid, current).startswith(": heartbeat")
        finally:
            client.post(f"/sessions/{sid}/delete")
