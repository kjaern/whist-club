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
            players_on_card = {m[1]: m for m in re.findall(
                r'<span class="card-player( not-playing)?(?: dealer)?">\s*<span class="card-name(?: dealer)?"(?: title="Giver")?>(\w)</span>\s*'
                r'<span class="delta[^"]*">([^<]*)</span>\s*<span class="card-total">([^<]*)</span>', card)}
            assert players_on_card["E"][0] and players_on_card["E"][2] == "–"  # E sat out, greyed
            # A–D played: each shows the change and the score after the hand.
            assert all(not players_on_card[n][0] and players_on_card[n][2] != "–" for n in "ABCD")
            assert all(players_on_card[n][3] == players_on_card[n][2].lstrip("+") for n in "ABCD")  # first hand
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


def test_rounds_are_marked_in_tables_and_cards():
    with TestClient(app) as client:
        client.post("/login", data={"username": "admin", "password": "a$$word"})
        sid, players = _night(client)  # four players: a round is four hands
        try:
            base = _base(client.get(f"/sessions/{sid}").text)
            for hand in range(5):
                base = _base(client.post(f"/sessions/{sid}/hands", data=_hand(players, base, hand % 4)).text)
            for url in (f"/sessions/{sid}", f"/sessions/{sid}/live"):
                html = client.get(url).text
                table = html[html.index('<table class="night"'):]
                rows = re.findall(r'<tr class="score-row( round-end)?"(?: data-hand="\d+")?>\s*<td class="hand-num">\s*(\d+)', table)
                assert [number for end, number in rows if end] == ["4"], url
            cards = _cards(client.get(f"/sessions/{sid}").text)
            headers = re.findall(r'class="round-divider">([^<]*)<', cards)
            assert headers == ["Runde 2 · 1 af 4 har givet", "Runde 1"]
            # Each header sits above the newest hand of its round.
            assert cards.index("Runde 2") < cards.index(">#5<") < cards.index("Runde 1") < cards.index(">#4<")
        finally:
            client.post(f"/sessions/{sid}/delete")


def _round_ends(client, sid):
    table = client.get(f"/sessions/{sid}").text
    table = table[table.index('<table class="night"'):]
    rows = re.findall(r'<tr class="score-row( round-end)?"(?: data-hand="\d+")?>\s*<td class="hand-num">\s*(\d+)', table)
    return [int(number) for end, number in rows if end]


def _play_suggested(client, sid):
    """Save a hand with the four players the rotation suggests, the first of them declaring."""
    page = client.get(f"/sessions/{sid}").text
    chosen = re.findall(r'name="participants" value="(\d+)"\s+checked', page)
    saved = client.post(f"/sessions/{sid}/hands", data={
        "bid_key": "sol", "declarer": chosen[0], "participants": chosen,
        f"actual_{chosen[0]}": "0", "base_hand_id": _base(page)})
    assert saved.status_code == 200


def _five(client):
    created = client.post("/sessions", data={"title": "Runder", "guests": "A\nB\nC\nD\nE"}, follow_redirects=False)
    return int(created.headers["location"].split("/")[-1])


def _names(sid):
    with connect() as db:
        return {name: pid for pid, name in db.execute("SELECT id,name FROM players WHERE session_id=?", (sid,))}


def _dealers(sid):
    names = {pid: name for name, pid in _names(sid).items()}
    with connect() as db:
        return "".join(names[r[0]] for r in db.execute("SELECT dealer_id FROM hands WHERE session_id=? ORDER BY id", (sid,)))


def test_a_round_is_everyone_present_dealing_once():
    with TestClient(app) as client:
        client.post("/login", data={"username": "admin", "password": "a$$word"})
        sid = _five(client)
        try:
            for _ in range(5):
                _play_suggested(client, sid)
            assert _dealers(sid) == "ABCDE" and _round_ends(client, sid) == [5]
            # B deals hand 7 and then leaves: the deal moves on to C, and round 2 is
            # A, B, C, D, E: five hands, because B had dealt before leaving.
            for _ in range(2):
                _play_suggested(client, sid)
            client.post(f"/sessions/{sid}/players/{_names(sid)['B']}/toggle")
            for _ in range(3):
                _play_suggested(client, sid)
            assert _dealers(sid) == "ABCDE" + "ABCDE" and _round_ends(client, sid) == [5, 10]
        finally:
            client.post(f"/sessions/{sid}/delete")


def test_leaving_before_ones_deal_shortens_the_round():
    with TestClient(app) as client:
        client.post("/login", data={"username": "admin", "password": "a$$word"})
        sid = _five(client)
        try:
            for _ in range(6):
                _play_suggested(client, sid)
            # B leaves before dealing in round 2: the round is A, C, D, E, only four hands.
            client.post(f"/sessions/{sid}/players/{_names(sid)['B']}/toggle")
            for _ in range(3):
                _play_suggested(client, sid)
            assert _dealers(sid) == "ABCDE" + "ACDE" and _round_ends(client, sid) == [5, 9]
            # F joins in the seat after E, so deals next; the round waits until all five have dealt.
            client.post(f"/sessions/{sid}/players", data={"name": "F", "before_player": "0"})
            for _ in range(4):
                _play_suggested(client, sid)
            assert _dealers(sid) == "ABCDE" + "ACDE" + "FACD" and _round_ends(client, sid) == [5, 9]
            _play_suggested(client, sid)
            assert _dealers(sid).endswith("FACDE") and _round_ends(client, sid) == [5, 9, 14]
        finally:
            client.post(f"/sessions/{sid}/delete")


def test_dealer_is_marked_in_table_cards_and_next_hand():
    with TestClient(app) as client:
        client.post("/login", data={"username": "admin", "password": "a$$word"})
        sid = _five(client)
        try:
            _play_suggested(client, sid)
            ids = _names(sid)
            html = client.get(f"/sessions/{sid}").text
            # The next hand is dealt by B: their chip carries the G.
            line = html[html.index('class="players-line"'):html.index('id="roster-dialog"')]
            assert re.search(rf'class="player-name dealer" data-id="{ids["B"]}"', line)
            assert line.count(' dealer"') == 1
            cards = _cards(html)
            assert '<span class="card-name dealer" title="Giver">A</span>' in cards and cards.count('card-name dealer') == 1
            for url in (f"/sessions/{sid}", f"/sessions/{sid}/live"):
                table = client.get(url).text
                table = table[table.index('<table class="night"'):]
                assert len(re.findall(r'<td class="[^"]*\sdealer"', table)) == 1
        finally:
            client.post(f"/sessions/{sid}/delete")


def test_hands_saved_before_rounds_existed_have_no_round_marks():
    with TestClient(app) as client:
        client.post("/login", data={"username": "admin", "password": "a$$word"})
        sid, players = _night(client)
        try:
            base = _base(client.get(f"/sessions/{sid}").text)
            for hand in range(4):
                base = _base(client.post(f"/sessions/{sid}/hands", data=_hand(players, base, hand)).text)
            with connect() as db:
                db.execute("UPDATE hands SET round_no=NULL, dealer_id=NULL WHERE session_id=?", (sid,))
            html = client.get(f"/sessions/{sid}").text
            assert _round_ends(client, sid) == [] and 'class="round-divider"' not in html
            # The next hand starts round 1 from there.
            client.post(f"/sessions/{sid}/hands", data=_hand(players, _base(html), 0))
            assert "Runde 1 · 1 af 4 har givet" in client.get(f"/sessions/{sid}").text
        finally:
            client.post(f"/sessions/{sid}/delete")


def test_round_ends_when_the_last_missing_player_leaves():
    with TestClient(app) as client:
        client.post("/login", data={"username": "admin", "password": "a$$word"})
        created = client.post("/sessions", data={"title": "Runder", "guests": "A\nB\nC\nD\nE"}, follow_redirects=False)
        sid = int(created.headers["location"].split("/")[-1])
        try:
            for _ in range(4):  # A–D have bid first; E has not
                _play_suggested(client, sid)
            assert _round_ends(client, sid) == []
            with connect() as db:
                e = db.execute("SELECT id FROM players WHERE session_id=? AND name='E'", (sid,)).fetchone()[0]
            client.post(f"/sessions/{sid}/players/{e}/toggle")  # E leaves before their turn
            _play_suggested(client, sid)
            assert _round_ends(client, sid) == [4]  # round 1 was over when E left
            with connect() as db:
                assert [r[0] for r in db.execute("SELECT round_no FROM hands WHERE session_id=? ORDER BY id", (sid,))] == [1, 1, 1, 1, 2]
        finally:
            client.post(f"/sessions/{sid}/delete")


def test_live_view_has_the_same_compact_cards_without_editing():
    with TestClient(app) as client:
        client.post("/login", data={"username": "admin", "password": "a$$word"})
        sid, players = _night(client)
        try:
            base = _base(client.get(f"/sessions/{sid}").text)
            for hand in range(2):
                base = _base(client.post(f"/sessions/{sid}/hands", data=_hand(players, base, hand)).text)
            for html in (client.get(f"/sessions/{sid}/live").text, client.get(f"/sessions/{sid}/live/scoreboard").text):
                cards = _cards(html)
                assert re.findall(r'class="hand-num">#(\d+)<', cards) == ["2", "1"]
                assert cards.count('class="card-total"') == 2 * len(players)
                assert "/result" not in cards and "Ret" not in cards  # the live view only shows
                assert len(re.findall(r'class="total-chip[" ]', html)) == len(players)
                assert 'class="table-scroll all-hands"' in html and "Vis fuld tabel" in html
        finally:
            client.post(f"/sessions/{sid}/delete")


def test_hand_table_cells_can_stack_numbers_under_the_bid():
    with TestClient(app) as client:
        client.post("/login", data={"username": "admin", "password": "a$$word"})
        sid, players = _night(client)
        try:
            base = _base(client.get(f"/sessions/{sid}").text)
            client.post(f"/sessions/{sid}/hands", data=_hand(players, base))
            for url in (f"/sessions/{sid}", f"/sessions/{sid}/live", f"/sessions/{sid}/live/scoreboard"):
                html = client.get(url).text
                table = html[html.index('<table class="night"'):]
                assert "--stacked-column-width:" in table[:300]
                # Every player cell has its bid part and its numbers part, which the page
                # stacks (class "stacked") as soon as the table would need a sideways scrollbar.
                assert table.count('<span class="cell-bid">') == table.count('<span class="cell-nums">') == len(players)
                assert "window.whistFitTables()" in table
        finally:
            client.post(f"/sessions/{sid}/delete")


def test_live_refresh_sends_nothing_when_nothing_changed():
    with TestClient(app) as client:
        client.post("/login", data={"username": "admin", "password": "a$$word"})
        sid, players = _night(client)
        try:
            page = client.get(f"/sessions/{sid}/live").text
            version = re.search(r'/live/scoreboard\?v=([0-9a-f]+)"', page).group(1)
            # Polling with the shown version: no change, no content, nothing redrawn.
            same = client.get(f"/sessions/{sid}/live/scoreboard?v={version}")
            assert same.status_code == 204 and same.text == ""
            # A saved hand changes it; the new scoreboard carries its own new version.
            client.post(f"/sessions/{sid}/hands", data=_hand(players, _base(client.get(f"/sessions/{sid}").text)))
            changed = client.get(f"/sessions/{sid}/live/scoreboard?v={version}")
            assert changed.status_code == 200 and "#1" in changed.text
            newer = re.search(r'/live/scoreboard\?v=([0-9a-f]+)"', changed.text).group(1)
            assert newer != version
            assert client.get(f"/sessions/{sid}/live/scoreboard?v={newer}").status_code == 204
            # So does a player leaving, which the hands alone would not show.
            client.post(f"/sessions/{sid}/players/{players[3]}/toggle")
            assert client.get(f"/sessions/{sid}/live/scoreboard?v={newer}").status_code == 200
        finally:
            client.post(f"/sessions/{sid}/delete")
