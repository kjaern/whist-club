"""Placeringer statistics: places per night, and a year's nights side by side."""
from fractions import Fraction

from whistapp.database import connect, init_db
from whistapp.stats.data import Scope, load
from whistapp.stats.night_grid import NightGrid
from whistapp.stats.night_results import places
from whistapp.stats.placements import Placements
from test_stats_meldinger import _seed, _texts


def _load(tmp_path, monkeypatch, *nights, scope=Scope(), final_only=False):
    import whistapp.database as database
    monkeypatch.setattr(database, 'DB_PATH', tmp_path / 'test.sqlite3')
    init_db()
    with connect() as db:
        for sid, year in nights:
            _seed(db, sid=sid, year=year)
        if final_only:  # an imported night with final scores only: M2 wins, M1 last
            db.execute("INSERT INTO sessions(id,title,created_at,is_club,club_id,origin) VALUES(9,'Import','2026-09-01',1,1,'imported')")
            for member, score in ((1, -10), (2, 20), (3, -5), (4, -5)):
                pid = db.execute("INSERT INTO players(session_id,name,member_id) VALUES(9,?,?)", (f'M{member}', member)).lastrowid
                db.execute("INSERT INTO historical_final_scores VALUES(9,?,?,1)", (pid, score))
        return load(db, scope)


def test_shared_places():
    assert places({'a': 5, 'b': 5, 'c': 1, 'd': -1}) == {
        'a': ('1/2', Fraction(3, 2)), 'b': ('1/2', Fraction(3, 2)), 'c': ('3', 3), 'd': ('4', 4)}
    assert {k: v[0] for k, v in places({'a': 9, 'b': 0, 'c': 0, 'd': 0}).items()} == \
        {'a': '1', 'b': '2/3/4', 'c': '2/3/4', 'd': '2/3/4'}


def test_placements(tmp_path, monkeypatch):
    # Seed night totals: M1 +15, M4 -2, M3 -6, M2 -7. The import night ties M3 and M4 at 2/3.
    rows = {r['name']: r for r in _texts(Placements().compute(_load(tmp_path, monkeypatch, (1, 2026), final_only=True)))}
    assert rows['M1'] == {'name': 'M1', 'nights': '2', 'p1': '1', 'p2': '0', 'p2/3': '0', 'p3': '0', 'p4': '1', 'average': '2,5'}
    assert (rows['M2']['p1'], rows['M2']['p4'], rows['M2']['average']) == ('1', '1', '2,5')
    assert (rows['M3']['p3'], rows['M3']['p2/3'], rows['M3']['average']) == ('1', '1', '2,75')
    assert (rows['M4']['p2'], rows['M4']['p2/3'], rows['M4']['average']) == ('1', '1', '2,25')


def test_year_grid(tmp_path, monkeypatch):
    data = _load(tmp_path, monkeypatch, (1, 2026), (2, 2026), scope=Scope('year', year=2026))
    table = NightGrid().compute(data)
    assert [c.label for c in table.columns] == ['Spiller', '1/3', '1/3', 'Sum']
    rows = {r['name']: r for r in _texts(table)}
    assert (rows['M1']['n1'], rows['M1']['n2'], rows['M1']['total']) == ('15', '15', '30')
    assert rows['M2']['total'] == '-14'
    assert NightGrid.scopes == ('year',)
