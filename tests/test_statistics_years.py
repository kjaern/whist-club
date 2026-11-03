from whistapp.statistics import club_statistics
from whistapp.database import init_db, connect


def test_statistics_years_and_self_partner(tmp_path, monkeypatch):
    import whistapp.database as database
    monkeypatch.setattr(database, 'DB_PATH', tmp_path / 'test.sqlite3')
    init_db()
    with connect() as db:
        db.execute("INSERT INTO sessions(id,title,created_at,is_club) VALUES(1,'Night','2026-10-01 12:00:00',1)")
        db.executemany('INSERT INTO club_members(id,name) VALUES(?,?)',
                       [(1,'A'),(2,'B'),(3,'C'),(4,'D'),(5,'E')])
        db.executemany('INSERT INTO players(id,session_id,name,member_id) VALUES(?,?,?,?)',
                       [(1,1,'A',1),(2,1,'B',2),(3,1,'C',3),(4,1,'D',4)])
        db.execute("INSERT INTO hands(id,session_id,bid_key,bid_label,target,won,rules_snapshot,partner_id) VALUES(1,1,'alm','Alm',8,1,'{}',1)")
        db.execute('INSERT INTO hand_declarers VALUES(1,1)')
        db.executemany('INSERT INTO hand_participants VALUES(1,?)',[(1,),(2,),(3,),(4,)])
        db.executemany('INSERT INTO hand_scores VALUES(1,?,?,1)',[(1,9),(2,-3),(3,-3),(4,-3)])
        sections=club_statistics(db,[('a','A'),('b','B'),('c','C'),('d','D'),('e','E')])
    assert [s['title'] for s in sections]==['Alle år','2026']
    assert len(sections[1]['players'])==4
    a=next(p for p in sections[1]['players'] if p['name']=='A')
    assert (a['bids'],a['self_partner'],a['eligible'],a['won'])==(1,1,1,1)
    assert a['self_pct']=='100,0 %'
    b=next(p for p in sections[1]['players'] if p['name']=='B')
    assert b['lost']==1 and b['partner']==0


def test_statistics_scoped_to_one_night(tmp_path, monkeypatch):
    import whistapp.database as database
    monkeypatch.setattr(database, 'DB_PATH', tmp_path / 'test.sqlite3')
    init_db()
    with connect() as db:
        db.executemany("INSERT INTO sessions(id,title,created_at,is_club) VALUES(?,?,?,1)",
                       [(1,'First','2026-01-01 12:00:00'),(2,'Second','2026-02-01 12:00:00')])
        db.execute("INSERT INTO club_members(id,name) VALUES(1,'A')")
        db.executemany('INSERT INTO players(id,session_id,name,member_id) VALUES(?,?,?,?)',
                       [(1,1,'A',1),(2,2,'A',1)])
        for sid in (1,2):
            db.execute('INSERT INTO hands(id,session_id,bid_key,bid_label,target,won,rules_snapshot) VALUES(?,?,?,?,?,?,?)',
                       (sid,sid,'sol','Sol',8,1,'{}'))
            db.execute('INSERT INTO hand_participants VALUES(?,?)',(sid,sid))
            db.execute('INSERT INTO hand_scores VALUES(?,?,?,1)',(sid,sid,5*sid))
        all_stats = club_statistics(db,[('a','A')])
        single = club_statistics(db,[('a','A')],session_id=2)
    assert all_stats[0]['players'][0]['hands']==2
    assert single[0]['players'][0]['hands']==1
    assert single[0]['players'][0]['points']=='10'
    assert single[0]['night_count']==1
