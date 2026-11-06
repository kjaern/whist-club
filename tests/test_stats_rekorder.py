"""Rekorder statistics: streaks, single hands and the club's rhythm."""
from fractions import Fraction

from whistapp.database import connect, init_db
from whistapp.stats.club_rhythm import ClubRhythm
from whistapp.stats.data import Scope, load
from whistapp.stats.extremes import Extremes
from whistapp.stats.member_hands import member_hands
from whistapp.stats.streaks import Streaks, longest
from test_stats_meldinger import _seed, _texts


def _load(tmp_path, monkeypatch, *nights, scope=Scope()):
    import whistapp.database as database
    monkeypatch.setattr(database, 'DB_PATH', tmp_path / 'test.sqlite3')
    init_db()
    with connect() as db:
        for sid, year in nights:
            _seed(db, sid=sid, year=year)
        return load(db, scope)


def test_runs():
    # A zero change ends a run; the most points need not come from the longest run.
    assert longest([2, 3, 0, 1], 1) == (2, 5)
    assert longest([-1, -2, 3, -5], -1) == (2, -5)
    assert longest([1, 2], -1) == (0, 0)


def test_streaks_and_single_hands(tmp_path, monkeypatch):
    data = _load(tmp_path, monkeypatch, (1, 2026))
    # Per hand: M1 +4 +3 +6 +2, M2 +4 -9 0 -2, M3 -4 +3 -6 +1, M4 -4 +3 0 -1.
    streaks = {r['name']: r for r in _texts(Streaks().compute(data))}
    assert streaks['M1'] == dict(name='M1', win='4', won='15', loss='0', lost='—')
    assert streaks['M2'] == dict(name='M2', win='1', won='4', loss='1', lost='-9')
    assert streaks['M3'] == dict(name='M3', win='1', won='3', loss='1', lost='-6')
    hands = {r['name']: r for r in _texts(Extremes().compute(data))}
    assert hands['M1'] == dict(name='M1', hands='4', average='3,75', best='6', worst='—', zero='0')
    assert hands['M2'] == dict(name='M2', hands='4', average='-1,75', best='4', worst='-9', zero='1')
    assert hands['M4']['zero'] == '1'


def test_hands_follow_the_night_dates(tmp_path, monkeypatch):
    # Night 1 was entered first but is dated after night 2; streaks follow the dates.
    data = _load(tmp_path, monkeypatch, (1, 2026), (2, 2025))
    m1 = next(iter(member_hands(data).values()))
    assert [h.session_id for h, _ in m1] == [2] * 4 + [1] * 4
    assert all(isinstance(change, Fraction) for _, change in m1)


def test_club_rhythm(tmp_path, monkeypatch):
    rows = _texts(ClubRhythm().compute(_load(tmp_path, monkeypatch, (1, 2026), (2, 2025))))
    assert [(r['what'], r['value']) for r in rows] == [
        ('Spilaftener', '2'), ('Hænder pr. aften', '4'), ('Selvmakker pr. aften', '1'),
        ('Sol, Ren sol og Bordlægger pr. aften', '1'), ('Dage mellem aftener', '365')]
    assert ClubRhythm.scopes == ('all', 'year')  # a single night has no rhythm
