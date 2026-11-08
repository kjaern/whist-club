"""Pages load their scripts and styles from the app itself, never from a third party."""
import re
from pathlib import Path

from fastapi.testclient import TestClient

import whistapp
from whistapp.main import app

EXTERNAL = re.compile(r'<(?:script|link)[^>]+(?:src|href)="(?:https?:)?//', re.I)


def test_no_template_loads_scripts_or_styles_from_another_host():
    templates = Path(whistapp.__file__).parent / "templates"
    offenders = [p.name for p in templates.rglob("*.html") if EXTERNAL.search(p.read_text(encoding="utf-8"))]
    assert offenders == []


def test_htmx_is_served_by_the_app():
    with TestClient(app) as client:
        client.post("/login", data={"username": "admin", "password": "a$$word"})
        page = client.get("/").text
        htmx = re.search(r'<script src="(/static/htmx\.min\.js\?v=\d+)"', page).group(1)
        served = client.get(htmx)
        assert served.status_code == 200 and served.text.startswith("var htmx=function()")
