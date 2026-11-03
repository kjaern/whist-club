import re
from fastapi.testclient import TestClient
from whistapp.main import app
from whistapp.database import connect


def test_admin_creates_account_and_user_changes_password():
    with TestClient(app) as admin:
        assert admin.post('/login', data={'username': 'admin', 'password': 'a$$word'}, follow_redirects=False).status_code == 303
        with connect() as db:
            member_id = db.execute("INSERT INTO club_members(name,status) VALUES ('Testmedlem','active')").lastrowid
        r = admin.post('/admin/accounts', data={'username': 'newmember', 'member_id': member_id})
        assert r.status_code == 200
        assert 'Engangsadgangskode' in r.text
        temporary = re.search(r'<input readonly value="([A-Za-z0-9_-]+)"', r.text)
        assert temporary
        pw = re.findall(r'<input readonly value="([A-Za-z0-9_-]+)"', r.text)[1]
        with connect() as db:
            user = db.execute('SELECT * FROM user_accounts WHERE username=?', ('newmember',)).fetchone()
            assert user['password_hash'] != pw
            assert db.execute('SELECT user_id FROM member_accounts WHERE member_id=?', (member_id,)).fetchone()[0] == user['id']
        with TestClient(app) as member:
            assert member.post('/login', data={'username':'newmember','password':pw}, follow_redirects=False).status_code == 303
            assert member.get('/club', follow_redirects=False).status_code == 303
            assert member.get('/account').status_code == 200
            assert member.post('/account/password', data={'current_password':pw, 'new_password':'eight123', 'confirm_password':'eight123'}, follow_redirects=False).headers['location'] == '/'
            assert 'Spilaftener' in member.get('/').text
            assert 'Testmedlem' in member.get('/').text
            assert member.get('/club', follow_redirects=False).status_code == 403
            # Members may start an evening (the empty form is rejected as invalid, not forbidden) but not edit club data.
            assert member.post('/sessions', data={}).status_code == 400
            assert member.post('/club', data={}).status_code == 403
            assert member.get('/account').status_code == 200
        reset = admin.post(f'/admin/accounts/{user["id"]}/reset')
        assert reset.status_code == 200
        with TestClient(app) as member:
            assert 'Forkert brugernavn' in member.post('/login', data={'username':'newmember','password':'eight123'}).text
