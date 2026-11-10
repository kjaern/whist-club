"""The Klubkasse line in the banner: on every page with an active club, worked out once per request,
nights posted only on reads, and never failing the page it sits on."""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import whistapp.web as web
from whistapp.club import active_club_reset, active_club_set
from whistapp.database import connect, init_db
from whistapp.kasse import ledger
from whistapp.kasse.base import UnknownEntryType
from whistapp.main import app
from test_clubs import _activate, _new_login


def _banner(page):
    return page.split("<main>", 1)[0].split('<div class="header-info">', 1)[1]


@pytest.fixture
def admin():
    with TestClient(app) as client:
        client.post("/login", data={"username": "admin", "password": "a$$word"})
        yield client


def test_a_member_sees_their_own_saldo_on_other_pages(admin):
    member_id, temporary = _new_login(admin, "bannermedlem", "Bannermedlem")
    try:
        with TestClient(app) as member:
            _activate(member, "bannermedlem", temporary)
            for url in ("/statistics", "/rules", "/guide"):
                banner = _banner(member.get(url).text)
                assert "Din saldo" in banner and "bogført" in banner and "afventer" in banner, url
    finally:
        with connect() as db:
            db.execute("UPDATE club_members SET status='paused' WHERE id=?", (member_id,))


@pytest.mark.parametrize("error", [lambda: UnknownEntryType(1, "forsvundet"), lambda: RuntimeError("uventet")])
def test_the_banner_never_fails_the_page(admin, monkeypatch, error):
    # A new exception each time: a raised exception kept by pytest (or a kept log record) holds its
    # traceback, and so the route's open connection, until the test home is removed.
    def broken(db, club_id):
        raise error()
    monkeypatch.setattr(ledger, "balances", broken)
    logged = []
    monkeypatch.setattr(web.logging.getLogger("whistapp.web"), "exception", lambda *args, **kwargs: logged.append(args))
    monkeypatch.setattr(web.logging.getLogger("whistapp.web"), "error", lambda *args, **kwargs: logged.append(args))
    response = admin.get("/statistics")
    assert response.status_code == 200
    assert "Klubkassen kan ikke vises lige nu" in _banner(response.text)
    assert logged  # the reason is logged for the server admin


def test_the_kasse_page_adds_up_the_books_once(admin, monkeypatch):
    calls, real = [], ledger.balances
    monkeypatch.setattr(ledger, "balances", lambda db, club_id: calls.append(club_id) or real(db, club_id))
    page = admin.get("/kasse").text
    assert "Kassebeholdning" in _banner(page)
    assert len(calls) == 1


@pytest.mark.parametrize("method,posts", [("GET", True), ("POST", False)])
def test_nights_are_posted_only_when_a_page_is_read(monkeypatch, method, posts):
    """A POST that renders a full page may still hold its own BEGIN IMMEDIATE (fx close_error), so the
    banner must not post from a second connection then. The result is kept for the rest of the request."""
    init_db()  # also when this test runs on its own, before any client has started the app
    finalized = []
    monkeypatch.setattr(web, "finalize_nights", finalized.append)
    monkeypatch.setattr(web, "linked_member", lambda request: None)
    request = SimpleNamespace(method=method, state=SimpleNamespace())
    token = active_club_set(1)
    try:
        first = web.kasse_banner(request)
        assert web.kasse_banner(request) is first and "cash" in first
    finally:
        active_club_reset(token)
    assert finalized == ([1] if posts else [])


def test_no_banner_without_an_active_club():
    token = active_club_set(None)  # a login session that has not picked a club
    try:
        assert web.kasse_banner(SimpleNamespace(method="GET", state=SimpleNamespace())) is None
    finally:
        active_club_reset(token)
