from fastapi.testclient import TestClient
import pytest

from whistapp.buildinfo import build_stamp
from whistapp.cli import list_nights, stamp_night
from whistapp.database import connect
from whistapp.main import app


def _new_night(client, title):
    assert client.post("/login", data={"username": "admin", "password": "a$$word"},
                       follow_redirects=False).status_code == 303
    response = client.post("/sessions", data={"title": title, "rotation_mode": "consecutive",
                                               "guests": "Anne\nBo\nClara\nDan"}, follow_redirects=False)
    assert response.status_code in (302, 303)
    return int(response.headers["location"].rstrip("/").split("/")[-1])


def test_new_night_records_server_build_and_shows_it():
    with TestClient(app) as client:
        sid = _new_night(client, "Build stamp")
        with connect() as db:
            row = db.execute("SELECT server_version,server_commit FROM sessions WHERE id=?", (sid,)).fetchone()
        assert (row["server_version"], row["server_commit"]) == build_stamp()
        page = client.get(f"/sessions/{sid}")
        assert f"Oprettet med server v{build_stamp()[0]}" in page.text


def test_stamp_night_fills_only_unstamped_nights():
    with TestClient(app) as client:
        sid = _new_night(client, "Before stamping")
        with connect() as db:
            db.execute("UPDATE sessions SET server_version=NULL,server_commit=NULL WHERE id=?", (sid,))
        assert "Oprettet med server" not in client.get(f"/sessions/{sid}").text

        assert "Before stamping  [not stamped]" in list_nights()
        message = stamp_night(sid, "v1.0.0", "338dfb5d8894e8f9")
        assert "Before stamping  [v1.0.0, commit 338dfb5]" in list_nights()
        assert "v1.0.0, commit 338dfb5" in message
        with connect() as db:
            row = db.execute("SELECT server_version,server_commit FROM sessions WHERE id=?", (sid,)).fetchone()
        assert (row["server_version"], row["server_commit"]) == ("1.0.0", "338dfb5")
        assert "Oprettet med server v1.0.0 · 338dfb5" in client.get(f"/sessions/{sid}").text

        with pytest.raises(SystemExit):
            stamp_night(sid, "2.0.0", "abcdef0")
        stamp_night(sid, "2.0.0", "abcdef0", force=True)
        with connect() as db:
            assert db.execute("SELECT server_version FROM sessions WHERE id=?", (sid,)).fetchone()[0] == "2.0.0"

    with pytest.raises(SystemExit):
        stamp_night(999999, "1.0.0", "338dfb5")
