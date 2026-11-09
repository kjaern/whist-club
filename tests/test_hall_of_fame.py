"""Hall of Fame: every award from small nights whose winners can be worked out by hand."""
import hashlib
from fractions import Fraction

import pytest
from fastapi.testclient import TestClient

from whistapp.club import active_club_reset, active_club_set
from whistapp.database import connect, init_db
from whistapp.hall_of_fame import compute
from whistapp.stats.data import Scope, load
from whistapp.main import app
from whistapp.rule_versions import stamp_night
from test_clubs import _activate, _new_login

NAMES = {1: "Anna", 2: "Bo", 3: "Carl", 4: "Dorte", 5: "Erik"}


@pytest.fixture
def db(tmp_path, monkeypatch):
    import whistapp.database as database
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "hof.sqlite3")
    init_db()
    with connect() as db:
        yield db


def night(db, sid, day, hands=(), members=(1, 2, 3, 4), closed=True, origin="live", is_club=1,
          club_id=1, final=None):
    """A night on `day`; hands are dicts with scores per member and optional bid fields."""
    created = day if origin == "imported" else f"{day} 18:00:00"
    db.execute("INSERT INTO sessions(id,title,created_at,ended_at,is_club,club_id,origin) VALUES(?,?,?,?,?,?,?)",
               (sid, f"Aften {sid}", created, created if closed else None, is_club, club_id, origin))
    pid = {}
    for m in members:
        db.execute("INSERT OR IGNORE INTO club_members(id,name,club_id) VALUES(?,?,?)", (m, NAMES.get(m, f"M{m}"), club_id))
        pid[m] = db.execute("INSERT INTO players(session_id,name,member_id) VALUES(?,?,?)",
                            (sid, NAMES.get(m, f"M{m}"), m)).lastrowid
    for h in hands:
        scores = h["scores"]
        decl = h.get("decl", next(iter(scores)))
        partner = h.get("partner")
        hid = db.execute("""INSERT INTO hands(session_id,bid_key,bid_label,target,won,rules_snapshot,partner_id)
            VALUES(?,?,?,?,?,'{}',?)""", (sid, h.get("bid", "almindelige"), h.get("bid", "Almindelige"),
                                          h.get("target", 8), h.get("won"), pid[partner] if partner else None)).lastrowid
        db.execute("INSERT INTO hand_declarers VALUES(?,?)", (hid, pid[decl]))
        db.executemany("INSERT INTO hand_participants VALUES(?,?)", [(hid, pid[m]) for m in scores])
        db.executemany("INSERT INTO hand_scores VALUES(?,?,?,1)", [(hid, pid[m], s) for m, s in scores.items()])
        db.executemany("INSERT INTO special_bids VALUES(?,?,?,?)",
                       [(hid, pid[m], actual, won) for m, actual, won in h.get("specials", ())])
    for m, score in (final or {}).items():
        db.execute("INSERT INTO historical_final_scores VALUES(?,?,?,1)", (sid, pid[m], score))
    return pid


def ranked(*order):
    """One hand that ranks the members in this order, best first, all different."""
    n = len(order)
    return [dict(scores={m: 2 * (n - 1) - 4 * i for i, m in enumerate(order)})]


def hof(db, year=None, origin="all"):
    """The Hall of Fame for the lifetime or a year, from the statistics pages' own data."""
    return compute(load(db, Scope("year", year=year, origin=origin) if year else Scope(origin=origin)))


def holders(award):
    return [(h.name, h.value) for h in award.holders]


def test_empty_database_leaves_every_title_vacant(db):
    result = hof(db)
    assert result.nights == 0
    assert all(a.vacant for a in result.awards)
    assert all(a.explanation and a.empty for a in result.awards)
    assert [label for label, _a in result.records] == [
        "Flest førstepladser", "Længste sejrsrække (aftener)", "Længste sejrsrække (hænder)",
        "Længste taberrække (aftener)", "Længste taberrække (hænder)", "Største comeback", "Største nedtur",
        "Flest vundne Sol og Ren sol", "Flest vundne meldinger som makker"]


def test_one_night_gives_a_winner_but_no_king_or_nemesis(db):
    night(db, 1, "2026-03-01", ranked(2, 1, 3, 4))
    result = hof(db)
    assert result.nights == 1
    assert holders(result.award("sejre")) == [("Bo", 1)]
    assert result.award("sejre").holders[0].detail == "1 af 1 aftener (100,0 %)"
    for key in ("konge", "nemesis", "sejrsraekke", "taberraekke", "sejrsraekke_haender",
                "taberraekke_haender", "form", "toer", "jojo", "vovehals", "forsigtige"):
        assert result.award(key).vacant, key


def test_king_is_the_best_average_placement_score(db):
    night(db, 1, "2026-01-05", ranked(1, 2, 3, 4))
    night(db, 2, "2026-01-12", ranked(2, 1, 3, 4))
    night(db, 3, "2026-01-19", ranked(1, 3, 2, 4))
    king = hof(db, 2026).award("konge")
    assert king.title == "Årets konge"
    # Places score 1, 2/3, 1/3, 0. Anna: (1 + 2/3 + 1) / 3 = 8/9; Bo (2/3 + 1 + 1/3) / 3 = 2/3.
    assert holders(king) == [("Anna", Fraction(8, 9))]
    assert king.holders[0].value_text == "88,9 %"
    assert king.holders[0].detail == "3 aftener · 22,2 procentpoint foran nr. 2"
    assert hof(db).award("konge").title == "Tidernes konge"


def test_king_needs_three_nights(db):
    night(db, 1, "2026-01-05", ranked(1, 2, 3, 4))
    night(db, 2, "2026-01-12", ranked(1, 2, 3, 4))
    assert hof(db).award("konge").vacant


def test_tied_wins_share_the_title_and_a_shared_first_is_no_win(db):
    night(db, 1, "2026-01-05", ranked(1, 2, 3, 4))
    night(db, 2, "2026-01-12", ranked(2, 1, 3, 4))
    night(db, 3, "2026-01-19", [dict(scores={1: 5, 2: 5, 3: -5, 4: -5})])
    wins = hof(db).award("sejre")
    assert wins.shared and holders(wins) == [("Anna", 1), ("Bo", 1)]
    assert wins.holders[0].detail == "1 af 3 aftener (33,3 %) · 1 delte førstepladser"


def test_only_finished_club_nights_of_the_active_club_count(db):
    night(db, 1, "2026-01-05", ranked(1, 2, 3, 4))
    night(db, 2, "2026-01-12", ranked(2, 1, 3, 4), closed=False)  # still open
    night(db, 3, "2026-01-19", ranked(2, 1, 3, 4), is_club=0)  # a guest night
    night(db, 4, "2026-01-26", ranked(2, 1, 3, 4))
    db.execute("UPDATE sessions SET ended_at=NULL WHERE id=4")  # reopened
    night(db, 5, "2026-02-02", ranked(2, 1, 3, 4))
    db.execute("DELETE FROM sessions WHERE id=5")  # deleted
    db.execute("INSERT INTO clubs(id,name) VALUES(2,'Anden klub')")
    night(db, 6, "2026-02-09", ranked(7, 8, 9, 10), members=(7, 8, 9, 10), club_id=2)
    result = hof(db)
    assert result.nights == 1 and holders(result.award("sejre")) == [("Anna", 1)]
    token = active_club_set(2)
    try:
        assert holders(hof(db).award("sejre")) == [("M7", 1)]
    finally:
        active_club_reset(token)


def test_years_are_the_statistics_pages_years(db):
    night(db, 1, "2025-06-01", ranked(1, 2, 3, 4))
    night(db, 2, "2026-02-01", ranked(2, 1, 3, 4), origin="imported")
    # 23:30 UTC on New Year's Eve: the statistics pages (and so the Hall of Fame) put it in 2025.
    db.execute("INSERT INTO sessions(id,title,created_at,ended_at,is_club,club_id) "
               "VALUES(3,'Nytår','2025-12-31 23:30:00','2026-01-01 03:00:00',1,1)")
    pid = {m: db.execute("INSERT INTO players(session_id,name,member_id) VALUES(3,?,?)", (NAMES[m], m)).lastrowid
           for m in (1, 2, 3, 4)}
    hid = db.execute("INSERT INTO hands(session_id,bid_key,bid_label,target,rules_snapshot) "
                     "VALUES(3,'almindelige','Almindelige',8,'{}')").lastrowid
    for m, s in {3: 6, 1: 2, 2: -2, 4: -6}.items():
        db.execute("INSERT INTO hand_participants VALUES(?,?)", (hid, pid[m]))
        db.execute("INSERT INTO hand_scores VALUES(?,?,?,1)", (hid, pid[m], s))
    assert hof(db).nights == 3
    assert hof(db, 2026).nights == 1
    assert holders(hof(db, 2026).award("sejre")) == [("Bo", 1)]
    assert hof(db, 2025).nights == 2
    assert holders(hof(db, 2025).award("sejre")) == [("Anna", 1), ("Carl", 1)]
    empty = hof(db, 2024)
    assert empty.nights == 0 and all(a.vacant for a in empty.awards)  # never all-time instead


def test_live_or_imported_follows_the_statistics_choice(db):
    night(db, 1, "2026-01-05", ranked(1, 2, 3, 4))
    night(db, 2, "2026-02-01", ranked(2, 1, 3, 4), origin="imported")
    assert holders(hof(db, origin="live").award("sejre")) == [("Anna", 1)]
    assert holders(hof(db, origin="imported").award("sejre")) == [("Bo", 1)]
    assert hof(db).nights == 2


# Five nights where Anna is always ahead of Bo; no other pair is as one-sided.
RIVALRY = [(1, 2, 3, 4), (3, 1, 4, 2), (4, 3, 1, 2), (1, 4, 2, 3), (3, 4, 1, 2)]


def test_nemesis_needs_five_shared_nights(db):
    for i, order in enumerate(RIVALRY[:4], 1):
        night(db, i, f"2026-01-0{i}", ranked(*order, 5), members=(1, 2, 3, 4, 5))
    assert hof(db).award("nemesis").vacant
    night(db, 5, "2026-01-05", ranked(*RIVALRY[4]))  # Erik is absent: not a loss for him
    nemesis = hof(db).award("nemesis")
    assert [h.member_ids for h in nemesis.holders] == [(1, 2)]
    assert nemesis.holders[0].value_text == "5 af 5"
    assert nemesis.holders[0].detail == "Anna har slået Bo 5 gange på 5 fælles aftener (0 gange omvendt, 0 uafgjort)."


def test_nemesis_counts_ties_apart(db):
    for i, order in enumerate(RIVALRY, 1):
        night(db, i, f"2026-01-0{i}", ranked(*order))
    night(db, 6, "2026-01-06", [dict(scores={1: 3, 2: 3, 3: -3, 4: -3})])
    holder = hof(db).award("nemesis").holders[0]
    assert holder.value == (Fraction(5, 6), 5)
    assert holder.detail.endswith("på 6 fælles aftener (0 gange omvendt, 1 uafgjort).")


def _bids(member, specs):
    """Hands where `member` declares each (bid, target) in specs; nobody's score changes."""
    return [dict(scores={1: 0, 2: 0, 3: 0, 4: 0}, decl=member, partner=member if bid not in
                 ("sol", "ren_sol", "bordlaegger", "paskrig") else None, bid=bid, target=target, won=1,
                 specials=[(member, 0, 1)] if bid in ("sol", "ren_sol", "bordlaegger") else ())
            for bid, target in specs]


def test_daredevil_and_careful_use_the_same_classification(db):
    hands = _bids(1, [("almindelige", 10)] * 3 + [("sol", 9), ("bordlaegger", 11)] + [("almindelige", 8)] * 5)
    hands += _bids(2, [("gode", 11)] + [("halve", 9)] * 9)
    hands += _bids(3, [("vip2", 10)] * 9)  # nine bids: too few
    hands += _bids(4, [("sans", 9)] * 10 + [("paskrig", 0)])  # Påskrig is nobody's bid
    night(db, 1, "2026-01-05", hands)
    result = hof(db)
    dare = result.award("vovehals")
    assert holders(dare) == [("Anna", Fraction(1, 2))]
    assert dare.holders[0].detail == "5 vovede af 10 meldinger"
    careful = result.award("forsigtige")
    assert holders(careful) == [("Dorte", 0)]
    assert careful.holders[0].detail == "0 vovede af 10 meldinger"


def test_comeback_and_collapse_follow_the_running_score(db):
    night(db, 1, "2026-01-05", [
        dict(scores={1: -4, 2: 8, 3: -2, 4: -2}),
        dict(scores={1: -6, 2: 2, 3: 2, 4: 2}),
        dict(scores={1: 3, 2: -5, 3: 1, 4: 1}),
        dict(scores={1: 12, 2: -12, 3: 0, 4: 0}),
    ])
    # A final-only import has no hand order: its huge swing is not a comeback.
    night(db, 2, "2026-02-01", members=(1, 2, 3, 4), origin="imported", final={1: -90, 2: 0, 3: 100, 4: -10})
    result = hof(db)
    up = result.award("comeback")
    # Anna: 0, -4, -10, -7, +5: from -10 to +5.
    assert holders(up) == [("Anna", 15)]
    assert (up.holders[0].value_text, up.holders[0].detail) == ("+15", "fra laveste -10 til +5 point")
    assert up.holders[0].link == "/sessions/1"
    down = result.award("nedtur")
    # Bo: 0, +8, +10, +5, -7: from +10 to -7.
    assert holders(down) == [("Bo", 17)]
    assert (down.holders[0].value_text, down.holders[0].detail) == ("-17", "fra højeste +10 til -7 point")


def test_a_hand_sat_out_leaves_the_running_score(db):
    night(db, 1, "2026-01-05", [
        dict(scores={1: -5, 2: 5, 3: 5, 4: -5}),
        dict(scores={2: 3, 3: -1, 4: -1, 5: -1}),  # Anna sits out
        dict(scores={1: 9, 2: -3, 3: -3, 5: -3}),
    ], members=(1, 2, 3, 4, 5))
    assert holders(hof(db).award("comeback")) == [("Anna", 9)]


def test_eternal_second_prefers_those_who_never_won(db):
    for i in range(1, 3):
        night(db, i, f"2026-01-0{i}", ranked(1, 2, 3, 4))
    assert hof(db).award("toer").vacant  # two second places are not enough
    night(db, 3, "2026-01-03", ranked(1, 2, 3, 4))
    night(db, 4, "2026-01-04", ranked(3, 4, 1, 2))
    for i in range(5, 9):
        night(db, i, f"2026-01-0{i}", ranked(3, 4, 1, 2))
    # Dorte: five times nr. 2, never first. Bo: three times nr. 2, never first.
    toer = hof(db).award("toer")
    assert holders(toer) == [("Dorte", 5)]
    assert toer.holders[0].detail == "aldrig nr. 1 i perioden"


def test_kingmaker_counts_won_bids_as_partner_only(db):
    hand = lambda decl, partner, won, bid="almindelige": dict(  # noqa: E731
        scores={1: 0, 2: 0, 3: 0, 4: 0}, decl=decl, partner=partner, won=won, bid=bid)
    night(db, 1, "2026-01-05", [
        hand(1, 2, 1), hand(1, 2, 1), hand(1, 2, 1), hand(1, 2, 0), hand(3, 2, 1),
        hand(2, 2, 1),  # Bo as his own partner: not a partner's win
        hand(2, 2, None),
        hand(3, 1, 1), hand(4, 1, 1),
        dict(scores={1: 0, 2: 0, 3: 0, 4: 0}, decl=1, bid="sol", specials=[(1, 0, 1)]),
    ])
    award = hof(db).award("kongemager")
    assert holders(award) == [("Bo", 4)]
    assert award.holders[0].detail == "4 af 5 meldinger som makker"


def test_sun_king_ranks_by_wins_then_fewest_attempts(db):
    zero = {1: 0, 2: 0, 3: 0, 4: 0}
    sol = lambda bid, *specials: dict(scores=zero, decl=specials[0][0], bid=bid, specials=specials)  # noqa: E731
    night(db, 1, "2026-01-05", [
        sol("sol", (1, 0, 1), (2, 3, 0)), sol("sol", (1, 1, 1)), sol("sol", (1, 0, 1)), sol("sol", (1, 4, 0)),
        sol("ren_sol", (1, 0, 1)),
        sol("sol", (2, 0, 1)), sol("sol", (2, 0, 1)), sol("ren_sol", (2, 0, 1)), sol("ren_sol", (2, 0, 1)),
        sol("sol", (2, 5, 0)),
        sol("sol", (3, None, None)),  # no recorded outcome: not an attempt
        sol("bordlaegger", (3, 0, 1)),  # not Sol or Ren sol
    ])
    award = hof(db).award("solkonge")
    # Both won four; Anna needed five attempts, Bo six.
    assert holders(award) == [("Anna", 4)]
    assert award.holders[0].detail == "3 Sol og 1 Ren sol af 5 forsøg (80,0 %)"
    assert award.extra == ["Bedste succesrate (mindst 5 forsøg): 80,0 %, Anna (4 af 5)."]


def test_a_shared_best_sol_rate_shows_each_players_attempts(db):
    zero = {1: 0, 2: 0, 3: 0, 4: 0}
    sol = lambda m, won: dict(scores=zero, decl=m, bid="sol", specials=[(m, 0 if won else 3, won)])  # noqa: E731
    night(db, 1, "2026-01-05", [sol(1, 1)] * 4 + [sol(1, 0)] + [sol(2, 1)] * 8 + [sol(2, 0)] * 2)
    award = hof(db).award("solkonge")
    assert holders(award) == [("Bo", 8)]
    assert award.extra == ["Bedste succesrate (mindst 5 forsøg): 80,0 %, Anna (4 af 5) og Bo (8 af 10)."]


def test_yoyo_compares_consecutive_appearances_only(db):
    night(db, 1, "2026-01-01", ranked(1, 2, 3, 4))
    night(db, 2, "2026-01-02", ranked(2, 3, 4, 1))
    night(db, 3, "2026-01-03", ranked(2, 3, 4, 5), members=(2, 3, 4, 5))  # Anna is away
    night(db, 4, "2026-01-04", ranked(2, 1, 3, 4))
    night(db, 5, "2026-01-05", ranked(1, 2, 3, 4))
    # Anna has played four nights, one short; Bo, Carl and Dorte average 1/6 over five.
    assert holders(hof(db).award("jojo")) == [("Bo", Fraction(1, 6)), ("Carl", Fraction(1, 6)), ("Dorte", Fraction(1, 6))]
    night(db, 6, "2026-01-06", ranked(1, 2, 3, 4))
    award = hof(db).award("jojo")
    # Anna: 1, 0, 2/3, 1, 1: steps 1, 2/3, 1/3, 0, average 1/2. Bo, Carl, Dorte average 2/15.
    assert holders(award) == [("Anna", Fraction(1, 2))]
    assert award.holders[0].value_text == "50,0 procentpoint i snit"
    assert award.holders[0].detail == "Største spring: ned 100,0 % (2026-01-01) → 0,0 % (2026-01-02)"


def test_form_uses_the_last_five_nights(db):
    night(db, 1, "2026-01-01", ranked(2, 3, 4, 1))
    for i in range(2, 7):
        night(db, i, f"2026-01-0{i}", ranked(1, 2, 3, 4))
    award = hof(db).award("form")
    assert holders(award) == [("Anna", 1)]
    assert award.holders[0].detail == "Seneste placeringer: nr. 1 · nr. 1 · nr. 1 · nr. 1 · nr. 1"
    assert hof(db, 2025).award("form").vacant


def test_win_streak_counts_appearances_and_breaks_on_a_shared_first(db):
    night(db, 1, "2026-01-01", ranked(1, 2, 3, 4))
    night(db, 2, "2026-01-02", ranked(1, 2, 3, 4))
    night(db, 3, "2026-01-03", ranked(2, 3, 4, 5), members=(2, 3, 4, 5))  # Anna away: no break
    night(db, 4, "2026-01-04", ranked(1, 2, 3, 4))
    night(db, 5, "2026-01-05", [dict(scores={1: 5, 2: 5, 3: -5, 4: -5})])  # shared first breaks it
    night(db, 6, "2026-01-06", ranked(1, 2, 3, 4))
    result = hof(db)
    streak = result.award("sejrsraekke")
    assert holders(streak) == [("Anna", 3)]
    assert streak.holders[0].detail == "fra 2026-01-01 til 2026-01-04"
    assert holders(result.award("sejre")) == [("Anna", 4)]
    records = dict(result.records)
    assert records["Længste sejrsrække (aftener)"] is streak


def test_night_streak_shows_a_member_once_for_two_equal_runs(db):
    for i, order in enumerate([(1, 2, 3, 4), (1, 2, 3, 4), (2, 1, 3, 4), (1, 2, 3, 4), (1, 2, 3, 4)], 1):
        night(db, i, f"2026-01-0{i}", ranked(*order))
    streak = hof(db).award("sejrsraekke")
    assert holders(streak) == [("Anna", 2)]
    assert streak.holders[0].detail == "fra 2026-01-01 til 2026-01-02"


def test_lose_streak_counts_appearances_and_breaks_on_a_shared_last(db):
    night(db, 1, "2026-01-01", ranked(2, 3, 4, 1))
    night(db, 2, "2026-01-02", ranked(2, 3, 1), members=(1, 2, 3))  # last of three is a loss too
    night(db, 3, "2026-01-03", ranked(2, 3, 4, 5), members=(2, 3, 4, 5))  # Anna away: no break
    night(db, 4, "2026-01-04", ranked(2, 3, 4, 1))
    night(db, 5, "2026-01-05", [dict(scores={1: -5, 2: 5, 3: 5, 4: -5})])  # shared last breaks it
    night(db, 6, "2026-01-06", ranked(2, 3, 4, 1))
    night(db, 7, "2026-01-07", ranked(1, 2, 3, 4))
    night(db, 8, "2026-01-08", ranked(1, 2, 3, 4))
    result = hof(db)
    streak = result.award("taberraekke")
    assert holders(streak) == [("Anna", 3)]
    assert streak.holders[0].detail == "fra 2026-01-01 til 2026-01-04"
    assert streak.holders[0].value_text == "3 aftener i træk"
    # Dorte's two last places (nights 7 and 8) are a shorter streak; Bo's wins are unaffected.
    assert holders(result.award("sejrsraekke")) == [("Bo", 4)]
    assert dict(result.records)["Længste taberrække (aftener)"] is streak
    assert hof(db, 2025).award("taberraekke").vacant


def test_lose_streak_is_shared_on_equal_length(db):
    night(db, 1, "2026-01-01", ranked(1, 2, 4, 3))
    night(db, 2, "2026-01-02", ranked(1, 2, 4, 3))
    night(db, 3, "2026-01-03", ranked(1, 2, 3, 4))
    night(db, 4, "2026-01-04", ranked(1, 2, 3, 4))
    streak = hof(db).award("taberraekke")
    assert streak.shared
    assert holders(streak) == [("Carl", 2), ("Dorte", 2)]
    assert [h.detail for h in streak.holders] == ["fra 2026-01-01 til 2026-01-02", "fra 2026-01-03 til 2026-01-04"]


def test_hand_streaks_end_with_the_night_skip_sat_out_hands_and_break_on_zero(db):
    night(db, 1, "2026-01-01", [dict(scores={1: 3, 2: -1, 3: -1, 4: -1}),
                                dict(scores={1: 2, 2: -2, 3: 1, 4: -1}),
                                dict(scores={2: 4, 3: -2, 4: -2})])  # Anna sits out: no break
    night(db, 2, "2026-01-02", [dict(scores={1: 5, 2: -5, 3: 0, 4: 0}),  # a new night: Anna starts over
                                dict(scores={1: 0, 2: 0, 3: 1, 4: -1}),  # Anna's zero breaks hers
                                dict(scores={1: 1, 2: -1, 3: 1, 4: -1})])
    result = hof(db)
    wins, losses = result.award("sejrsraekke_haender"), result.award("taberraekke_haender")
    # Anna won the last two hands of night 1 she played and the first of night 2: two, not three.
    assert holders(wins) == [("Anna", 2), ("Carl", 2)]
    assert wins.holders[0].value_text == "2 hænder i træk"
    assert [h.detail for h in wins.holders] == ["aftenen 2026-01-01 · +5 point", "aftenen 2026-01-02 · +2 point"]
    assert holders(losses) == [("Dorte", 3)]
    assert losses.holders[0].detail == "aftenen 2026-01-01 · -4 point"
    records = dict(result.records)
    assert records["Længste sejrsrække (hænder)"] is wins
    assert records["Længste taberrække (hænder)"] is losses
    assert hof(db, 2025).award("sejrsraekke_haender").vacant



def test_members_who_left_keep_their_titles(db):
    night(db, 1, "2026-01-01", ranked(5, 2, 3, 4), members=(2, 3, 4, 5))
    db.execute("UPDATE club_members SET status='left' WHERE id=5")
    assert holders(hof(db).award("sejre")) == [("Erik", 1)]


def test_results_are_deterministic(db):
    for i, order in enumerate(RIVALRY, 1):
        night(db, i, f"2026-01-0{i}", ranked(*order))
    first, second = hof(db), hof(db)
    assert [(a.key, holders(a), a.extra) for a in first.awards] == [(a.key, holders(a), a.extra) for a in second.awards]


# --- the page, in the shared test home

def _dump():
    with connect() as db:
        return hashlib.sha256("\n".join(db.iterdump()).encode()).hexdigest()


def test_it_is_a_tab_on_the_statistics_pages_and_changes_nothing():
    with TestClient(app) as client:
        assert client.get("/statistics?topic=hall-of-fame", follow_redirects=False).status_code == 303
        client.post("/login", data={"username": "admin", "password": "a$$word"})
        home = client.get("/").text
        assert '<a href="/statistics">Statistik</a>' in home and "Klubstatistik →" not in home
        stats = client.get("/statistics").text
        assert 'topic=hall-of-fame" >🏆 Hall of Fame</a>' in stats and "hof-hero" not in stats
        before = _dump()
        page = client.get("/statistics?topic=hall-of-fame")
        year = client.get("/statistics/2020?origin=live&topic=hall-of-fame")
        assert _dump() == before
        assert page.status_code == year.status_code == 200
        assert 'class="active" aria-current="page">🏆 Hall of Fame' in page.text
        assert "Whistkongernes helte, syndebukke og legender" in page.text
        assert "Sådan beregnes det" in page.text and "Klubrekorder" in page.text
        assert "Ingen afsluttede klubaftener i 2020" in year.text
        assert client.get("/hall-of-fame").status_code == 404  # no page of its own


def test_the_year_and_origin_choice_pick_the_nights():
    members = (9001, 9002)
    with TestClient(app) as client:  # starting the app creates the shared test database
        with connect() as db:
            night(db, 9001, "2031-03-01", ranked(9001, 9002), members=members)
            night(db, 9002, "2031-04-01", ranked(9002, 9001), members=members, origin="imported")
            night(db, 9003, "2032-03-01", ranked(9002, 9001), members=members)
            db.execute("UPDATE club_members SET status='left' WHERE id IN (9001,9002)")
        try:
            client.post("/login", data={"username": "admin", "password": "a$$word"})
            live = client.get("/statistics/2031?origin=live&topic=hall-of-fame").text
            year = client.get("/statistics/2031?topic=hall-of-fame").text
            assert "Bygger på 1 afsluttede klubaftener i 2031" in live
            assert '<div class="hof-name">M9001</div>' in live and "Delt titel" not in live  # only M9001 won
            assert "Bygger på 2 afsluttede klubaftener i 2031" in year and "Delt titel" in year
        finally:
            with connect() as db:
                db.execute("DELETE FROM sessions WHERE id IN (9001,9002,9003)")
                db.execute("DELETE FROM club_members WHERE id IN (9001,9002)")


def test_a_night_has_no_hall_of_fame_tab():
    with TestClient(app) as client:
        client.post("/login", data={"username": "admin", "password": "a$$word"})
        with connect() as db:
            sid = db.execute("INSERT INTO sessions(title,club_id,is_club) VALUES ('HoF-aften',1,1)").lastrowid
            stamp_night(db, sid)
        try:
            page = client.get(f"/sessions/{sid}/statistics?topic=hall-of-fame").text
            assert "topic=hall-of-fame" not in page and "hof-hero" not in page
        finally:
            with connect() as db:
                db.execute("DELETE FROM sessions WHERE id=?", (sid,))


def test_every_club_member_may_view_it():
    with TestClient(app) as admin:
        admin.post("/login", data={"username": "admin", "password": "a$$word"})
        _member, temporary = _new_login(admin, "hof-member", "Hof Medlem")
    with TestClient(app) as member:
        _activate(member, "hof-member", temporary)
        page = member.get("/statistics?topic=hall-of-fame")
        assert page.status_code == 200 and "Whistkongernes helte" in page.text
