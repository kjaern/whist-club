from fastapi.testclient import TestClient
from whistapp.main import app

def test_live_scoreboard_is_read_only_and_polls():
    client = TestClient(app)
    with client:
        assert client.post("/login", data={"username":"admin","password":"a$$word"}, follow_redirects=False).status_code == 303
        response = client.post('/sessions', data={'title':'Live test','rotation_mode':'consecutive','guests':'Anne\nBo\nClara\nDan'}, follow_redirects=False)
        assert response.status_code in (302,303)
        url = response.headers['location']
        sid = url.rstrip('/').split('/')[-1]
        live = client.get(f'/sessions/{sid}/live')
        assert live.status_code == 200
        assert 'every 3s' in live.text
        assert 'Ret</summary>' not in live.text
        partial = client.get(f'/sessions/{sid}/live/scoreboard')
        assert partial.status_code == 200
        assert 'Total' in partial.text
        assert 'hx-post' not in partial.text
