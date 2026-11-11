import os
import sqlite3
import tempfile
import time
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from whistapp import backups
from whistapp.auth import hash_password
from whistapp.database import connect, init_db
from whistapp.settings import HOME
from whistapp.web import DISPLAY_TZ
from whistapp.main import app


@pytest.fixture
def folder(monkeypatch, tmp_path):
    init_db()
    monkeypatch.setenv("WHIST_BACKUP_DIR", str(tmp_path / "backups"))
    return tmp_path / "backups"


@pytest.fixture
def client(folder):
    with TestClient(app) as test_client:
        test_client.post("/login", data={"username": "admin", "password": "a$$word"})
        yield test_client


def _club_names(path):
    db = sqlite3.connect(path)
    try:
        return [row[0] for row in db.execute("SELECT name FROM clubs ORDER BY id")]
    finally:
        db.close()


def test_manual_backup_is_a_working_copy_in_the_backup_folder(folder):
    path = backups.create_backup(datetime(2026, 11, 11, 14, 30, 5))
    assert path == folder / "whist-manual-2026-11-11-143005.sqlite3"
    assert _club_names(path)
    assert not list(folder.glob(".*partial"))
    [row] = backups.list_backups()
    assert row["name"] == path.name and row["kind"] == "Manuel" and row["size"] > 0


def test_monthly_backup_is_made_once_per_month_and_never_replaced(folder):
    first = backups.ensure_monthly(datetime(2026, 11, 1, 3, 0))
    assert first == folder / "whist-monthly-2026-11.sqlite3"
    first.write_bytes(b"kept")
    assert backups.ensure_monthly(datetime(2026, 11, 30, 23, 0)) is None
    assert first.read_bytes() == b"kept"
    assert backups.ensure_monthly(datetime(2026, 12, 1, 0, 5)) == folder / "whist-monthly-2026-12.sqlite3"


def test_server_startup_makes_the_months_backup(client, folder):
    # The lifespan task runs its first check right away; give it a moment in the event loop thread.
    for _ in range(100):
        if any(row["kind"] == "Månedlig" for row in backups.list_backups()): break
        client.get("/admin/backups")
    assert (folder / f"whist-monthly-{datetime.now(DISPLAY_TZ):%Y-%m}.sqlite3").exists()


def test_list_ignores_other_files_and_rejects_paths_outside(folder):
    folder.mkdir()
    (folder / "holiday.jpg").write_bytes(b"x")
    (folder / "whist-manual-nonsense.sqlite3").write_bytes(b"x")
    assert backups.list_backups() == []
    assert backups.backup_file("../whist.sqlite3") is None
    assert backups.backup_file("holiday.jpg") is None


def test_admin_page_makes_lists_and_downloads_backups(client, folder):
    page = client.get("/admin/backups")
    assert page.status_code == 200 and "Lav backup nu" in page.text and str(folder) in page.text

    made = client.post("/admin/backups", follow_redirects=False)
    assert made.status_code == 303
    name = made.headers["location"].split("saved=", 1)[1]
    assert (folder / name).exists()

    page = client.get(made.headers["location"])
    assert f"Backup gemt: {name}" in page.text and f'href="/admin/backups/{name}"' in page.text

    download = client.get(f"/admin/backups/{name}")
    assert download.status_code == 200
    assert download.content == (folder / name).read_bytes()
    assert name in download.headers["content-disposition"]
    assert client.get("/admin/backups/whist-manual-2000-01-01-000000.sqlite3").status_code == 404


def test_download_live_database_is_a_fresh_copy_not_kept(client, folder, monkeypatch, tmp_path):
    temp = tmp_path / "tmp"; temp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp))
    stale = temp / "whist-download-old.sqlite3"
    stale.write_bytes(b"x")
    os.utime(stale, (time.time() - 2 * backups.STALE_DOWNLOAD_SECONDS,) * 2)
    recent = temp / "whist-download-busy.sqlite3"
    recent.write_bytes(b"x")

    download = client.get("/admin/backups/download")
    assert download.status_code == 200
    assert download.content.startswith(b"SQLite format 3\x00")
    assert "whist-" in download.headers["content-disposition"]
    # The copy is removed after sending, a download abandoned long ago too, one still in progress is kept.
    assert sorted(p.name for p in temp.iterdir()) == ["whist-download-busy.sqlite3"]
    assert not list(folder.glob("whist-manual-*"))


def test_failed_live_download_shows_error_page(client, folder, monkeypatch, tmp_path):
    temp = tmp_path / "tmp"; temp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp))
    def broken(_target): raise sqlite3.OperationalError("disk I/O error")
    monkeypatch.setattr(backups, "_copy_database", broken)
    response = client.get("/admin/backups/download")
    assert response.status_code == 500
    assert "Kunne ikke lave en midlertidig kopi" in response.text and "disk I/O error" in response.text
    assert list(temp.iterdir()) == []


def test_unwritable_folder_shows_error_instead_of_crashing(client, folder, monkeypatch, tmp_path):
    blocker = tmp_path / "not-a-folder"
    blocker.write_bytes(b"x")
    monkeypatch.setenv("WHIST_BACKUP_DIR", str(blocker / "sub"))
    response = client.post("/admin/backups")
    assert response.status_code == 500 and "Kunne ikke skrive backup" in response.text


def test_backups_are_for_the_server_admin_only(folder):
    with connect() as db:
        member_id = db.execute("INSERT INTO club_members(name,status,is_club_admin,club_id) VALUES ('Backupadmin','active',1,1)").lastrowid
        user_id = db.execute("INSERT INTO user_accounts(username,password_hash,must_change_password) VALUES ('backupclubadmin',?,0)",
                             (hash_password("club-pass-1"),)).lastrowid
        db.execute("INSERT INTO member_accounts(member_id,user_id) VALUES (?,?)", (member_id, user_id))
    try:
        with TestClient(app) as club_admin:
            assert club_admin.post("/login", data={"username": "backupclubadmin", "password": "club-pass-1"},
                                   follow_redirects=False).status_code == 303
            assert club_admin.get("/club").status_code == 200  # really a club administrator
            for url in ("/admin/backups", "/admin/backups/download", "/admin/backups/whist-monthly-2026-11.sqlite3"):
                assert club_admin.get(url).status_code == 403
            assert club_admin.post("/admin/backups").status_code == 403
    finally:
        # Other tests save club 1's member list and expect only their own members.
        with connect() as db:
            db.execute("DELETE FROM user_accounts WHERE id=?", (user_id,))
            db.execute("DELETE FROM club_members WHERE id=?", (member_id,))
    with TestClient(app) as anonymous:
        assert anonymous.get("/admin/backups/download", follow_redirects=False).status_code == 303
        assert anonymous.post("/admin/backups", follow_redirects=False).status_code in (303, 403)
    assert not list(folder.glob("whist-manual-*"))


def test_post_needs_csrf_token(client, folder):
    response = client.post("/admin/backups", headers={"X-Test-No-Csrf": "1"})
    assert response.status_code == 403
    assert not list(folder.glob("whist-manual-*"))


def test_backups_in_the_same_second_get_their_own_files(folder):
    moment = datetime(2026, 11, 11, 14, 30, 5)
    first = backups.create_backup(moment)
    first.write_bytes(b"kept")
    second = backups.create_backup(moment)
    assert second == folder / "whist-manual-2026-11-11-143005-2.sqlite3"
    assert first.read_bytes() == b"kept"
    assert {row["name"] for row in backups.list_backups()} == {first.name, second.name}


def test_partial_file_left_by_a_stopped_server_is_removed(folder):
    folder.mkdir()
    leftover = folder / f"{backups.PARTIAL_PREFIX}whist-monthly-2026-10.sqlite3"
    leftover.write_bytes(b"half")
    backups.create_backup(datetime(2026, 11, 11, 9, 0, 0))
    assert not leftover.exists()


def test_relative_backup_folder_is_inside_the_whist_home(monkeypatch):
    monkeypatch.setenv("WHIST_BACKUP_DIR", "ekstern")
    assert backups.backup_dir() == HOME / "ekstern"
