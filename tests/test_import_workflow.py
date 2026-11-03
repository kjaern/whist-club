import copy
import json
import re
import shutil
from html.parser import HTMLParser
from io import BytesIO
from zipfile import ZipFile

from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook

from whistapp.routes import imports as import_routes
from whistapp.database import connect
from whistapp.main import app
from whistapp.vision_import import build_workbooks


PLAYERS = ["Anne", "Bo", "Clara", "Dan"]


def valid_score_workbook():
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Hænder"
    headers = ["Hånd", "Melder", "Melding", "Meldt", "Stik", "Makker"]
    for name in PLAYERS:
        headers.extend([f"{name} Δ", f"{name} total"])
    headers.extend(["Melder 2", "Melder 3"])
    sheet.append(headers)
    rows = [
        (1, "Anne", {"Anne": "2?", "Bo": 2, "Clara": -2, "Dan": -2}),
        (2, "Clara", {"Anne": -2, "Bo": -2, "Clara": 2, "Dan": 2}),
        (3, "Anne", {"Anne": 2, "Bo": -2, "Clara": 2, "Dan": -2}),
    ]
    for number, bidder, scores in rows:
        row = [number, bidder, "almindelige", 8, "", ""]
        for player in PLAYERS:
            row.extend([scores[player], ""])
        row.extend(["", ""])
        sheet.append(row)
    sheet.cell(row=2, column=10, value=999)
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


class HiddenDataParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.data = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "input" and attributes.get("name") == "data":
            self.data = json.loads(attributes["value"])


def add_review_form_fields(payload):
    form = {
        "data": json.dumps(payload, ensure_ascii=False),
        "title": "Excel correction test",
        "game_date": "",
        "date_precision": "unknown",
        "replace_id": "",
        "placements": "",
        "fejl40": "40",
    }
    for index, player in enumerate(payload["players"]):
        form[f"player_name_{index}"] = player
        form[f"member_{index}"] = ""
    for index, hand in enumerate(payload["hands"]):
        form[f"bidder_{index}"] = hand["bidder"]
        form[f"bid_{index}"] = hand["bid"]
        form[f"target_{index}"] = str(hand["target"])
        form[f"actual_{index}"] = str(hand.get("actual", ""))
        form[f"partner_{index}"] = str(hand.get("partner", ""))
        for player_index, player in enumerate(payload["players"]):
            form[f"score_{index}_{player_index}"] = hand["scores"][player]
    return form


def test_excel_review_allows_reorder_delete_insert_and_persists_sources_and_log():
    original = valid_score_workbook()
    with TestClient(app) as client:
        client.post("/login", data={"username": "admin", "password": "a$$word"})
        response = client.post(
            "/import/preview",
            files={
                "workbook": (
                    "score.xlsx",
                    original,
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
        )
        assert response.status_code == 200
        assert "Hænder" in response.text
        assert 'name="player_name_0"' in response.text
        assert "move-hand-up" in response.text
        assert "delete-hand" in response.text
        assert 'name="actual_0"' in response.text
        assert 'name="partner_0"' in response.text
        assert 'data-state="ok"' in response.text
        assert "uncertain" in response.text
        assert "source-warning" in response.text
        assert "løbende total for Bo" in response.text
        parser = HiddenDataParser()
        parser.feed(response.text)
        data = parser.data
        assert data

        snapshot = data["source_snapshot"]
        initial = data["hands"]
        # Correct a name and value, reorder two hands, delete a duplicate, and insert a hand.
        name_map = {"Anne": "Anne K", "Bo": "Bo", "Clara": "Clara", "Dan": "Dan"}

        def rename_hand(hand):
            result = copy.deepcopy(hand)
            result["bidder"] = name_map.get(result["bidder"], result["bidder"])
            result["scores"] = {
                name_map[name]: value for name, value in result["scores"].items()
            }
            return result

        moved = rename_hand(initial[1])
        corrected = rename_hand(initial[0])
        corrected["scores"]["Anne K"] = "3"
        corrected["scores"]["Bo"] = "3"
        corrected["scores"]["Clara"] = "-3"
        corrected["scores"]["Dan"] = "-3"
        corrected["actual"] = "9"
        corrected["partner"] = "Bo"
        corrected["actual_explicit"] = True
        corrected["partner_explicit"] = True
        inserted = rename_hand(initial[1])
        inserted["source_id"] = "new-test-hand"
        inserted["source_sheet"] = ""
        inserted["source_row"] = 0
        data["players"] = [name_map[name] for name in data["players"]]
        data["hands"] = [moved, corrected, inserted]
        data["source_snapshot"] = snapshot

        form = add_review_form_fields(data)
        saved = client.post("/import/commit", data=form, follow_redirects=False)
        assert saved.status_code == 303, saved.text
        sid = int(saved.headers["location"].rsplit("/", 1)[1])

        with connect() as db:
            stored_players = [
                row["name"]
                for row in db.execute(
                    "SELECT name FROM players WHERE session_id=? ORDER BY seat_order",
                    (sid,),
                )
            ]
            stored_hands = db.execute(
                "SELECT actual FROM hands WHERE session_id=? ORDER BY id", (sid,)
            ).fetchall()
            source = db.execute(
                "SELECT id,original_name FROM session_source_files WHERE session_id=?",
                (sid,),
            ).fetchone()
            log_descriptions = [
                row[0]
                for row in db.execute(
                    "SELECT description FROM evening_log WHERE evening_id=?", (sid,)
                )
            ]
        assert stored_players[0] == "Anne K"
        assert [row["actual"] for row in stored_hands] == [8, 9, 8]
        assert source["original_name"] == "score.xlsx"
        assert any(
            "Renamed player Anne to Anne K" in entry for entry in log_descriptions
        )
        assert any("Inserted hand" in entry for entry in log_descriptions)
        assert any("Deleted hand" in entry for entry in log_descriptions)
        assert any("Moved hand" in entry for entry in log_descriptions)
        assert any("Corrected hand" in entry for entry in log_descriptions)
        assert any("Stik" in entry for entry in log_descriptions)

        source_response = client.get(f"/sessions/{sid}/source-files/{source['id']}")
        assert source_response.status_code == 200
        assert source_response.content == original
        saved_page = client.get(f"/sessions/{sid}")
        assert "Importmateriale og originalfiler" in saved_page.text
        assert "Hænder" in saved_page.text
        assert "Ændringslog" in saved_page.text

    with connect() as db:
        db.execute("DELETE FROM sessions WHERE id=?", (sid,))
    shutil.rmtree(import_routes.SOURCE_ROOT / str(sid), ignore_errors=True)


def test_arithmetic_error_is_shown_in_review_instead_of_aborting_preview():
    workbook = load_workbook(BytesIO(valid_score_workbook()))
    workbook.active.cell(row=2, column=9, value=1)
    output = BytesIO()
    workbook.save(output)

    with TestClient(app) as client:
        client.post("/login", data={"username": "admin", "password": "a$$word"})
        response = client.post(
            "/import/preview",
            files={
                "workbook": (
                    "unbalanced.xlsx",
                    output.getvalue(),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
        )
        assert response.status_code == 200
        assert "Hænder — 3" in response.text
        assert 'data-state="error"' in response.text
        assert 'class="import-status uncertain' not in response.text
        assert "Scoreændringer summerer ikke til nul" in response.text
        token_match = re.search(r"/import/source-staged/([0-9a-f]{32})/", response.text)
        assert token_match
        parser = HiddenDataParser()
        parser.feed(response.text)
        data = parser.data
        correction = client.post("/import/commit", data=add_review_form_fields(data))
        assert correction.status_code == 200
        assert "Scoreændringer summerer ikke til nul" in correction.text
        assert 'data-state="error"' in correction.text
        import_routes._remove_staged_source_files(token_match.group(1))


def test_invalid_contract_is_red_even_when_scores_sum_to_zero():
    workbook = load_workbook(BytesIO(valid_score_workbook()))
    workbook.active.cell(row=2, column=3, value="Unrecognized bid")
    output = BytesIO()
    workbook.save(output)
    with TestClient(app) as client:
        client.post("/login", data={"username": "admin", "password": "a$$word"})
        response = client.post(
            "/import/preview",
            files={
                "workbook": (
                    "invalid-bid.xlsx",
                    output.getvalue(),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
        )
        assert response.status_code == 200
        assert 'data-state="error"' in response.text
        assert "Ukendt melding" in response.text
        token_match = re.search(r"/import/source-staged/([0-9a-f]{32})/", response.text)
        assert token_match
        import_routes._remove_staged_source_files(token_match.group(1))


def test_raw_multisheet_transcription_uses_the_shared_review():
    workbook = Workbook()
    workbook.remove(workbook.active)
    first_headers = ["Hånd"]
    second_headers = ["Hånd"]
    for name in PLAYERS:
        first_headers.extend([name, None, None])
        second_headers.extend(["Melding", "Δ", "Total"])
    for sheet_name, hand_number in (("Ark 1", 1), ("Ark 2", 2)):
        sheet = workbook.create_sheet(sheet_name)
        sheet.append(first_headers)
        sheet.append(second_headers)
        values = [hand_number]
        for name in PLAYERS:
            notation = "Almindelige" if name == "Anne" else ""
            score = (
                "2?"
                if hand_number == 1 and name == "Anne"
                else ("2" if name == "Anne" else "-2" if name == "Bo" else "0")
            )
            values.extend([notation, score, ""])
        sheet.append(values)
    output = BytesIO()
    workbook.save(output)

    with TestClient(app) as client:
        client.post("/login", data={"username": "admin", "password": "a$$word"})
        response = client.post(
            "/import/preview",
            files={
                "workbook": (
                    "raw.xlsx",
                    output.getvalue(),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
        )
        assert response.status_code == 200
        assert "Hænder — 2" in response.text
        assert "Ark 1" in response.text
        assert "Ark 2" in response.text
        assert "Rå transskriptionsark" in response.text
        assert "Anne: Almindelige" in response.text
        assert "uncertain" in response.text
        token_match = re.search(r"/import/source-staged/([0-9a-f]{32})/", response.text)
        assert token_match
        import_routes._remove_staged_source_files(token_match.group(1))


def test_ai_zip_files_and_remarks_are_kept_after_import():
    payload = {
        "worksheets": [{"name": "Ark 1", "rows": [["Hånd", "Anne Δ"], ["1", "2?"]]}],
        "import": {
            "players": PLAYERS,
            "hands": [
                {
                    "number": 1,
                    "bidder": "Anne",
                    "bid": "almindelige",
                    "target": "8",
                    "actual": "",
                    "partner": "",
                    "extra": [],
                    "scores": {"Anne": "2", "Bo": "2", "Clara": "-2", "Dan": "-2"},
                }
            ],
            "date": "",
            "date_precision": "unknown",
            "remarks_markdown": "Kort LLM-remarks: læs hånd 1 igen.",
        },
    }
    photos = [("sheet.jpg", b"\xff\xd8\xff\xe0original-photo")]
    artifacts = build_workbooks(payload, photos)
    data = artifacts["data"]
    data["ai_token"] = import_routes._stage_ai_artifacts(
        artifacts["workbook"],
        artifacts["transcription"],
        artifacts["remarks"],
        artifacts["archive"],
    )
    data["pending_photos"] = import_routes._stage_photos(photos)
    data["source_snapshot"] = import_routes._import_snapshot(data)
    data["ai_job_id"] = "a" * 32
    import_routes.AI_JOBS[data["ai_job_id"]] = {"status": "complete", "data": data}

    with TestClient(app) as client:
        client.post("/login", data={"username": "admin", "password": "a$$word"})
        response = client.post(
            "/import/commit",
            data=add_review_form_fields(data),
            follow_redirects=False,
        )
        assert response.status_code == 303, response.text
        sid = int(response.headers["location"].rsplit("/", 1)[1])

        with connect() as db:
            sources = [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM session_source_files WHERE session_id=?", (sid,)
                )
            ]
            photo = db.execute(
                "SELECT id,original_name FROM session_photos WHERE session_id=?", (sid,)
            ).fetchone()
        names = {source["original_name"] for source in sources}
        assert {
            "transcription.xlsx",
            "import.xlsx",
            "remarks.md",
            "source.zip",
        }.issubset(names)
        assert photo["original_name"] == "sheet.jpg"
        source_by_name = {source["original_name"]: source for source in sources}
        remarks = client.get(
            f"/sessions/{sid}/source-files/{source_by_name['remarks.md']['id']}"
        )
        assert remarks.status_code == 200
        assert "Kort LLM-remarks" in remarks.text
        archive = client.get(
            f"/sessions/{sid}/source-files/{source_by_name['source.zip']['id']}"
        )
        assert archive.status_code == 200
        with ZipFile(BytesIO(archive.content)) as zip_file:
            assert "remarks.md" in zip_file.namelist()
            assert "photos/01-sheet.jpg" in zip_file.namelist()
            assert zip_file.read("photos/01-sheet.jpg") == photos[0][1]
        assert (
            client.get(f"/sessions/{sid}/photos/{photo['id']}").content == photos[0][1]
        )
        session_page = client.get(f"/sessions/{sid}")
        assert "transcription.xlsx" in session_page.text
        assert "import.xlsx" in session_page.text
        assert "Kort LLM-remarks" in session_page.text
        assert "photos/01-sheet.jpg" in session_page.text

    assert not (import_routes.AI_ROOT / data["ai_token"]).exists()
    import_routes.AI_JOBS.pop(data["ai_job_id"], None)
    with connect() as db:
        db.execute("DELETE FROM sessions WHERE id=?", (sid,))
    shutil.rmtree(import_routes.SOURCE_ROOT / str(sid), ignore_errors=True)
    shutil.rmtree(import_routes.PHOTO_ROOT / str(sid), ignore_errors=True)


def test_import_does_not_store_a_members_own_name_as_alias():
    workbook = load_workbook(BytesIO(valid_score_workbook()))
    workbook.active.cell(row=2, column=7, value=2)
    workbook.active.cell(row=2, column=10, value=None)
    output = BytesIO()
    workbook.save(output)
    with connect() as db:
        own_id = db.execute("INSERT INTO club_members(name) VALUES ('Ida Egen')").lastrowid
        other_id = db.execute("INSERT INTO club_members(name) VALUES ('Pia Medlem')").lastrowid
    sid = None
    try:
        with TestClient(app) as client:
            client.post("/login", data={"username": "admin", "password": "a$$word"})
            response = client.post("/import/preview", files={"workbook": (
                "aliases.xlsx", output.getvalue(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
            parser = HiddenDataParser()
            parser.feed(response.text)
            data = parser.data
            names = {"Anne": "Ida Egen", "Bo": "Pia Import", "Clara": "Clara", "Dan": "Dan"}
            data["players"] = [names[name] for name in data["players"]]
            for hand in data["hands"]:
                hand["bidder"] = names.get(hand["bidder"], hand["bidder"])
                hand["scores"] = {names[name]: value for name, value in hand["scores"].items()}
            form = add_review_form_fields(data)
            form[f"member_{data['players'].index('Ida Egen')}"] = str(own_id)
            form[f"member_{data['players'].index('Pia Import')}"] = str(other_id)
            saved = client.post("/import/commit", data=form, follow_redirects=False)
            assert saved.status_code == 303, saved.text
            sid = int(saved.headers["location"].rsplit("/", 1)[1])
        with connect() as db:
            aliases = {tuple(r) for r in db.execute(
                "SELECT member_id,alias FROM club_member_aliases WHERE member_id IN (?,?)", (own_id, other_id))}
        # A different spelling is remembered; the member's own name is not duplicated as an alias.
        assert aliases == {(other_id, "Pia Import")}
    finally:
        with connect() as db:
            if sid:
                db.execute("DELETE FROM sessions WHERE id=?", (sid,))
            db.execute("DELETE FROM club_member_aliases WHERE member_id IN (?,?)", (own_id, other_id))
            db.execute("DELETE FROM club_members WHERE id IN (?,?)", (own_id, other_id))
