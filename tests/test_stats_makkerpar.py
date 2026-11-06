"""Makkerpar statistics: pairs of members, and each member playing alone."""
from whistapp.database import connect, init_db
from whistapp.stats.data import Scope, load
from whistapp.stats.member_specials import MemberSpecials
from whistapp.stats.partner_pairs import PartnerPairs, ranks
from whistapp.stats.self_partner import SelfPartner
from test_stats_meldinger import _seed, _texts


def _data(tmp_path, monkeypatch):
    import whistapp.database as database
    monkeypatch.setattr(database, 'DB_PATH', tmp_path / 'test.sqlite3')
    init_db()
    with connect() as db:
        p = _seed(db)  # M1+M2 win 9 alm (+4), M2 self-partner loses (-9), Sol: M1 wins, M3 loses
        hid = db.execute("""INSERT INTO hands(session_id,bid_key,bid_label,target,won,rules_snapshot,partner_id)
            VALUES(1,'gode','Gode',8,0,'{}',?)""", (p[3],)).lastrowid
        db.execute("INSERT INTO hand_declarers VALUES(?,?)", (hid, p[2]))
        db.executemany("INSERT INTO hand_participants VALUES(?,?)", [(hid, x) for x in p])
        db.executemany("INSERT INTO hand_scores VALUES(?,?,?,1)", [(hid, x, s) for x, s in zip(p, [2, 2, -2, -2])])
        db.execute("UPDATE sessions SET collect_club_money=1 WHERE id=1")
        db.execute("INSERT INTO penalty_payments(session_id,player_id,amount) VALUES(1,?,40)", (p[3],))
        return load(db, Scope())


def test_partner_pairs_ranked_by_win_rate(tmp_path, monkeypatch):
    rows = _texts(PartnerPairs().compute(_data(tmp_path, monkeypatch)))
    # The self-partner hand and the Sol are not pairs.
    assert rows == [
        dict(pair='M1 + M2', count='1', won='1', lost='0', pct='100,0 %', points='4', average='4', rank='1'),
        dict(pair='M3 + M4', count='1', won='0', lost='1', pct='0,0 %', points='-2', average='-2', rank='2'),
    ]


def test_ties_share_a_place():
    rows = [dict(v=3), dict(v=5), dict(v=3), dict(v=None), dict(v=1)]
    ranks(rows, lambda r: r["v"])
    assert [r.get("rank") for r in rows] == [2, 1, 2, None, 4]


def test_alone_counts_only_sol_type_bids(tmp_path, monkeypatch):
    rows = {r['name']: r for r in _texts(MemberSpecials().compute(_data(tmp_path, monkeypatch)))}
    # Fejl40 is not a bid, and selvmakker has its own table: only M1 and M3 bid Sol.
    assert rows == {
        'M1': dict(name='M1', count='1', won='1', lost='0', pct='100,0 %', points='6', average='6'),
        'M3': dict(name='M3', count='1', won='0', lost='1', pct='0,0 %', points='-6', average='-6'),
    }


def test_self_partner_against_the_bids_that_allowed_it(tmp_path, monkeypatch):
    rows = {r['name']: r for r in _texts(SelfPartner().compute(_data(tmp_path, monkeypatch)))}
    # M1, M2 and M3 each declared one ordinary bid; only M2 chose themself, and lost.
    assert rows['M2'] == dict(name='M2', eligible='1', count='1', won='0', lost='1', pct='0,0 %',
                              points='-9', average='-9', share='100,0 %')
    assert (rows['M1']['eligible'], rows['M1']['count'], rows['M1']['share'], rows['M1']['points']) == ('1', '0', '0,0 %', '—')
    assert rows['M3']['eligible'] == '1' and 'M4' not in rows
