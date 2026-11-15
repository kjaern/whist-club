"""Bordet statistics: neighbours, the partner's seat, fixed tables and the seat from the dealer."""
from whistapp.database import connect, init_db
from whistapp.rule_versions import stamp_night
from whistapp.stats import neighbours
from whistapp.stats.data import Scope, load
from whistapp.stats.dealer_seat import DealerSeat
from whistapp.stats.neighbours import Neighbours
from whistapp.stats.partner_seat import PartnerSeat
from whistapp.stats.seatings import Seatings
from test_stats_meldinger import _texts


def _db(tmp_path, monkeypatch):
    import whistapp.database as database
    monkeypatch.setattr(database, 'DB_PATH', tmp_path / 'test.sqlite3')
    init_db()


def _night(db, sid, seats, day=1):
    """A night with players in the given seat order: member ids, or None for a guest."""
    db.execute("INSERT INTO sessions(id,title,created_at,is_club,club_id) VALUES(?,?,?,1,1)",
               (sid, 'Aften', f'2026-03-{day:02d} 18:00:00'))
    stamp_night(db, sid)
    ids = {}
    for seat, m in enumerate(seats):
        if m is not None:
            db.execute("INSERT OR IGNORE INTO club_members(id,name,club_id) VALUES(?,?,1)", (m, f'M{m}'))
        ids[m] = db.execute("INSERT INTO players(session_id,name,member_id,seat_order) VALUES(?,?,?,?)",
                            (sid, f'M{m}' if m else 'Gæst', m, seat)).lastrowid
    return ids


def _hand(db, sid, p, players, scores, declarer, partner=None, won=1, key='almindelige', dealer=None):
    hid = db.execute("""INSERT INTO hands(session_id,bid_key,bid_label,target,won,rules_snapshot,partner_id,dealer_id)
        VALUES(?,?,?,9,?,'{}',?,?)""", (sid, key, key.title(), won, partner and p[partner], dealer and p[dealer])).lastrowid
    db.execute("INSERT INTO hand_declarers VALUES(?,?)", (hid, p[declarer]))
    db.executemany("INSERT INTO hand_participants VALUES(?,?)", [(hid, p[m]) for m in players])
    db.executemany("INSERT INTO hand_scores VALUES(?,?,?,1)", [(hid, p[m], s) for m, s in zip(players, scores)])


def test_neighbours_follow_the_seats_of_who_played(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    monkeypatch.setattr(neighbours, 'MIN_TOGETHER', 2)
    with connect() as db:
        p = _night(db, 1, [1, 2, None, 3, 4])
        # M4 sits out: M1 has M2 after and M3 (round the table) before; M2 has the guest after.
        _hand(db, 1, p, [1, 2, None, 3], [3, 3, -3, -3], 1, 2)
        # The guest sits out: M1 has M2 after and M4 before.
        _hand(db, 1, p, [1, 2, 3, 4], [-1, -1, 1, 1], 1, 2, won=0)
        data = load(db, Scope())
    rows = {r['name']: r for r in _texts(Neighbours().compute(data))}
    assert rows['M1'] == dict(name='M1', hands='2', after='M2 (100,0 %)', before='M3 (50,0 %)', average='1',
                              lucky='M2 (1)', unlucky='—')
    # The guest takes a seat but is never named: M2's only member after them is M3.
    assert (rows['M2']['after'], rows['M2']['before']) == ('M3 (50,0 %)', 'M1 (100,0 %)')
    assert (rows['M3']['after'], rows['M3']['before']) == ('M1 (50,0 %)', 'M2 (50,0 %)')
    assert 'Gæst' not in rows and rows['M4']['hands'] == '1'


def test_partner_seat_left_opposite_right(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    with connect() as db:
        p = _night(db, 1, [1, 2, 3, 4, 5])
        four = [1, 2, 3, 4]
        _hand(db, 1, p, four, [2, 2, -2, -2], 1, 2, won=1)        # partner to the left
        _hand(db, 1, p, four, [-2, 2, -2, 2], 1, 3, won=0)        # opposite
        _hand(db, 1, p, four, [2, -2, -2, 2], 1, 4, won=1)        # to the right
        _hand(db, 1, p, four, [2, -2, -2, 2], 3, 4, won=1)        # M3 with M4 to the left
        _hand(db, 1, p, [1, 2, 3, 4, 5], [2, 2, -2, -2, 0], 1, 2)  # five at the table: left out
        _hand(db, 1, p, four, [6, -2, -2, -2], 1, None, key='sol')  # no partner: left out
        _hand(db, 1, p, four, [3, -1, -1, -1], 2, 2)              # self-partner: left out
        data = load(db, Scope())
    table = PartnerSeat().compute(data)
    assert _texts(table) == [
        dict(name='M1', count='3', left='100,0 % (1)', opposite='0,0 % (1)', right='100,0 % (1)'),
        dict(name='M3', count='1', left='100,0 % (1)', opposite='—', right='—'),
    ]
    assert table.footer['count'] == 4 and table.footer['left'].text == '100,0 % (2)'


def _orders(table):
    return [(r['table'], r['hands'], r['nights'], r['last']) for r in _texts(table)]


def test_playing_order_counts_rotations_as_one(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    with connect() as db:
        for sid, seats in enumerate([[1, 2, 3, 4], [3, 4, 1, 2], [1, 3, 2, 4], [2, 3, 4, 1]], start=1):
            p = _night(db, sid, seats, day=sid)
            _hand(db, sid, p, seats, [1, 1, -1, -1], seats[0], seats[1])
        _night(db, 5, [1, 2], day=5)  # no hands: no order
        data = load(db, Scope())
    table = Seatings().compute(data)
    assert _orders(table) == [('M1 → M2 → M3 → M4', '3', '3', '4/3/2026'), ('M1 → M3 → M2 → M4', '1', '1', '3/3/2026')]
    # Each member's average per hand in that order; the first two seated won each hand.
    rows = _texts(table)
    assert [c.label for c in table.columns] == ['Rækkefølge', 'Hænder', 'Aftener', 'Senest', 'M1', 'M2', 'M3', 'M4']
    assert [rows[0][f'm{m}'] for m in (1, 2, 3, 4)] == ['-0,33', '0,33', '0,33', '-0,33']
    assert [rows[1][f'm{m}'] for m in (1, 2, 3, 4)] == ['1', '-1', '1', '-1']


def test_playing_order_follows_who_played_each_hand(tmp_path, monkeypatch):
    """A member who sits over or has gone home is not in the hand's order."""
    _db(tmp_path, monkeypatch)
    with connect() as db:
        p = _night(db, 1, [1, 2, 3, 5, 4])
        _hand(db, 1, p, [1, 2, 3, 5], [2, 2, -2, -2], 1, 2)   # M4 sits over
        _hand(db, 1, p, [2, 3, 5, 4], [3, 3, -3, -3], 2, 3)   # M1 sits over
        _hand(db, 1, p, [1, 2, 3, 4], [4, -4, 4, -4], 1, 3)   # M5 sits over
        for _ in range(2):                                    # M5 has gone home
            _hand(db, 1, p, [1, 2, 3, 4], [1, 1, -1, -1], 1, 2)
        g = _night(db, 2, [1, 2, 3, None], day=2)
        _hand(db, 2, g, [1, 2, 3, None], [9, 9, -9, -9], 1, 2)  # a guest played: left out
        data = load(db, Scope())
    table = Seatings().compute(data)
    assert _orders(table) == [
        ('M1 → M2 → M3 → M4', '3', '1', '1/3/2026'),
        ('M1 → M2 → M3 → M5', '1', '1', '1/3/2026'),
        ('M2 → M3 → M5 → M4', '1', '1', '1/3/2026'),
    ]
    rows = _texts(table)
    assert [rows[0][f'm{m}'] for m in (1, 2, 3, 4, 5)] == ['2', '-0,67', '0,67', '-2', '—']
    assert [rows[2][f'm{m}'] for m in (1, 2, 3, 4, 5)] == ['—', '3', '3', '-3', '-3']
    # A single night shows it too.
    night = load_night(1)
    assert _orders(Seatings().compute(night))[0] == ('M1 → M2 → M3 → M4', '3', '1', '1/3/2026')


def load_night(sid):
    with connect() as db:
        return load(db, Scope('night', session_id=sid))


def test_who_sat_where_is_never_graphed(tmp_path, monkeypatch):
    from whistapp.stats.trends import trend
    _db(tmp_path, monkeypatch)
    with connect() as db:
        for sid in (1, 2):
            p = _night(db, sid, [1, 2, 3, 4], day=sid)
            _hand(db, sid, p, [1, 2, 3, 4], [1, 1, -1, -1], 1, 2)
        db.execute("UPDATE sessions SET created_at='2025-03-01 18:00:00' WHERE id=1")
        data = load(db, Scope())
    # The share behind 'Oftest efter dig' belongs to a different neighbour each year.
    graphs = trend(Neighbours(), Neighbours().compute(data), data)
    assert set(graphs['columns']) == {'hands', 'average'}
    assert trend(PartnerSeat(), PartnerSeat().compute(data), data)['columns'].keys() == {'count'}


def test_dealer_seat_counts_from_the_dealer(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    with connect() as db:
        p = _night(db, 1, [1, 2, 3, 4])
        four = [1, 2, 3, 4]
        _hand(db, 1, p, four, [4, 4, -4, -4], 1, 2, won=1, dealer=4)  # M1 is forhånd
        _hand(db, 1, p, four, [2, 2, -2, -2], 3, 4, won=0, dealer=1)  # M3 is 2. efter giver
        _hand(db, 1, p, four, [9, 9, -9, -9], 1, 2)                   # no dealer: left out
        data = load(db, Scope())
    table = DealerSeat().compute(data)
    assert _texts(table) == [
        dict(seat='Forhånd (1. efter giver)', declared='1', share='50,0 %', pct='100,0 %', average='3'),
        dict(seat='2. efter giver', declared='1', share='50,0 %', pct='0,0 %', average='1'),
        dict(seat='3. efter giver', declared='0', share='0,0 %', pct='—', average='-3'),
        dict(seat='Giver', declared='0', share='0,0 %', pct='—', average='-1'),
    ]
    assert table.footer['seat'].text == '2 hænder'
