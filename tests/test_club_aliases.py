from fastapi.testclient import TestClient

from whistapp.database import connect
from whistapp.main import app
from whistapp.routes.imports import match_import_players


def test_admin_can_manage_member_aliases_and_imports_recognize_them():
    with TestClient(app) as client:
        client.post("/login", data={"username": "admin", "password": "a$$word"})
        with connect() as db:
            first_id = db.execute(
                "INSERT INTO club_members(name,status) VALUES (?, 'active')",
                ("Alias Test Member One",),
            ).lastrowid
            second_id = db.execute(
                "INSERT INTO club_members(name,status) VALUES (?, 'active')",
                ("Alias Test Member Two",),
            ).lastrowid
            members = [
                dict(row)
                for row in db.execute(
                    "SELECT id,name,status FROM club_members ORDER BY id"
                )
            ]
            settings = db.execute(
                "SELECT placements,fejl40 FROM club_settings WHERE id=1"
            ).fetchone()
            original_aliases = [
                tuple(row)
                for row in db.execute(
                    "SELECT member_id,alias FROM club_member_aliases ORDER BY member_id,alias"
                )
            ]

        def alias_form(values):
            active_count = sum(member["status"] == "active" for member in members)
            data = {
                "placements": ",".join("0" for _ in range(active_count)),
                "fejl40": str(settings["fejl40"]),
            }
            for member in members:
                member_id = member["id"]
                data[f"name_{member_id}"] = member["name"]
                data[f"status_{member_id}"] = (
                    "active" if member["status"] == "active" else "paused"
                )
                data[f"aliases_{member_id}"] = values.get(member_id, "")
            return data

        try:
            page = client.get("/club")
            assert f'name="aliases_{first_id}"' in page.text

            saved = client.post(
                "/club/save",
                data=alias_form(
                    {
                        first_id: "Score Alias A, Score Alias A2",
                        second_id: "Score Alias B",
                    }
                ),
                follow_redirects=False,
            )
            assert saved.status_code == 303

            with connect() as db:
                assert match_import_players(db, ["score alias a"]) == {
                    "score alias a": first_id
                }
                saved_aliases = {
                    tuple(row)
                    for row in db.execute(
                        "SELECT member_id,alias FROM club_member_aliases"
                    )
                }
            assert (first_id, "Score Alias A") in saved_aliases
            assert (first_id, "Score Alias A2") in saved_aliases
            assert (second_id, "Score Alias B") in saved_aliases

            conflict = client.post(
                "/club/save",
                data=alias_form(
                    {first_id: "Duplicate Alias", second_id: "duplicate alias"}
                ),
            )
            assert conflict.status_code == 200
            assert "skal være unikt mellem medlemmer" in conflict.text
            with connect() as db:
                after_conflict = {
                    tuple(row)
                    for row in db.execute(
                        "SELECT member_id,alias FROM club_member_aliases"
                    )
                }
            assert after_conflict == saved_aliases

            # Older imports stored members' own names as aliases; saving must not be blocked by them.
            with connect() as db:
                db.execute(
                    "INSERT INTO club_member_aliases(member_id,club_id,alias) VALUES (?,1,?)",
                    (second_id, "alias test member two"),
                )
            own_name = client.post(
                "/club/save",
                data=alias_form({first_id: "Score Alias A", second_id: "Alias Test Member Two, Score Alias B"}),
                follow_redirects=False,
            )
            assert own_name.status_code == 303, own_name.text
            with connect() as db:
                assert {tuple(r) for r in db.execute(
                    "SELECT member_id,alias FROM club_member_aliases WHERE member_id=?", (second_id,)
                )} == {(second_id, "Score Alias B")}

            # Another member's name is still refused.
            taken = client.post(
                "/club/save",
                data=alias_form({first_id: "Alias Test Member Two"}),
            )
            assert "er allerede et medlemsnavn" in taken.text

            removed = client.post(
                "/club/save",
                data=alias_form({}),
                follow_redirects=False,
            )
            assert removed.status_code == 303
            with connect() as db:
                assert (
                    db.execute("SELECT 1 FROM club_member_aliases LIMIT 1").fetchone()
                    is None
                )
        finally:
            with connect() as db:
                db.execute("DELETE FROM club_member_aliases")
                db.executemany(
                    "INSERT INTO club_member_aliases(member_id,club_id,alias) VALUES (?,1,?)",
                    original_aliases,
                )
                db.execute(
                    "DELETE FROM club_members WHERE id IN (?,?)", (first_id, second_id)
                )
