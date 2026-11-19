"""An imported night is final REOPEN_HOURS after it was saved, like a live night after closing.

An import always makes a new night. Until it is final it can be deleted (and imported again to
correct it); after that it is locked, when the kasse posts it. A final imported night also keeps
its original sheets. There is no replacing an import and no changing its Klubkasse choice later.
"""
import pytest
from fastapi.testclient import TestClient

from tests.test_import_workflow import HiddenDataParser, add_review_form_fields, valid_score_workbook
from whistapp.database import connect
from whistapp.main import app

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@pytest.fixture
def admin():
    """The server admin in club 1; removes the nights and kasse alerts the test made."""
    made = []
    with TestClient(app) as client:
        client.post("/login", data={"username": "admin", "password": "a$$word"})
        client.made = made
        yield client
    with connect() as db:
        for sid in made:
            db.execute("DELETE FROM kasse_alerts WHERE session_id=?", (sid,))
            db.execute("DELETE FROM sessions WHERE id=?", (sid,))


def _preview(client):
    response = client.post("/import/preview", files={"workbook": ("score.xlsx", valid_score_workbook(), XLSX)})
    assert response.status_code == 200
    return response.text


def _new_import(client):
    parser = HiddenDataParser()
    parser.feed(_preview(client))
    data = parser.data
    data["hands"][0]["scores"]["Anne"] = "2"  # the workbook's uncertain "2?"
    saved = client.post("/import/commit", data=add_review_form_fields(data), follow_redirects=False)
    assert saved.status_code == 303, saved.text
    sid = int(saved.headers["location"].rsplit("/", 1)[1])
    client.made.append(sid)
    return sid


def _saved_hours_ago(sid, hours):
    with connect() as db:
        db.execute("UPDATE sessions SET saved_at=datetime('now',?) WHERE id=?", (f"-{hours} hours", sid))


def _exists(sid):
    with connect() as db:
        return db.execute("SELECT 1 FROM sessions WHERE id=?", (sid,)).fetchone() is not None


def _photo(sid):
    with connect() as db:
        return db.execute("INSERT INTO session_photos(session_id,stored_name,original_name) VALUES (?,?,?)",
                          (sid, "01.jpg", "ark.jpg")).lastrowid


def _has_photo(photo):
    with connect() as db:
        return db.execute("SELECT 1 FROM session_photos WHERE id=?", (photo,)).fetchone() is not None


def test_an_import_is_always_new_and_its_klubkasse_choice_is_not_edited_later(admin):
    assert 'name="replace_id"' not in _preview(admin) and "Ret en tidligere import" not in _preview(admin)
    first, second = _new_import(admin), _new_import(admin)
    assert first != second
    with connect() as db:
        assert db.execute("SELECT saved_at FROM sessions WHERE id=?", (first,)).fetchone()[0]
    page = admin.get(f"/sessions/{first}").text
    assert f"/sessions/{first}/money" not in page and "Historisk import</h2>" not in page
    assert admin.post(f"/sessions/{first}/money", data={"collect_club_money": "1"}).status_code in (404, 405)


def test_an_imported_night_can_be_deleted_for_twelve_hours_after_saving_then_is_final(admin):
    sid = _new_import(admin)
    _saved_hours_ago(sid, 11)
    page = admin.get(f"/sessions/{sid}").text
    assert "kan slette den fra forsiden indtil" in page and f"/sessions/{sid}/reopen" not in page
    assert f"/sessions/{sid}/delete" in admin.get("/").text

    _saved_hours_ago(sid, 13)
    assert "Endelig; kan ikke længere slettes." in admin.get(f"/sessions/{sid}").text
    assert f"/sessions/{sid}/delete" not in admin.get("/").text
    deleted = admin.post(f"/sessions/{sid}/delete", follow_redirects=False)
    assert deleted.status_code == 409 and "gemt for mere end 12 timer siden" in deleted.text
    assert _exists(sid)


def test_deleting_inside_the_window_removes_the_import(admin):
    sid = _new_import(admin)
    _saved_hours_ago(sid, 11)
    assert admin.post(f"/sessions/{sid}/delete", follow_redirects=False).status_code == 303
    assert not _exists(sid)


def test_an_import_saved_before_saved_at_existed_is_final(admin):
    """No time it was saved: it counts as long ago, so an old import is locked."""
    sid = _new_import(admin)
    with connect() as db:
        db.execute("UPDATE sessions SET saved_at=NULL WHERE id=?", (sid,))
    assert "Endelig; kan ikke længere slettes." in admin.get(f"/sessions/{sid}").text
    assert admin.post(f"/sessions/{sid}/delete", follow_redirects=False).status_code == 409
    assert _exists(sid)


def test_a_final_import_keeps_its_original_sheets_but_can_get_more(admin):
    sid = _new_import(admin)
    photo = _photo(sid)
    _saved_hours_ago(sid, 1)
    assert f"/sessions/{sid}/photos/{photo}/delete" in admin.get(f"/sessions/{sid}").text

    _saved_hours_ago(sid, 13)
    page = admin.get(f"/sessions/{sid}").text
    assert f"/sessions/{sid}/photos/{photo}/delete" not in page and f'action="/sessions/{sid}/photos"' in page
    assert admin.post(f"/sessions/{sid}/photos/{photo}/delete", follow_redirects=False).status_code == 409
    assert _has_photo(photo)


def test_a_final_live_night_can_still_have_photos_removed(admin):
    """The photo lock is for imports only: a live night's photos are an extra."""
    with connect() as db:
        sid = db.execute("""INSERT INTO sessions(title,is_club,club_id,ended_at,rule_version_id)
                            VALUES ('Live med foto',0,1,datetime('now','-13 hours'),
                                    (SELECT MAX(id) FROM club_rule_versions WHERE club_id=1))""").lastrowid
    admin.made.append(sid)
    photo = _photo(sid)
    page = admin.get(f"/sessions/{sid}").text
    assert "Endelig; kan ikke længere genåbnes." in page and f"/sessions/{sid}/photos/{photo}/delete" in page
    assert admin.post(f"/sessions/{sid}/photos/{photo}/delete", follow_redirects=False).status_code == 303
    assert not _has_photo(photo)
