from fastapi.testclient import TestClient

from whistapp.main import app
from test_clubs import _activate, _new_login


def test_ordinary_member_can_read_guide_from_header():
    with TestClient(app) as admin:
        admin.post("/login", data={"username": "admin", "password": "a$$word"})
        _, temporary = _new_login(admin, "guidereader", "Guidelæser")
        with TestClient(app) as member:
            _activate(member, "guidereader", temporary)
            assert 'href="/guide"' in member.get("/").text
            page = member.get("/guide")
            assert page.status_code == 200
            # The hidden features the guide exists to point out.
            assert "Opret ny klub" in page.text and "Sortér en tabel" in page.text
            assert 'href="/clubs"' in page.text


def test_guide_requires_login():
    with TestClient(app) as anonymous:
        response = anonymous.get("/guide", follow_redirects=False)
        assert response.status_code == 303 and response.headers["location"] == "/login"
