"""Kort: the PCA and MDS maths, and the two member maps."""
import math

from whistapp.database import connect
from whistapp.stats import maps
from whistapp.stats.data import Scope, load
from whistapp.stats.projection import classical_mds, eigh, pca, standardize
from test_stats_bordet import _db, _hand, _night


def _close(a, b, tol=1e-6):
    return abs(a - b) < tol


def test_eigh_finds_eigenpairs_largest_first():
    values, vectors = eigh([[2, 1, 0], [1, 2, 0], [0, 0, 5]])
    assert [round(v, 6) for v in values] == [5, 3, 1]
    s = 1 / math.sqrt(2)
    assert all(_close(x, y) for x, y in zip(vectors[1], [s, s, 0]))  # sign: largest component positive
    m = [[4, 1, 2], [1, 3, 0], [2, 0, 1]]
    for value, vec in zip(*eigh(m)):
        mv = [sum(m[i][j] * vec[j] for j in range(3)) for i in range(3)]
        assert all(_close(a, value * b) for a, b in zip(mv, vec))


def test_pca_puts_a_line_on_one_axis():
    rows, kept = standardize([[1, 2, 7], [2, 4, 7], [3, 6, 7], [4, 8, 7]])
    assert kept == [0, 1]  # the column where everyone is equal is dropped
    coords, shares, _ = pca(rows)
    assert _close(shares[0], 1) and _close(shares[1], 0)
    assert all(_close(c[1], 0) for c in coords)


def test_mds_keeps_the_distances_of_a_square():
    d = math.sqrt(2)
    coords, shares = classical_mds([[0, 1, d, 1], [1, 0, 1, d], [d, 1, 0, 1], [1, d, 1, 0]])
    for i in range(4):
        for j in range(4):
            want = [[0, 1, d, 1], [1, 0, 1, d], [d, 1, 0, 1], [1, d, 1, 0]][i][j]
            assert _close(math.dist(coords[i], coords[j]), want)
    assert _close(shares[0] + shares[1], 1) and _close(shares[2], 0)


def test_seating_map_puts_neighbours_closer_than_opposites(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    monkeypatch.setattr(maps, 'MIN_HANDS', 1)
    with connect() as db:
        p = _night(db, 1, [1, 2, 3, 4])
        for _ in range(3):
            _hand(db, 1, p, [1, 2, 3, 4], [1, 1, -1, -1], 1, 2)
        data = load(db, Scope())
    seating = maps.seating_map(data)
    xyz = {pt['name']: (pt['x'], pt['y'], pt['z']) for pt in seating['points']}
    assert math.dist(xyz['M1'], xyz['M2']) < math.dist(xyz['M1'], xyz['M3'])
    # Always side by side, yet still two separate points (half a table apart, not zero).
    assert math.dist(xyz['M1'], xyz['M2']) > 0.3
    assert _close(math.dist(xyz['M1'], xyz['M3']), math.dist(xyz['M2'], xyz['M4']), 1e-3)
    # Every pair of neighbours sat together every hand; opposites never did.
    assert sorted((e['a'], e['b'], e['w']) for e in seating['edges']) == [(0, 1, 1), (0, 3, 1), (1, 2, 1), (2, 3, 1)]


def test_style_map_needs_three_members_with_enough_hands(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    monkeypatch.setattr(maps, 'MIN_HANDS', 2)
    with connect() as db:
        p = _night(db, 1, [1, 2, 3, 4, 5])
        four = [1, 2, 3, 4]
        _hand(db, 1, p, four, [4, 4, -4, -4], 1, 2, won=1)
        _hand(db, 1, p, four, [-3, 9, -3, -3], 2, 2, won=0)
        _hand(db, 1, p, four, [6, -2, -2, -2], 3, None, key='sol')
        _hand(db, 1, p, [1, 2, 3, 5], [2, 2, -2, -2], 3, 5, won=0)  # M5 plays only once
        data = load(db, Scope())
    found, hands = maps.profiles(data)
    assert sorted(found) == [1, 2, 3, 4] and hands[5] == 1
    assert found[2]['self'] == 1 and found[1]['won_partner'] is None and found[2]['won_partner'] == 1
    style = maps.style_map(data)
    assert [pt['name'] for pt in style['points']] == ['M1', 'M2', 'M3', 'M4']
    assert style['table']['rows'][0][:2] == ['M1', '4']
    monkeypatch.setattr(maps, 'MIN_HANDS', 5)  # nobody played five hands
    assert maps.style_map(data)['points'] == []


def test_pick_map_brings_every_number_and_the_defaults(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    monkeypatch.setattr(maps, 'MIN_HANDS', 1)
    with connect() as db:
        p = _night(db, 1, [1, 2, 3, 4])
        _hand(db, 1, p, [1, 2, 3, 4], [4, 4, -4, -4], 1, 2, won=1)
        _hand(db, 1, p, [1, 2, 3, 4], [-2, -2, 2, 2], 1, 3, won=0)
        data = load(db, Scope())
    pick = maps.pick_map(data)
    keys = [f['key'] for f in pick['fields']]
    assert pick['defaults'] == ['bids', 'won_bid', 'points'] and set(pick['defaults']) <= set(keys)
    m1 = next(pt for pt in pick['points'] if pt['name'] == 'M1')
    assert m1['values']['hands'] == 2 and m1['values']['bids'] == 1 and m1['values']['won_bid'] == 0.5
    assert set(m1['values']) == set(keys)
    # Nobody else declared: no win rate as declarer, which the map leaves out rather than guess.
    assert next(pt for pt in pick['points'] if pt['name'] == 'M2')['values']['won_bid'] is None


def test_partner_map_counts_both_roles_from_each_side(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    with connect() as db:
        p = _night(db, 1, [1, 2, 3, 4])
        four = [1, 2, 3, 4]
        _hand(db, 1, p, four, [4, 4, -4, -4], 1, 2, won=1)   # M1 declares with M2: won
        _hand(db, 1, p, four, [-3, -3, 3, 3], 2, 1, won=0)   # M2 declares with M1: lost
        _hand(db, 1, p, four, [2, -2, 2, -2], 1, 3, won=1)   # M1 with M3: won
        _hand(db, 1, p, four, [-9, 3, 3, 3], 1, 1, won=0)    # self-partner: not a pair
        _hand(db, 1, p, four, [6, -2, -2, -2], 1, None, key='sol')
        data = load(db, Scope())
    found = maps.partners(data)
    assert found[1][2] == dict(count=2, share=2 / 3, pct=0.5, pct_bid=1.0, pct_partner=0.0, points=0.5)
    assert found[2][1]['pct_bid'] == 0.0 and found[2][1]['pct_partner'] == 1.0
    assert found[1][3]['count'] == 1 and 4 not in found[1]
    partner = maps.partner_map(data)
    # Everyone who played can be picked: M2 and M3 had one partner, M4 none.
    assert [(g['name'], [pt['name'] for pt in g['points']]) for g in partner['groups']] == [
        ('M1', ['M2', 'M3']), ('M2', ['M1']), ('M3', ['M1']), ('M4', [])]
