"""Zero-sum laws, checked by Hypothesis against many generated hands and nights.

Whatever the bid, the club's configured values or the tricks taken, points only
move between the players: every hand, and so every night, sums to exactly zero.
"""
from fractions import Fraction

import pytest
from hypothesis import given, settings, strategies as st

import whistapp.database as database
from whistapp.bids import get_bid
from whistapp.database import connect, init_db
from whistapp.finance import score_settlement_rows

ORDINARY = ("almindelige", "halve", "sans", "gode", "vip1", "vip2", "vip3")
SPECIAL = ("sol", "ren_sol", "bordlaegger")
ALL_BIDS = ORDINARY + SPECIAL + ("paskrig",)

tricks = st.integers(0, 13)
# Club-configured values: any exact fraction, including halves, zero and odd values.
values = st.fractions(min_value=-100, max_value=100, max_denominator=12)


@st.composite
def option_config(draw, key):
    """A club configuration for this bid, with every option set to a drawn value."""
    return {name: draw(values) for name in get_bid(key).options}


@st.composite
def hand(draw, players):
    """One legal hand: a bid, its configured values and the tricks, for four of `players`."""
    active = draw(st.permutations(players))[:4]
    key = draw(st.sampled_from(ALL_BIDS))
    config = draw(option_config(key))
    kwargs = dict(active=active, declarer=draw(st.sampled_from(active)))
    if key in ORDINARY:
        kwargs.update(partner=draw(st.sampled_from(active)), target=draw(st.integers(8, 13)), actual=draw(tricks))
        if key.startswith("vip"): kwargs["level"] = int(key[-1])
    elif key in SPECIAL:
        bidders = draw(st.lists(st.sampled_from(active), min_size=1, max_size=4, unique=True))
        kwargs["actuals"] = {pid: draw(tricks) for pid in bidders}
    else:
        kwargs["actuals"] = {pid: draw(tricks) for pid in active}
    return key, config, kwargs


def score(key, config, kwargs):
    return get_bid(key).score(config, **kwargs).scores


@settings(max_examples=500)
@given(st.lists(st.integers(1, 10_000), min_size=4, max_size=4, unique=True).flatmap(hand))
def test_every_hand_is_zero_sum(drawn):
    key, config, kwargs = drawn
    scores = score(key, config, kwargs)
    assert set(scores) == set(kwargs["active"])  # everyone at the table gets a score, nobody else
    assert all(type(s) is Fraction for s in scores.values())  # exact, never float
    assert sum(scores.values(), Fraction(0)) == 0


@pytest.fixture(scope="module")
def night_db(tmp_path_factory):
    """One database for the module; each Hypothesis example adds its own night."""
    path = tmp_path_factory.mktemp("laws") / "test.db"
    mp = pytest.MonkeyPatch(); mp.setattr(database, "DB_PATH", path)
    init_db()
    yield
    mp.undo()


@st.composite
def night(draw):
    """4-6 members on a night, each hand played by four of them."""
    members = draw(st.integers(4, 6))
    hands = draw(st.lists(hand(list(range(members))), max_size=25))
    return members, hands


@settings(max_examples=60, deadline=None)
@given(night(), st.fractions(min_value=0, max_value=10, max_denominator=4))
def test_night_totals_and_settlement_are_zero_sum(night_db, drawn, rate):
    members, hands = drawn
    with connect() as db:
        sid = db.execute("INSERT INTO sessions(title,is_club,club_id) VALUES ('Lov',1,1)").lastrowid
        mids = [db.execute("INSERT INTO club_members(name,club_id) VALUES (?,1)", (f"M{i}",)).lastrowid
                for i in range(members)]
        pids = [db.execute("INSERT INTO players(session_id,name,member_id) VALUES (?,?,?)",
                           (sid, f"M{i}", mid)).lastrowid for i, mid in enumerate(mids)]
        totals = {mid: Fraction(0) for mid in mids}
        for key, config, kwargs in hands:
            hid = db.execute("INSERT INTO hands(session_id,bid_key,bid_label,target,rules_snapshot) VALUES (?,?,?,0,'{}')",
                             (sid, key, key)).lastrowid
            for seat, s in score(key, config, kwargs).items():
                db.execute("INSERT INTO hand_scores VALUES (?,?,?,?)", (hid, pids[seat], s.numerator, s.denominator))
                totals[mids[seat]] += s

        # The night's final scores cancel out.
        assert sum(totals.values(), Fraction(0)) == 0

        # Settling between the players by final score: what is paid equals what is received.
        rows = score_settlement_rows(db, sid, "score_settle", rate)
        assert {mid for mid, *_ in rows} == set(mids)
        assert sum((Fraction(n, d) for _, _, n, d in rows), Fraction(0)) == 0
