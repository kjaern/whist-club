from contextlib import contextmanager
from pathlib import Path

from fastapi.testclient import TestClient

from conftest import NO_CSRF, session_csrf
from whistapp.main import app


@contextmanager
def _admin():
    with TestClient(app) as client:
        assert client.post("/login", data={"username": "admin", "password": "a$$word"}, headers={NO_CSRF: "1"},
                           follow_redirects=False).status_code == 303
        yield client


def test_form_post_with_bad_token_gets_a_readable_danish_403_not_json():
    with _admin() as client:
        for data in ({}, {"csrf_token": "forkert"}):
            response = client.post("/club/new-member", data=data, headers={NO_CSRF: "1"})
            assert response.status_code == 403 and response.headers["content-type"].startswith("text/plain")
            assert "Sikkerhedsnøglen" in response.text and not response.text.lstrip().startswith("{")


def test_htmx_request_with_bad_token_gets_the_danish_message():
    with _admin() as client:
        response = client.post("/club/new-member", headers={"HX-Request": "true", "X-CSRF-Token": "forkert"})
        assert response.status_code == 403 and response.headers["content-type"].startswith("text/plain")
        assert response.text.startswith("Sikkerhedsnøglen mangler eller er forældet")


def test_valid_token_still_works_and_other_errors_keep_their_format():
    with _admin() as client:
        token = session_csrf(client)
        assert client.post("/club/new-member", data={"csrf_token": token}, headers={NO_CSRF: "1"}).status_code == 200
        assert client.post("/club/new-member", headers={"HX-Request": "true", "X-CSRF-Token": token}).status_code == 200
        # Other HTTPExceptions are untouched by the CSRF handler.
        missing = client.get("/finds-ikke")
        assert missing.status_code == 404 and missing.json() == {"detail": "Not Found"}


def test_base_template_shows_refused_htmx_requests():
    base = (Path(__file__).parents[1] / "src/whistapp/templates/base.html").read_text(encoding="utf-8")
    assert "htmx:responseError" in base and "xhr.status === 403" in base and "alert(xhr.responseText)" in base
    assert "location.reload" not in base
