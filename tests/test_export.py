"""Excel export of a night over HTTP."""
from io import BytesIO

from fastapi.testclient import TestClient
from openpyxl import load_workbook

from whistapp.database import connect
from whistapp.main import app


def _night_with_hand(client, guests='Anne\nBo\nClara\nDan'):
    created = client.post('/sessions', data={
        'title': 'Eksportaften', 'rotation_mode': 'consecutive', 'guests': guests,
    }, follow_redirects=False)
    sid = int(created.headers['location'].split('/')[-1])
    with connect() as db:
        players = [r[0] for r in db.execute('SELECT id FROM players WHERE session_id=? ORDER BY id', (sid,))]
    response = client.post(f'/sessions/{sid}/hands', data={
        'bid_key': 'almindelige', 'target': '8', 'actual': '9',
        'declarer': str(players[0]), 'partner': str(players[1]),
        'participants': [str(p) for p in players[:4]],
    })
    assert response.status_code == 200, response.text
    return sid


def test_export_night_downloads_workbook():
    with TestClient(app) as client:
        client.post('/login', data={'username': 'admin', 'password': 'a$$word'})
        sid = _night_with_hand(client)
        try:
            response = client.get(f'/sessions/{sid}/export.xlsx')
            assert response.status_code == 200, response.text[:2000]
            wb = load_workbook(BytesIO(response.content))
            assert len(wb.worksheets) == 1
            assert wb.active.cell(2, 1).value == 1
        finally:
            client.post(f'/sessions/{sid}/delete')


def test_export_night_with_five_players_includes_the_player_sitting_out():
    # Regression: nights with a fifth player crashed the export with a 500.
    with TestClient(app) as client:
        client.post('/login', data={'username': 'admin', 'password': 'a$$word'})
        sid = _night_with_hand(client, 'Anne\nBo\nClara\nDan\nEva')
        try:
            response = client.get(f'/sessions/{sid}/export.xlsx')
            assert response.status_code == 200, response.text[:2000]
            sheet = load_workbook(BytesIO(response.content)).active
            headers = [c.value for c in sheet[1]]
            assert 'Eva Δ' in headers and 'Eva total' in headers
            assert sheet.cell(2, headers.index('Eva Δ') + 1).value == 0
            assert sum(sheet.cell(2, headers.index(f'{n} Δ') + 1).value for n in ('Anne', 'Bo', 'Clara', 'Dan', 'Eva')) == 0
        finally:
            client.post(f'/sessions/{sid}/delete')


def test_export_year_has_overview_and_one_sheet_per_night_oldest_first():
    # Regression: the whole-year Excel export had gone missing.
    with TestClient(app) as client:
        client.post('/login', data={'username': 'admin', 'password': 'a$$word'})
        sids = [_night_with_hand(client) for _ in range(2)]
        with connect() as db:
            members = [db.execute("INSERT INTO club_members(name,club_id) VALUES (?,1)", (f'Årsmedlem{i}',)).lastrowid
                       for i in range(4)]
            for day, sid in zip(('1999-03-01', '1999-02-01'), sids):
                db.execute("UPDATE sessions SET is_club=1,created_at=? WHERE id=?", (f'{day} 18:00:00', sid))
                players = [r[0] for r in db.execute('SELECT id FROM players WHERE session_id=? ORDER BY id', (sid,))]
                for pid, mid in zip(players, members):
                    db.execute("UPDATE players SET member_id=? WHERE id=?", (mid, pid))
        try:
            assert '/statistics/1999/export.xlsx' in client.get('/statistics/1999').text
            response = client.get('/statistics/1999/export.xlsx')
            assert response.status_code == 200, response.text[:2000]
            assert 'whist-1999.xlsx' in response.headers['content-disposition']
            wb = load_workbook(BytesIO(response.content))
            # Same title twice: sheet names stay unique; oldest night first.
            assert wb.sheetnames == ['Oversigt', '1999-02-01 Eksportaften', '1999-03-01 Eksportaften']
            overview = [[c.value for c in row] for row in wb['Oversigt'].iter_rows(min_row=3)]
            assert overview == [['1999-02-01', 'Eksportaften', 1, '1999-02-01 Eksportaften'],
                                ['1999-03-01', 'Eksportaften', 1, '1999-03-01 Eksportaften']]
            night = wb['1999-02-01 Eksportaften']
            assert night.cell(1, 7).value == 'Årsmedlem0 Δ' and night.cell(2, 1).value == 1
        finally:
            for sid in sids:
                client.post(f'/sessions/{sid}/delete')
            with connect() as db:
                db.executemany("DELETE FROM club_members WHERE id=?", [(m,) for m in members])


def test_ordinary_member_can_download_year_export():
    from test_clubs import _activate, _new_login
    with TestClient(app) as admin:
        admin.post('/login', data={'username': 'admin', 'password': 'a$$word'})
        member_id, temporary = _new_login(admin, 'yearexport', 'Årseksport')
    try:
        with TestClient(app) as member:
            _activate(member, 'yearexport', temporary)
            assert member.get('/statistics/1998/export.xlsx').status_code == 200
    finally:
        with connect() as db:
            db.execute("UPDATE club_members SET status='paused' WHERE id=?", (member_id,))
