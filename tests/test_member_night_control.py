"""Ordinary club members run their club's nights, including closing them and reopening them shortly after."""

from fastapi.testclient import TestClient

from whistapp.database import connect
from whistapp.main import app
from test_clubs import _activate, _new_login, cleanup_clubs  # noqa: F401


def _night_with_hand(club_id):
    with connect() as db:
        sid = db.execute("INSERT INTO sessions(title,club_id) VALUES ('Medlemsaften',?)", (club_id,)).lastrowid
        db.execute("""INSERT INTO hands(session_id,bid_key,bid_label,target,rules_snapshot)
            VALUES (?,'almindelige','Almindelige',7,'{}')""", (sid,))
    return sid


def _ended(sid):
    with connect() as db:
        return db.execute("SELECT ended_at FROM sessions WHERE id=?", (sid,)).fetchone()[0] is not None


def test_ordinary_member_can_close_and_reopen_a_night():
    with TestClient(app) as admin:
        admin.post("/login", data={"username": "admin", "password": "a$$word"})
        member_id, temporary = _new_login(admin, "nightmember", "Aftenmedlem")
    sid = _night_with_hand(1)
    try:
        with TestClient(app) as member:
            _activate(member, "nightmember", temporary)
            page = member.get(f"/sessions/{sid}")
            assert page.status_code == 200 and f"/sessions/{sid}/end" in page.text
            closed = member.post(f"/sessions/{sid}/end", follow_redirects=False)
            assert closed.status_code == 303 and _ended(sid)
            assert f"/sessions/{sid}/reopen" in member.get(f"/sessions/{sid}").text
            reopened = member.post(f"/sessions/{sid}/reopen", follow_redirects=False)
            assert reopened.status_code == 303 and not _ended(sid)
            # Deleting a night stays with club administrators.
            assert member.post(f"/sessions/{sid}/delete", follow_redirects=False).status_code == 403
    finally:
        with connect() as db:
            db.execute("DELETE FROM sessions WHERE id=?", (sid,))
            db.execute("UPDATE club_members SET status='paused' WHERE id=?", (member_id,))


def test_outsiders_cannot_close_or_reopen_a_night(cleanup_clubs):  # noqa: F811
    with connect() as db:
        other_club = db.execute("INSERT INTO clubs(name) VALUES ('Andenklub')").lastrowid
    with TestClient(app) as admin:
        admin.post("/login", data={"username": "admin", "password": "a$$word"})
        _, outsider_temp = _new_login(admin, "othermember", "Andetmedlem", other_club)
        paused_id, paused_temp = _new_login(admin, "pausedmember", "Pauseret")
    with connect() as db:
        db.execute("UPDATE club_members SET status='paused' WHERE id=?", (paused_id,))
    sid = _night_with_hand(1)
    try:
        # A member of another club cannot reach the night at all.
        with TestClient(app) as outsider:
            _activate(outsider, "othermember", outsider_temp)
            assert outsider.post(f"/sessions/{sid}/end", follow_redirects=False).status_code in (403, 404)
            assert not _ended(sid)
            with connect() as db:
                db.execute("UPDATE sessions SET ended_at=CURRENT_TIMESTAMP WHERE id=?", (sid,))
            assert outsider.post(f"/sessions/{sid}/reopen", follow_redirects=False).status_code in (403, 404)
            assert _ended(sid)
        # A login whose membership is not active has no club and cannot touch the night either.
        with TestClient(app) as paused:
            _activate(paused, "pausedmember", paused_temp)
            assert paused.post(f"/sessions/{sid}/reopen", follow_redirects=False).status_code == 403
            assert _ended(sid)
    finally:
        with connect() as db:
            db.execute("DELETE FROM sessions WHERE id=?", (sid,))
