"""Renaming a club member shows the current name on earlier nights and statistics."""
from fastapi.testclient import TestClient

from whistapp.database import connect, init_db
from whistapp.main import app


def test_renamed_member_shows_current_name_on_previous_nights_and_statistics():
    init_db()
    with connect() as db:
        saved_settings = dict(db.execute("SELECT * FROM club_settings WHERE id=1").fetchone())
        members = [db.execute("INSERT INTO club_members(name,status,club_id) VALUES (?,'active',1)",
                              (name,)).lastrowid
                   for name in ("Gammelnavn", "Omdøb B", "Omdøb C", "Omdøb D")]
        sid = db.execute("""INSERT INTO sessions(title,club_id,is_club,ended_at,created_at)
            VALUES ('Omdøbt aften',1,1,'2025-03-01 22:00:00','2025-03-01 19:00:00')""").lastrowid
        pids = [db.execute("INSERT INTO players(session_id,name,member_id,seat_order) VALUES (?,?,?,?)",
                           (sid, name, mid, i)).lastrowid
                for i, (name, mid) in enumerate(zip(("Gammelnavn", "Omdøb B", "Omdøb C", "Omdøb D"), members))]
        # An unlinked guest keeps its stored name.
        db.execute("INSERT INTO players(session_id,name,seat_order) VALUES (?,'Gæst Uden',4)", (sid,))
        hid = db.execute("""INSERT INTO hands(session_id,bid_key,bid_label,target,won,rules_snapshot,partner_id)
            VALUES (?,'almindelige','Almindelige',8,1,'{}',?)""", (sid, pids[1])).lastrowid
        db.execute("INSERT INTO hand_declarers(hand_id,player_id) VALUES (?,?)", (hid, pids[0]))
        for pid, score in zip(pids, (3, 3, -3, -3)):
            db.execute("INSERT INTO hand_participants(hand_id,player_id) VALUES (?,?)", (hid, pid))
            db.execute("INSERT INTO hand_scores(hand_id,player_id,numerator,denominator) VALUES (?,?,?,1)",
                       (hid, pid, score))
    try:
        with TestClient(app) as admin:
            admin.post("/login", data={"username": "admin", "password": "a$$word"})
            with connect() as db:
                form = {"placements": "0", "fejl40": str(saved_settings["fejl40"]),
                        "money_mode": "score_settle", "point_rate": "1"}
                for m in db.execute("SELECT id,name,status FROM club_members WHERE club_id=1"):
                    form[f"name_{m['id']}"] = "Nytnavn" if m["id"] == members[0] else m["name"]
                    form[f"status_{m['id']}"] = "active" if m["status"] == "active" else "paused"
            assert admin.post("/club/save", data=form, follow_redirects=False).status_code == 303

            with connect() as db:
                # The historical row is not rewritten; only the display follows the member.
                assert db.execute("SELECT name FROM players WHERE id=?", (pids[0],)).fetchone()[0] == "Gammelnavn"
                assert db.execute("SELECT name FROM club_members WHERE id=?", (members[0],)).fetchone()[0] == "Nytnavn"

            for url in (f"/sessions/{sid}", f"/sessions/{sid}/live", f"/sessions/{sid}/live/scoreboard",
                        f"/sessions/{sid}/editor/results", "/statistics", "/statistics/2025"):
                page = admin.get(url)
                assert page.status_code == 200, url
                assert "Nytnavn" in page.text, url
                assert "Gammelnavn" not in page.text, url

            assert "Gæst Uden" in admin.get(f"/sessions/{sid}").text
            # One statistics row per member, with the new name.
            stats = admin.get("/statistics").text
            assert stats.count("Nytnavn") == stats.count("Omdøb B")
    finally:
        with connect() as db:
            db.execute("DELETE FROM sessions WHERE id=?", (sid,))
            db.execute(f"DELETE FROM club_members WHERE id IN ({','.join('?' * len(members))})", members)
            cols = [k for k in saved_settings if k != "id"]
            db.execute(f"UPDATE club_settings SET {','.join(f'{c}=?' for c in cols)} WHERE id=1",
                       [saved_settings[c] for c in cols])
