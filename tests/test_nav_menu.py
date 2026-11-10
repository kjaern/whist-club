"""The top banner's hamburger menu holds every page link, including those once on the front page."""
import re

from fastapi.testclient import TestClient

from whistapp.database import connect
from whistapp.main import app
from test_clubs import _activate, _new_login


def _menu(page):
    return re.search(r'<details class="nav-menu".*?</details>', page, re.S).group(0)


def test_menu_holds_the_front_page_links_and_respects_roles():
    with TestClient(app) as admin:
        admin.post("/login", data={"username": "admin", "password": "a$$word"})
        menu = _menu(admin.get("/").text)
        for href in ("/statistics", "/kasse", "/import", "/rules", "/club", "/admin/accounts", "/account", "/guide"):
            assert f'href="{href}"' in menu
        assert 'id="show-bid-rules"' in menu and 'action="/logout"' in menu
        # The links moved off the front page's content.
        assert 'href="/rules"' not in admin.get("/").text.split("<main>", 1)[1]
        # The Klubkasse line sits in the banner of every page once a club is active.
        for url in ("/", "/statistics", "/account", "/rules", "/guide"):
            banner = admin.get(url).text.split("<main>", 1)[0].split('<div class="header-info">', 1)[1]
            assert 'class="kasse-home"' in banner and "Kassebeholdning" in banner, url

        member_id, temporary = _new_login(admin, "menumedlem", "Menumedlem")
        try:
            with TestClient(app) as member:
                _activate(member, "menumedlem", temporary)
                menu = _menu(member.get("/").text)
                assert 'href="/import"' in menu and 'href="/rules"' in menu
                assert 'href="/club"' not in menu and 'href="/admin/accounts"' not in menu
        finally:
            with connect() as db:
                db.execute("UPDATE club_members SET status='paused' WHERE id=?", (member_id,))
