from fastapi.testclient import TestClient
from whistapp.main import app

def test_login_and_protected_pages():
    with TestClient(app) as c:
        assert c.get('/', follow_redirects=False).status_code == 303
        assert c.get('/sessions/1/editor/events', follow_redirects=False).status_code == 401
        assert c.post('/login', data={'username':'admin','password':'wrong'}).status_code == 200
        assert c.post('/login', data={'username':'admin','password':'a$$word'}, follow_redirects=False).status_code == 303
        assert c.get('/').status_code == 200
        assert c.post('/logout', follow_redirects=False).status_code == 303
        assert c.get('/', follow_redirects=False).status_code == 303
