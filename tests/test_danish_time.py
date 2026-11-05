"""Stored timestamps stay UTC; pages show them in Danish time (CET/CEST)."""
from fastapi.testclient import TestClient

from whistapp.database import connect
from whistapp.main import app
from whistapp.web import local_time, night_time


def test_utc_shown_in_danish_winter_and_summer_time():
    assert local_time("2026-11-05 18:31:18") == "2026-11-05 19:31:18"  # CET, UTC+1
    assert local_time("2026-07-01 18:31:18") == "2026-07-01 20:31:18"  # CEST, UTC+2
    assert local_time("2026-12-31 23:30:00") == "2027-01-01 00:30:00"
    # Around the DST switch (last Sunday of March, 01:00 UTC).
    assert local_time("2026-03-29 00:59:59") == "2026-03-29 01:59:59"
    assert local_time("2026-03-29 01:00:00") == "2026-03-29 03:00:00"


def test_imported_and_odd_values_are_not_converted():
    assert local_time("2024-01-01 20:00:00", imported=True) == "2024-01-01 20:00:00"
    assert local_time(None) is None and local_time("") == ""
    assert local_time("Ukendt") == "Ukendt"
    imported = {"origin": "imported", "created_at": "2024-01-01 20:00:00", "ended_at": "2024-01-01 20:00:00"}
    assert night_time(imported) == night_time(imported, "ended_at") == "2024-01-01 20:00:00"
    # Reopened and closed again: ended_at is now a UTC CURRENT_TIMESTAMP.
    imported["ended_at"] = "2026-11-05 18:00:00"
    assert night_time(imported, "ended_at") == "2026-11-05 19:00:00"
    live = {"origin": "live", "created_at": "2026-07-01 18:00:00", "ended_at": "2026-07-01 22:15:00"}
    assert (night_time(live), night_time(live, "ended_at")) == ("2026-07-01 20:00:00", "2026-07-02 00:15:00")


def test_pages_show_danish_time_for_stored_utc():
    with TestClient(app) as client:
        assert client.post("/login", data={"username": "admin", "password": "a$$word"},
                           follow_redirects=False).status_code == 303
        response = client.post("/sessions", data={"title": "Tidszone", "rotation_mode": "consecutive",
                                                   "guests": "Anne\nBo\nClara\nDan"}, follow_redirects=False)
        sid = int(response.headers["location"].rstrip("/").split("/")[-1])
        with connect() as db:
            db.execute("UPDATE sessions SET created_at='2026-01-15 18:05:00', ended_at='2026-07-15 21:10:00' WHERE id=?", (sid,))
            db.execute("INSERT INTO evening_log(evening_id,user_id,description,created_at) VALUES(?,?,?,?)",
                       (sid, "admin", "tidstest", "2026-07-15 21:11:00"))
            stored = db.execute("SELECT created_at,ended_at FROM sessions WHERE id=?", (sid,)).fetchone()
        assert tuple(stored) == ("2026-01-15 18:05:00", "2026-07-15 21:10:00")  # storage untouched
        home = client.get("/").text
        assert "2026-01-15 19:05:00" in home and "2026-01-15 18:05:00" not in home
        page = client.get(f"/sessions/{sid}").text
        assert "Afsluttet 2026-07-15 23:10:00" in page
        assert "2026-07-15 23:11:00" in page
