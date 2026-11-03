"""Exercise the actual HTTP/database path, not only the bid calculators."""
import pytest
from fastapi.testclient import TestClient
from whistapp.main import app
from whistapp.database import connect


@pytest.mark.parametrize('key,target,actual,expected', [
    ('almindelige', 10, 10, 8),
    ('vip1', 9, 10, 8),
    ('vip2', 9, 10, 10),
    ('vip3', 10, 10, 16),
    ('vip3', 10, 11, 20),
    ('vip3', 10, 9, -8),
])
def test_submit_hand_and_delete_night(key, target, actual, expected):
    with TestClient(app) as client:
        client.post('/login', data={'username': 'admin', 'password': 'a$$word'})
        created = client.post('/sessions', data={
            'title': 'Temporary regression', 'rotation_mode': 'consecutive',
            'guests': 'Anne\nBo\nClara\nDan',
        }, follow_redirects=False)
        assert created.status_code == 303
        sid = int(created.headers['location'].split('/')[-1])
        with connect() as db:
            players = [r[0] for r in db.execute('SELECT id FROM players WHERE session_id=? ORDER BY id', (sid,))]
        response = client.post(f'/sessions/{sid}/hands', data={
            'bid_key': key, 'target': str(target), 'actual': str(actual),
            'declarer': str(players[0]), 'partner': str(players[1]),
            'participants': [str(p) for p in players],
        })
        assert response.status_code == 200, response.text
        with connect() as db:
            row = db.execute('SELECT contract_points FROM hands WHERE session_id=?', (sid,)).fetchone()
            assert row is not None
            assert row[0] == expected
        assert client.post(f'/sessions/{sid}/delete', follow_redirects=False).status_code == 303
        with connect() as db:
            assert db.execute('SELECT 1 FROM sessions WHERE id=?', (sid,)).fetchone() is None
