import json
from fractions import Fraction

import pytest
from fastapi.testclient import TestClient

from whistapp.bids import get_bid
from whistapp.database import connect, init_db
from whistapp.finance import score_settlement_rows
from whistapp.main import app
from whistapp.rules import load_rules

ACTIVE = [1, 2, 3, 4]


@pytest.fixture(autouse=True, scope="module")
def database():
    init_db()


def test_default_values_are_the_original_house_rules():
    assert load_rules() == load_rules({})
    bids = load_rules()["bids"]
    assert [bids[k]["base_price"] for k in ("almindelige", "halve", "sans", "gode")] == [1, 2, 2, 2]
    assert [bids[k]["value"] for k in ("sol", "ren_sol", "bordlaegger", "paskrig")] == [9, 18, 36, 9]
    assert [bids[f"vip{n}"]["modifier"] for n in (1, 2, 3)] == [Fraction(1, 2), 1, 2]


def test_configured_values_reach_each_bid():
    settings = {
        "almindelige": {"options": {"base_price": "3/2"}},
        "sol": {"options": {"value": "12"}},
        "paskrig": {"options": {"value": "6"}},
        "vip": {"options": {"modifier_1": "1", "modifier_2": "1", "modifier_3": "3"}},
    }
    bids = load_rules(settings)["bids"]

    # price = (target - 6) / 2 * basePrice = 3/2 * 3/2; won with 9 tricks: price * 3.
    result = get_bid("almindelige").score(bids["almindelige"], active=ACTIVE, declarer=1,
                                          partner=2, target=9, actual=9)
    assert result.contract_points == Fraction(27, 4)
    assert bids["almindelige"]["values"][1] == Fraction(9, 4)

    sol = get_bid("sol").score(bids["sol"], active=ACTIVE, declarer=1, actuals={1: 0})
    assert sol.scores == {1: 12, 2: -4, 3: -4, 4: -4}

    paskrig = get_bid("paskrig").score(bids["paskrig"], active=ACTIVE, declarer=1,
                                      actuals={1: 5, 2: 3, 3: 3, 4: 2})
    assert paskrig.scores == {1: -6, 2: 2, 3: 2, 4: 2}

    # VIP3: (10 - 6) / 2 + 3 = 5 per trick over six.
    vip = get_bid("vip3").score(bids["vip3"], active=ACTIVE, declarer=1, partner=2,
                                target=10, actual=10, level=3)
    assert vip.contract_points == 20


def _night(db, scores, money_mode="placement"):
    """A finished-looking club night with one hand; returns (session id, member ids)."""
    sid = db.execute("INSERT INTO sessions(title,is_club,club_id,money_mode,point_rate) VALUES ('Penge',1,1,?,'1')",
                     (money_mode,)).lastrowid
    members, players = [], []
    for i, score in enumerate(scores):
        mid = db.execute("INSERT INTO club_members(name,status,club_id) VALUES (?,'paused',1)",
                         (f"Pengespiller {sid}-{i}",)).lastrowid
        members.append(mid)
        players.append(db.execute("INSERT INTO players(session_id,name,member_id,seat_order) VALUES (?,?,?,?)",
                                  (sid, f"P{i}", mid, i)).lastrowid)
    # The members are paused so the shared club keeps its roster, so the night gets its own
    # rule version with them on its medlemsliste (otherwise it would be a non-club night).
    rules = db.execute("SELECT placements,fejl40,bid_settings FROM club_settings WHERE id=1").fetchone()
    version = db.execute("""INSERT INTO club_rule_versions(club_id,number,created_by,placements,fejl40,
        money_mode,point_rate,bid_settings,member_ids)
        VALUES (1,(SELECT MAX(number)+1 FROM club_rule_versions WHERE club_id=1),'test',?,?,?,'1',?,?)""",
                         (json.dumps(list(range(len(scores)))), rules["fejl40"], money_mode,
                          rules["bid_settings"], json.dumps(members))).lastrowid
    db.execute("UPDATE sessions SET rule_version_id=? WHERE id=?", (version, sid))
    hid = db.execute("""INSERT INTO hands(session_id,bid_key,bid_label,target,rules_snapshot)
                        VALUES (?,'almindelige','Almindelige',8,'{}')""", (sid,)).lastrowid
    for pid, score in zip(players, scores):
        db.execute("INSERT INTO hand_scores VALUES (?,?,?,?)",
                   (hid, pid, Fraction(score).numerator, Fraction(score).denominator))
    return sid, members


def _cleanup(sid, members):
    with connect() as db:
        version = db.execute("SELECT rule_version_id FROM sessions WHERE id=?", (sid,)).fetchone()[0]
        db.execute("DELETE FROM sessions WHERE id=?", (sid,))
        db.execute("DELETE FROM club_rule_versions WHERE id=?", (version,))
        db.executemany("DELETE FROM club_members WHERE id=?", [(m,) for m in members])


@pytest.mark.parametrize("mode,rate,expected", [
    # Final scores 6, 2, -3, -5.
    ("score_settle", "1", [-6, -2, 3, 5]),
    ("score_losers", "1/2", [0, 0, Fraction(3, 2), Fraction(5, 2)]),
    ("score_shifted", "2", [0, 8, 18, 22]),
])
def test_score_money_modes(mode, rate, expected):
    with connect() as db:
        sid, members = _night(db, [6, 2, -3, -5])
        rows = score_settlement_rows(db, sid, mode, rate)
    try:
        amounts = {mid: Fraction(num, den) for mid, _place, num, den in rows}
        assert [amounts[m] for m in members] == expected
        assert [r[1] for r in rows] == [1, 2, 3, 4]
    finally:
        _cleanup(sid, members)


def test_ending_night_settles_players_between_themselves():
    with connect() as db:
        sid, members = _night(db, [4, 0, -1, -3], money_mode="score_settle")
    try:
        with TestClient(app) as client:
            client.post("/login", data={"username": "admin", "password": "a$$word"})
            client.post(f"/sessions/{sid}/end", data={"collect_club_money": "true"})
            page = client.get(f"/sessions/{sid}").text
        with connect() as db:
            paid = {r[0]: Fraction(r[1], r[2]) for r in db.execute(
                "SELECT member_id,amount_numerator,amount_denominator FROM club_member_settlements WHERE session_id=?",
                (sid,))}
        assert [paid[m] for m in members] == [-4, 0, 1, 3]
        assert "Spillerne afregner indbyrdes efter slutscore" in page
        assert "modtager 4 kr" in page
    finally:
        _cleanup(sid, members)


@pytest.fixture
def restore_club_rules():
    with connect() as db:
        before = tuple(db.execute("SELECT money_mode,point_rate,bid_settings FROM club_settings WHERE id=1").fetchone())
    yield
    with connect() as db:
        db.execute("UPDATE club_settings SET money_mode=?,point_rate=?,bid_settings=? WHERE id=1", before)


def _club_form(db, **extra):
    members = db.execute("SELECT id,name,status FROM club_members WHERE club_id=1").fetchall()
    settings = db.execute("SELECT placements,fejl40 FROM club_settings WHERE id=1").fetchone()
    data = {"placements": ",".join(str(x) for x in json.loads(settings["placements"])),
            "fejl40": str(settings["fejl40"])}
    for m in members:
        data[f"name_{m['id']}"] = m["name"]
        data[f"status_{m['id']}"] = "active" if m["status"] == "active" else "paused"
    data.update(extra)
    return data


def _bid_fields(**overrides):
    fields = {"bid_settings_form": "1"}
    defaults = {"almindelige": {"base_price": "1"}, "halve": {"base_price": "2"}, "sans": {"base_price": "2"},
                "gode": {"base_price": "2"}, "sol": {"value": "9"}, "ren_sol": {"value": "18"},
                "bordlaegger": {"value": "36"}, "paskrig": {"value": "9"},
                "vip": {"modifier_1": "0,5", "modifier_2": "1", "modifier_3": "2"}}
    for key, options in defaults.items():
        fields[f"bid_enabled_{key}"] = "1"
        for name, value in options.items():
            fields[f"bid_{key}_{name}"] = value
    fields.update(overrides)
    return fields


def test_club_chooses_bids_values_and_money_mode(restore_club_rules):
    with TestClient(app) as client:
        client.post("/login", data={"username": "admin", "password": "a$$word"})
        with connect() as db:
            form = _club_form(db, money_mode="score_losers", point_rate="0,5",
                              placements="1", **_bid_fields(bid_sol_value="12,5"))
        del form["bid_enabled_paskrig"]
        saved = client.post("/club/save", data=form, follow_redirects=False)
        assert saved.status_code == 303, saved.text
        with connect() as db:
            settings = db.execute("SELECT * FROM club_settings WHERE id=1").fetchone()
        assert (settings["money_mode"], settings["point_rate"]) == ("score_losers", "1/2")
        bid_settings = json.loads(settings["bid_settings"])
        assert bid_settings["paskrig"]["enabled"] is False
        assert bid_settings["sol"]["options"]["value"] == "25/2"

        page = client.get("/club").text
        assert 'value="12,5"' in page and 'name="bid_enabled_paskrig" value="1" >' in page
        # Only the fields for the chosen money mode are shown.
        assert '<div class="money-placement" hidden>' in page
        assert '<div class="money-score" >' in page

        # The rules dialog lists only enabled bids and shows the club's values with the source.
        assert "Paskrig" not in client.get("/").text.split("const rules = ")[1].split(";")[0]
        rules = client.get("/bids/sol/rules").text
        assert "Klubbens værdier" in rules and "12,5" in rules and "class Sol" in rules

        created = client.post("/sessions", data={"title": "Klubregler", "guests": "A\nB\nC\nD"},
                              follow_redirects=False)
        sid = int(created.headers["location"].split("/")[-1])
        with connect() as db:
            players = [r[0] for r in db.execute("SELECT id FROM players WHERE session_id=? ORDER BY id", (sid,))]
        try:
            night = client.get(f"/sessions/{sid}").text
            assert 'value="paskrig"' not in night.split('name="bid_key"')[1].split("</select>")[0]
            rejected = client.post(f"/sessions/{sid}/hands", data={
                "bid_key": "paskrig", "declarer": str(players[0]), "participants": [str(p) for p in players],
                **{f"actual_{p}": "3" for p in players}})
            assert rejected.status_code == 400
            sol = client.post(f"/sessions/{sid}/hands", data={
                "bid_key": "sol", "declarer": str(players[0]), "participants": [str(p) for p in players],
                f"actual_{players[0]}": "0"})
            assert sol.status_code == 200, sol.text

            # Later club changes do not alter a night that has already started.
            with connect() as db:
                db.execute("UPDATE club_settings SET bid_settings='{}' WHERE id=1")
            client.post(f"/sessions/{sid}/hands", data={
                "bid_key": "sol", "declarer": str(players[1]), "participants": [str(p) for p in players],
                f"actual_{players[1]}": "0"})
            with connect() as db:
                scores = [Fraction(r[0], r[1]) for r in db.execute(
                    """SELECT numerator,denominator FROM hand_scores hs JOIN hands h ON h.id=hs.hand_id
                       WHERE h.session_id=? AND hs.player_id IN (?,?) ORDER BY h.id""", (sid, players[0], players[1]))]
            assert Fraction(25, 2) in scores and scores.count(Fraction(25, 2)) == 2
        finally:
            client.post(f"/sessions/{sid}/delete")


def test_club_must_keep_at_least_one_bid(restore_club_rules):
    with TestClient(app) as client:
        client.post("/login", data={"username": "admin", "password": "a$$word"})
        with connect() as db:
            form = _club_form(db, money_mode="score_settle", point_rate="1", placements="1",
                              **_bid_fields())
        for key in list(form):
            if key.startswith("bid_enabled_"):
                del form[key]
        response = client.post("/club/save", data=form)
        assert "Mindst én melding skal være slået til." in response.text
        with connect() as db:
            assert db.execute("SELECT money_mode FROM club_settings WHERE id=1").fetchone()[0] != "score_settle"


def test_club_sets_short_bid_names_for_the_compact_table(restore_club_rules):
    from whistapp.rules import bid_short_names
    with TestClient(app) as client:
        client.post("/login", data={"username": "admin", "password": "a$$word"})
        with connect() as db:
            form = _club_form(db, **_bid_fields(bid_short_almindelige=" Alm ", bid_short_vip="V", bid_short_sol="Sol"))
        assert client.post("/club/save", data=form, follow_redirects=False).status_code == 303
        with connect() as db:
            bid_settings = json.loads(db.execute("SELECT bid_settings FROM club_settings WHERE id=1").fetchone()[0])
        # Stored only where it differs from the normal name.
        assert bid_settings["almindelige"]["short"] == "Alm" and bid_settings["vip"]["short"] == "V"
        assert "short" not in bid_settings["sol"] and "short" not in bid_settings["halve"]
        names = bid_short_names(bid_settings)
        assert (names["almindelige"], names["vip2"], names["sol"], names["halve"]) == ("Alm", "V2", "Sol", "Halve")
        page = client.get("/club").text
        assert 'name="bid_short_almindelige" value="Alm" placeholder="Almindelige"' in page

        # The hand table carries both names; the stacked table shows the short one.
        created = client.post("/sessions", data={"title": "Korte navne", "guests": "A\nB\nC\nD"}, follow_redirects=False)
        sid = int(created.headers["location"].split("/")[-1])
        try:
            with connect() as db:
                players = [str(r[0]) for r in db.execute("SELECT id FROM players WHERE session_id=? ORDER BY id", (sid,))]
            page = client.get(f"/sessions/{sid}").text
            base = page.split('name="base_hand_id" value="')[1].split('"')[0]
            client.post(f"/sessions/{sid}/hands", data={"bid_key": "almindelige", "target": "8", "declarer": players[0],
                        "partner": players[2], "actual": "8", "participants": players, "base_hand_id": base})
            for url in (f"/sessions/{sid}", f"/sessions/{sid}/live"):
                html = client.get(url).text
                assert '<span class="bid-name">Almindelige</span><span class="bid-short">Alm</span> <span class="bid-target">8</span>' in html
        finally:
            client.post(f"/sessions/{sid}/delete")

        # Too long a short name is refused with a message, and nothing is saved.
        with connect() as db:
            form = _club_form(db, **_bid_fields(bid_short_halve="Halvmeldinger"))
        refused = client.post("/club/save", data=form)
        assert "Kort navn for Halve må højst være 8 tegn." in refused.text
        with connect() as db:
            assert "short" not in json.loads(db.execute("SELECT bid_settings FROM club_settings WHERE id=1").fetchone()[0])["halve"]
