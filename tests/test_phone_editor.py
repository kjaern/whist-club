"""Spiladministration: compact cards for every hand (newest first) with the full table one
tap away, players shown on one line and changed in a popup, explanations behind ⓘ, and
the photo card last on live nights."""
import re

from fastapi.testclient import TestClient

from whistapp.database import connect
from whistapp.main import app
from test_stale_hand import _base, _hand, _night


def _cards(html):
    return html[html.index("Hænder · nyeste først"):html.index('class="secondary show-all-hands"')]


def test_compact_cards_list_every_hand_newest_first_with_editor():
    with TestClient(app) as client:
        client.post("/login", data={"username": "admin", "password": "a$$word"})
        sid, players = _night(client)
        try:
            base = _base(client.get(f"/sessions/{sid}").text)
            assert "Hænder · nyeste først" not in client.get(f"/sessions/{sid}").text  # nothing to show yet
            for declarer in range(4):
                base = _base(client.post(f"/sessions/{sid}/hands", data=_hand(players, base, declarer)).text)
            with connect() as db:
                hand_ids = [r[0] for r in db.execute("SELECT id FROM hands WHERE session_id=? ORDER BY id", (sid,))]

            for html in (client.get(f"/sessions/{sid}").text, client.get(f"/sessions/{sid}/editor/results").text):
                cards = _cards(html)
                assert re.findall(r'class="hand-num">#(\d+)<', cards) == ["4", "3", "2", "1"]
                # Each card has the table's editor, posting to the same routes.
                for hand_id in hand_ids:
                    assert f"/hands/{hand_id}/result" in cards and f"/hands/{hand_id}/delete" in cards
                assert "Ret" in cards and "Sol" in cards
                # Totals for every player, and the full table is still there.
                assert len(re.findall(r'class="total-chip[" ]', html)) == len(players)
                assert "Vis fuld tabel" in html and 'class="table-scroll all-hands"' in html
                table = html[html.index('class="table-scroll all-hands"'):]
                assert len(re.findall(r'class="hand-num">\s*\d+\s*<', table)) == 4
        finally:
            client.post(f"/sessions/{sid}/delete")


def test_players_are_one_line_and_edited_in_a_popup():
    with TestClient(app) as client:
        client.post("/login", data={"username": "admin", "password": "a$$word"})
        sid, players = _night(client)
        try:
            client.post(f"/sessions/{sid}/players/{players[3]}/toggle")  # D leaves the table
            html = client.get(f"/sessions/{sid}").text
            line = html[html.index('class="players-line"'):html.index('id="roster-dialog"')]
            # Everyone is listed; D, who left, is greyed and struck through.
            assert 'id="open-roster"' in line
            assert all(f'data-id="{p}"' in line for p in players)
            assert re.search(rf'class="player-name not-playing gone" data-id="{players[3]}"', line)
            assert 'name="participants"' not in line  # nothing to mis-tap while scrolling
            popup = html[html.index('id="roster-dialog"'):html.index("</dialog>", html.index('id="roster-dialog"'))]
            # The checkboxes stay inside the hand form, so they are still submitted with it.
            form = html[html.index('id="new-hand-form"'):html.index("</form>", html.index('id="new-hand-form"'))]
            assert 'id="roster-dialog"' in form
            assert popup.count('name="participants"') == len(players)
            assert "player-status" in popup and "Ændr bordrækkefølge" in popup and 'id="roster-new-name"' in popup
            added = client.post(f"/sessions/{sid}/players", data={"name": "Ekstra", "before_player": "0"})
            assert added.status_code == 200 and "Ekstra" in added.text
        finally:
            client.post(f"/sessions/{sid}/delete")


def test_cards_and_players_line_grey_out_whoever_sits_out():
    with TestClient(app) as client:
        client.post("/login", data={"username": "admin", "password": "a$$word"})
        sid, players = _night(client)
        try:
            client.post(f"/sessions/{sid}/players", data={"name": "E", "before_player": "0"})
            base = _base(client.get(f"/sessions/{sid}").text)
            client.post(f"/sessions/{sid}/hands", data=_hand(players, base))  # A–D play, E sits out
            html = client.get(f"/sessions/{sid}").text
            card = _cards(html)
            assert '<span class="not-playing">E –</span>' in card
            assert all(f'<span>{name} <span class="delta' in card for name in "ABCD")
            with connect() as db:
                e = db.execute("SELECT id FROM players WHERE session_id=? AND name='E'", (sid,)).fetchone()[0]
            # After the hand the rotation moves on: one of A–D is greyed out in the next hand's line.
            line = html[html.index('class="players-line"'):html.index('id="roster-dialog"')]
            assert line.count('class="player-name not-playing"') == 1 and f'data-id="{e}"' in line
        finally:
            client.post(f"/sessions/{sid}/delete")


def test_helper_texts_are_behind_info_buttons_and_photos_come_last():
    with TestClient(app) as client:
        client.post("/login", data={"username": "admin", "password": "a$$word"})
        sid, _ = _night(client)
        try:
            html = client.get(f"/sessions/{sid}").text
            for title in ("Spillere denne hånd", "Ny hånd", "Bordrækkefølge", "Klubkasse"):
                assert f'aria-label="Forklaring: {title}"' in html
            assert '<p class="muted">\n        Hånden gemmes først' not in html
            assert "Point og penge holdes adskilt</span>" not in html
            # A live night's photo card sits after the night itself.
            assert html.index('id="session-content"') < html.index("<h2>Originalark</h2>")
        finally:
            client.post(f"/sessions/{sid}/delete")
