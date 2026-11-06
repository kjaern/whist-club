"""The statistics module: Meldinger statistics and the shared statistics pages."""
from fastapi.testclient import TestClient

from whistapp.database import connect, init_db
from whistapp.main import app
from whistapp.stats.base import cell
from whistapp.stats.bids_by_member import BidsByMember
from whistapp.stats.bids_played import BidsPlayed
from whistapp.stats.data import Scope, load
from whistapp.stats.special_multi import SpecialMulti
from test_clubs import _activate, _new_login, cleanup_clubs  # noqa: F401


def _seed(db, sid=1, year=2026, members=(1, 2, 3, 4)):
    """Four members; 9 Almindelige won and lost (self-partner), a Sol with two bidders, a paskrig."""
    db.execute("INSERT INTO sessions(id,title,created_at,is_club,club_id) VALUES(?,?,?,1,1)",
               (sid, 'Aften', f'{year}-03-01 18:00:00'))
    for m in members:
        db.execute("INSERT OR IGNORE INTO club_members(id,name,club_id) VALUES(?,?,1)", (m, f'M{m}'))
    p = [db.execute("INSERT INTO players(session_id,name,member_id) VALUES(?,?,?)", (sid, f'M{m}', m)).lastrowid
         for m in members]

    def hand(key, label, target, won, declarer, partner, scores, specials=()):
        hid = db.execute("""INSERT INTO hands(session_id,bid_key,bid_label,target,won,rules_snapshot,partner_id)
            VALUES(?,?,?,?,?,'{}',?)""", (sid, key, label, target, won, partner)).lastrowid
        db.execute("INSERT INTO hand_declarers VALUES(?,?)", (hid, declarer))
        db.executemany("INSERT INTO hand_participants VALUES(?,?)", [(hid, x) for x in p])
        db.executemany("INSERT INTO hand_scores VALUES(?,?,?,1)", [(hid, x, s) for x, s in zip(p, scores)])
        db.executemany("INSERT INTO special_bids VALUES(?,?,?,?)", [(hid, *s) for s in specials])

    hand('almindelige', 'Almindelige', 9, 1, p[0], p[1], [4, 4, -4, -4])
    hand('almindelige', 'Almindelige', 9, 0, p[1], p[1], [3, -9, 3, 3])
    hand('sol', 'Sol', 8, None, p[0], None, [6, 0, -6, 0], [(p[0], 0, 1), (p[2], 2, 0)])
    hand('paskrig', 'Påskrig', 0, None, p[0], None, [2, -2, 1, -1], [(x, 3, None) for x in p])
    return p


def _texts(table):
    return [{c.key: cell(row[c.key], c.fmt)[0] for c in table.columns} for row in table.rows]


def test_meldinger_count_each_bid_and_attempt(tmp_path, monkeypatch):
    import whistapp.database as database
    monkeypatch.setattr(database, 'DB_PATH', tmp_path / 'test.sqlite3')
    init_db()
    with connect() as db:
        _seed(db)
        data = load(db, Scope())
    played = BidsPlayed().compute(data)
    rows = {r['bid']: r for r in _texts(played)}
    # Every bid the club plays is listed, in score-book order, also when nobody bid it.
    assert list(rows) == ['9 Almindelige', 'Halve', 'VIP', 'VIP1', 'VIP2', 'VIP3', 'Sans', 'Gode',
                          'Sol', 'Ren sol', 'Bordlægger', 'Påskrig']
    assert rows['9 Almindelige'] == dict(bid='9 Almindelige', count='2', won='1', lost='1', pct='50,0 %',
                                         points='-5', average='-2,5')
    # Each Sol bidder's own attempt counts, with that bidder's points.
    assert rows['Sol'] == dict(bid='Sol', count='2', won='1', lost='1', pct='50,0 %', points='0', average='0')
    # Påskrig has no bidder and no outcome: counted, never given a result.
    assert rows['Påskrig'] == dict(bid='Påskrig', count='1', won='—', lost='—', pct='—', points='—', average='—')
    assert rows['Halve'] == dict(bid='Halve', count='0', won='—', lost='—', pct='—', points='—', average='—')
    assert cell(played.footer['count'], 'int')[0] == '5'

    by_member_table = BidsByMember().compute(data)
    # A column for every bid a member can make (not Påskrig, which has no bidder).
    assert [c.label for c in by_member_table.columns] == [
        'Spiller', 'Almindelige', 'Halve', 'VIP', 'VIP1', 'VIP2', 'VIP3', 'Sans', 'Gode', 'Sol', 'Ren sol',
        'Bordlægger', 'I alt', 'Andel', 'Sejr %']
    by_member = {r['name']: r for r in _texts(by_member_table)}
    assert {k: by_member['M1'][k] for k in ('almindelige', 'sol', 'halve', 'total', 'share', 'pct')} == \
        {'almindelige': '1 (100,0 %)', 'sol': '1 (100,0 %)', 'halve': '0', 'total': '2',
         'share': '50,0 %', 'pct': '100,0 %'}
    assert by_member['M2']['almindelige'] == '1 (0,0 %)' and by_member['M2']['sol'] == '0'
    assert by_member['M3']['sol'] == '1 (0,0 %)' and 'M4' not in by_member

    assert _texts(SpecialMulti().compute(data)) == [dict(size='2 meldere', hands='1')]


def test_scopes_limit_the_hands(tmp_path, monkeypatch):
    import whistapp.database as database
    monkeypatch.setattr(database, 'DB_PATH', tmp_path / 'test.sqlite3')
    init_db()
    with connect() as db:
        _seed(db, sid=1, year=2025)
        _seed(db, sid=2, year=2026)
        counts = {scope: len(load(db, scope).hands) for scope in
                  (Scope(), Scope('year', year=2025), Scope('night', session_id=2), Scope(origin='imported'))}
    assert list(counts.values()) == [8, 4, 4, 0]


def test_statistics_pages_for_every_scope_and_topic(cleanup_clubs):  # noqa: F811
    init_db()  # the shared test home, when this test runs on its own
    with connect() as db:
        sid = db.execute("SELECT COALESCE(MAX(id),0)+1000 FROM sessions").fetchone()[0]
        members = [db.execute("INSERT INTO club_members(name,club_id) VALUES (?,1)", (f'Statmedlem{i}',)).lastrowid
                   for i in range(4)]
        _seed(db, sid=sid, year=1997, members=members)
    try:
        with TestClient(app) as admin:
            admin.post('/login', data={'username': 'admin', 'password': 'a$$word'})
            for url in ('/statistics', '/statistics/1997', f'/sessions/{sid}/statistics'):
                for topic, expected in (('overblik', 'Mulige selvmakker'), ('meldinger', '9 Almindelige'),
                                        ('makkerpar', 'Statmedlem0 + Statmedlem1')):
                    page = admin.get(f'{url}?topic={topic}')
                    assert page.status_code == 200, (url, topic)
                    assert expected in page.text and 'Statmedlem0' in page.text, (url, topic)
            year = admin.get('/statistics/1997').text
            assert f'/sessions/{sid}/statistics' in year and '/statistics/1997/export.xlsx' in year
            assert f'/sessions/{sid}/statistics' in admin.get(f'/sessions/{sid}').text
            _, temporary = _new_login(admin, 'statsmember', 'Statlæser')
            # A second club only now: with two clubs the admin would first get the club picker.
            with connect() as db:
                other_club = db.execute("INSERT INTO clubs(name) VALUES ('Statklub')").lastrowid
            _, outsider = _new_login(admin, 'statsoutsider', 'Statfremmed', other_club)
        with TestClient(app) as member:
            _activate(member, 'statsmember', temporary)
            assert member.get(f'/sessions/{sid}/statistics?topic=meldinger').status_code == 200
        with TestClient(app) as stranger:
            _activate(stranger, 'statsoutsider', outsider)
            assert stranger.get(f'/sessions/{sid}/statistics').status_code == 404
    finally:
        with connect() as db:
            db.execute("DELETE FROM sessions WHERE id=?", (sid,))
            db.execute("UPDATE club_members SET status='paused' WHERE name IN ('Statlæser')")
            db.executemany("DELETE FROM club_members WHERE id=?", [(m,) for m in members])


def test_disabled_bids_are_left_out_unless_played(tmp_path, monkeypatch):
    import json
    import whistapp.database as database
    monkeypatch.setattr(database, 'DB_PATH', tmp_path / 'test.sqlite3')
    init_db()
    with connect() as db:
        _seed(db)
        db.execute("UPDATE club_settings SET bid_settings=? WHERE id=1",
                   (json.dumps({'halve': {'enabled': False}, 'almindelige': {'enabled': False}}),))
        data = load(db, Scope())
        night = load(db, Scope('night', session_id=1))  # the night's own snapshot: all bids
    bids = [r['bid'] for r in _texts(BidsPlayed().compute(data))]
    assert 'Halve' not in bids and '9 Almindelige' in bids
    assert 'Halve' in [r['bid'] for r in _texts(BidsPlayed().compute(night))]


def test_vip_levels_are_grouped_under_vip(tmp_path, monkeypatch):
    import whistapp.database as database
    monkeypatch.setattr(database, 'DB_PATH', tmp_path / 'test.sqlite3')
    init_db()
    with connect() as db:
        p = _seed(db)
        for key, label, target, won, score in (('vip1', 'VIP1', 9, 1, 5), ('vip2', 'VIP2', 9, 0, -6),
                                               ('vip2', 'VIP2', 10, 1, 8)):
            hid = db.execute("""INSERT INTO hands(session_id,bid_key,bid_label,target,won,rules_snapshot,partner_id)
                VALUES(1,?,?,?,?,'{}',?)""", (key, label, target, won, p[1])).lastrowid
            db.execute("INSERT INTO hand_declarers VALUES(?,?)", (hid, p[0]))
            db.executemany("INSERT INTO hand_participants VALUES(?,?)", [(hid, x) for x in p])
            db.executemany("INSERT INTO hand_scores VALUES(?,?,?,1)", [(hid, x, s) for x, s in zip(p, [score, score, -score, -score])])
        data = load(db, Scope())
    table = BidsPlayed().compute(data)
    rows = [(r['bid'].text, r['_class'], cell(r['count'], 'int')[0], cell(r['points'], 'points')[0]) for r in table.rows]
    vip = rows[rows.index(('VIP', 'group', '3', '7')):][:5]
    # VIP is the bid; under it each level (an input) by tricks, once, as in Hvem melder hvad.
    assert vip == [('VIP', 'group', '3', '7'), ('9 VIP1', 'sub1', '1', '5'),
                   ('9 VIP2', 'sub1', '1', '-6'), ('10 VIP2', 'sub1', '1', '8'), ('VIP3', 'sub1', '0', '—')]
    m1 = {r['name']: r for r in _texts(BidsByMember().compute(data))}['M1']
    assert (m1['vip'], m1['vip1'], m1['vip2'], m1['vip3']) == ('3 (66,7 %)', '1 (100,0 %)', '2 (50,0 %)', '0')
