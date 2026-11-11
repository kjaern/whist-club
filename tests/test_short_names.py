"""Phones in portrait show short player names in the hand cards and the table head, so a
card needs fewer lines; landscape and PC keep the full names."""
import re
from pathlib import Path

from fastapi.testclient import TestClient

from whistapp.database import connect
from whistapp.main import app
from whistapp.web import short_names
from test_stale_hand import _base, _hand


def _players(*names):
    return [{"id": i, "name": n} for i, n in enumerate(names, 1)]


def test_short_name_is_the_shortest_unique_start_of_at_least_three_letters():
    assert short_names(_players("Kristoffer", "Karsten", "Anne Marie", "Annette", "Bo", "Bodil")) == {
        1: "Kri", 2: "Kar", 3: "Anne M", 4: "Annet", 5: "Bo", 6: "Bod"}
    # Case and extra spaces do not matter; a name that is the start of another stays whole.
    assert short_names(_players("Lars  Erik", "lars", "Lise")) == {1: "Lars E", 2: "lars", 3: "Lis"}
    # Two players with the same full name cannot be told apart anyway.
    assert short_names(_players("Ole", "Ole", "Oliver")) == {1: "Ole", 2: "Ole", 3: "Oli"}


def test_cards_and_table_head_carry_the_short_names_for_phones():
    with TestClient(app) as client:
        client.post("/login", data={"username": "admin", "password": "a$$word"})
        created = client.post("/sessions", data={"title": "Korte navne", "guests": "Kristoffer\nKarsten\nAnne Marie\nAnnette"},
                              follow_redirects=False)
        sid = int(created.headers["location"].split("/")[-1])
        try:
            with connect() as db:
                players = [r[0] for r in db.execute("SELECT id FROM players WHERE session_id=? ORDER BY id", (sid,))]
            base = _base(client.get(f"/sessions/{sid}").text)
            base = _base(client.post(f"/sessions/{sid}/hands", data=_hand(players, base, 2)).text)
            client.post(f"/sessions/{sid}/hands", data={
                "bid_key": "almindelige", "declarer": str(players[0]), "target": "8", "partner": str(players[1]),
                "actual": "9", "participants": [str(p) for p in players], "base_hand_id": base})
            normal = client.get(f"/sessions/{sid}").text
            line = normal[normal.index('class="recent-bid"'):normal.index('class="recent-deltas"')]
            # Full names, the target before the bid, and "M" for Makker on phones.
            assert re.search(r'Kristoffer <strong>8 <span class="full-form">Almindelige</span>.*?</strong> <span class="actual">9</span>'
                             r'\s*<span class="full-form">Makker</span><span class="short-form">M</span> Karsten', line, re.S)
            for html in (client.get(f"/sessions/{sid}").text, client.get(f"/sessions/{sid}/live").text):
                cards = html[html.index("Hænder · nyeste først"):html.index('class="secondary show-all-hands"')]
                for full, short in (("Kristoffer", "Kri"), ("Karsten", "Kar"), ("Anne Marie", "Anne M"),("Annette", "Annet")):
                    pair = f'<span class="full-form">{full}</span><span class="short-form">{short}</span>'
                    assert pair in cards  # the card's players and the bid line
                    head = html[html.index("<thead>"):html.index("</thead>")]
                    assert pair in head
                # The card's first line keeps the full names: the Sol hand reads "Sol Anne Marie →0",
                # without the fixed number Sol stores for ranking.
                assert '<strong><span class="full-form">Sol</span>' in cards and 'Anne Marie <span class="actual">0</span>' in cards
                # Every card player has the three parts whistFitCards measures.
                card_players = re.findall(r'<span class="card-player.*?<span class="card-total">', cards, re.S)
                assert len(card_players) == 2 * len(players)
                assert all('<span class="short-form">' in c and 'class="delta' in c for c in card_players)
                assert "window.whistFitCards()" in html
                # The table shows the number first too, and none for Sol.
                table = html[html.index('class="table-scroll all-hands"'):]
                assert '<span class="bid-label"><span class="bid-target">8</span> <span class="bid-name">Almindelige</span>' in table
                assert '<span class="bid-label"><span class="bid-name">Sol</span>' in table
                # The totals at the top keep the full names.
                chips = html[html.index('class="total-chips"'):html.index("Hænder · nyeste først")]
                assert "<span>Kristoffer</span>" in chips and "short-form" not in chips
            # Ret shows the hand the same way: "Hånd 2 · 8 Almindelige", and "Hånd 1 · Sol".
            assert re.search(r"Hånd 2 · 8 Almindelige\s*</div>", normal) and re.search(r"Hånd 1 · Sol\s*</div>", normal)
        finally:
            client.post(f"/sessions/{sid}/delete")


def test_short_forms_show_only_on_phones():
    css = (Path(__file__).parents[1] / "src/whistapp/static/style.css").read_text(encoding="utf-8")
    assert ".short-form { display: none; }" in css
    phone = css[css.index(".short-form { display: none; }"):]
    assert re.search(r"@media \(max-width: 700px\) \{\s*\.full-form \{ display: none; \} \.short-form \{ display: inline; \}", phone)
