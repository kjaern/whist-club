import asyncio
import json
import re
from io import BytesIO
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from whistapp import vision_import
from whistapp.routes import imports as import_routes
from whistapp.database import connect, init_db
from whistapp.historical_import import read_workbook
from whistapp.main import app
from whistapp.vision_import import build_workbooks


def sample_payload():
    return {
        "worksheets": [
            {
                "name": "Ark 1",
                "rows": [
                    ["Hånd", "Anne", "Bo", "Clara", "Dan"],
                    ["1", "10", "-10", "0", "0"],
                    ["2", "=1+1", "?", "0", "0"],
                ],
            }
        ],
        "import": {
            "players": ["Anne", "Bo", "Clara", "Dan"],
            "hands": [
                {
                    "number": 1,
                    "bidder": "Anne",
                    "bid": "almindelige",
                    "target": "10",
                    "actual": "",
                    "partner": "",
                    "extra": [],
                    "scores": {"Anne": "10", "Bo": "-10", "Clara": "0", "Dan": "0"},
                },
                {
                    "number": 2,
                    "bidder": "Bo",
                    "bid": "sans",
                    "target": "9",
                    "actual": "",
                    "partner": "",
                    "extra": [],
                    "scores": {"Anne": "0", "Bo": "0", "Clara": "5", "Dan": "-5"},
                },
            ],
            "date": "2026-10-30",
            "date_precision": "day",
            "remarks_markdown": "Hånd 2: usikker aflæsning.",
        },
    }


def test_build_workbooks_creates_importer_workbook_and_raw_sheets():
    result = build_workbooks(sample_payload(), [("score sheet.jpg", b"original-photo")])

    data = read_workbook(result["workbook"])
    assert data["players"] == ["Anne", "Bo", "Clara", "Dan"]
    assert len(data["hands"]) == 2
    assert data["hands"][0]["scores"]["Anne"] == "10"

    workbook = load_workbook(BytesIO(result["workbook"]))
    assert workbook.sheetnames == ["Hænder"]
    rows = list(workbook.active.values)
    assert rows[0][:6] == ("Hånd", "Melder", "Melding", "Meldt", "Stik", "Makker")
    assert rows[0][6:8] == ("Anne Δ", "Anne total")
    assert rows[2][0] == 2

    raw = load_workbook(BytesIO(result["transcription"]))
    assert raw.sheetnames == ["Ark 1"]
    assert raw.active["A3"].value == "2"
    assert raw.active["B3"].value == "=1+1"
    assert raw.active["B3"].data_type == "s"
    assert raw.active["C3"].fill.fgColor.rgb == "00FFF2A8"
    assert result["date"] == "2026-10-30"
    assert result["remarks"] == "Hånd 2: usikker aflæsning."

    with ZipFile(BytesIO(result["archive"])) as archive:
        assert archive.namelist() == [
            "transcription.xlsx",
            "import.xlsx",
            "remarks.md",
            "photos/01-score sheet.jpg",
        ]
        assert archive.read("photos/01-score sheet.jpg") == b"original-photo"
        assert archive.read("remarks.md").decode() == result["remarks"]


@pytest.fixture
def ai_import_enabled(monkeypatch, tmp_path):
    """Configure an LLM and allow AI import for the club, as an admin would."""
    monkeypatch.setattr(vision_import, "DATA", tmp_path)
    monkeypatch.setenv("WHIST_LLM_BASE_URL", "https://llm.example/v1")
    monkeypatch.setenv("WHIST_LLM_MODEL", "vision-model")
    init_db()
    with connect() as db:
        db.execute("UPDATE club_settings SET allow_ai_import=1 WHERE id=1")
    yield
    with connect() as db:
        db.execute("UPDATE club_settings SET allow_ai_import=0 WHERE id=1")


def test_saved_transcription_json_opens_review():
    # Regression: the route's local import broke when routes moved into routes/.
    raw = json.dumps(sample_payload()).encode()
    with TestClient(app) as client:
        client.post("/login", data={"username": "admin", "password": "a$$word"})
        response = client.post("/import/json/preview",
                               files={"transcription_json": ("transcription.json", raw, "application/json")})
        token_match = re.search(r"/import/source-staged/([0-9a-f]{32})/", response.text)
        try:
            assert response.status_code == 200
            assert "Vælg en .json-fil" not in response.text and "Clara" in response.text
            assert token_match
        finally:
            if token_match:
                import_routes._remove_staged_source_files(token_match.group(1))


def test_ai_import_review_keeps_artifacts(monkeypatch, ai_import_enabled):
    async def fake_transcribe(photos, on_progress=None, **club_context):
        assert [name for name, _ in photos] == ["sheet.jpg"]
        if on_progress:
            on_progress("mock", "Mock-model produced a transcription.")
        return build_workbooks(sample_payload(), photos)

    monkeypatch.setattr(import_routes, "transcribe_images", fake_transcribe)

    with TestClient(app) as client:
        client.post("/login", data={"username": "admin", "password": "a$$word"})
        response = client.post(
            "/import/ai",
            files={"photos": ("sheet.jpg", b"\xff\xd8\xff\xe0test", "image/jpeg")},
        )
        assert response.status_code == 200
        assert "Følg trinene" in response.text
        job_match = re.search(r"/import/ai/jobs/([0-9a-f]{32})/events", response.text)
        assert job_match
        job_id = job_match.group(1)
        trace = client.get(f"/import/ai/jobs/{job_id}/events")
        assert trace.status_code == 200
        assert "Mock-model produced a transcription." in trace.text
        assert '"status": "complete"' in trace.text

        review = client.get(f"/import/ai/jobs/{job_id}/review")
        assert review.status_code == 200
        assert "AI-transskription" in review.text
        assert "Bemærkninger fra AI" in review.text
        assert "Originalark" in review.text
        assert "Raw AI-transskription" in review.text
        assert "Hænder-importark" in review.text
        assert "Filer i ZIP-arkivet" in review.text
        photo_match = re.search(
            r'href="(/import/staged/[0-9a-f]{32}/00\.jpg)"', review.text
        )
        assert photo_match
        photo = client.get(photo_match.group(1))
        assert photo.status_code == 200
        assert photo.content == b"\xff\xd8\xff\xe0test"

        match = re.search(r"/import/ai/staged/([0-9a-f]{32})/", review.text)
        assert match
        token = match.group(1)

        workbook = client.get(f"/import/ai/staged/{token}/transcription.xlsx")
        assert workbook.status_code == 200
        assert workbook.content.startswith(b"PK")

        remarks = client.get(f"/import/ai/staged/{token}/remarks.md")
        assert remarks.status_code == 200
        assert "usikker aflæsning" in remarks.text

        imported = client.get(f"/import/ai/staged/{token}/import.xlsx")
        assert imported.status_code == 200
        assert imported.content.startswith(b"PK")

        archive = client.get(f"/import/ai/staged/{token}/source.zip")
        assert archive.status_code == 200
        with ZipFile(BytesIO(archive.content)) as zip_file:
            assert "photos/01-sheet.jpg" in zip_file.namelist()
            assert zip_file.read("photos/01-sheet.jpg") == b"\xff\xd8\xff\xe0test"

        import_routes._remove_ai_artifacts(token)
        assert not (import_routes.AI_ROOT / token).exists()
        import_routes.AI_JOBS.pop(job_id, None)


def test_ai_review_allows_uncertain_scores_to_be_corrected(monkeypatch, ai_import_enabled):
    payload = sample_payload()
    payload["import"]["hands"][0]["scores"]["Anne"] = "?"

    async def fake_transcribe(photos, on_progress=None, **club_context):
        return build_workbooks(payload, photos)

    monkeypatch.setattr(import_routes, "transcribe_images", fake_transcribe)

    with TestClient(app) as client:
        client.post("/login", data={"username": "admin", "password": "a$$word"})
        response = client.post(
            "/import/ai",
            files={"photos": ("uncertain.jpg", b"\xff\xd8\xff\xe0test", "image/jpeg")},
        )
        job_match = re.search(r"/import/ai/jobs/([0-9a-f]{32})/events", response.text)
        assert job_match
        job_id = job_match.group(1)
        client.get(f"/import/ai/jobs/{job_id}/events")
        response = client.get(f"/import/ai/jobs/{job_id}/review")

    assert response.status_code == 200
    assert 'name="score_0_0" value="?"' in response.text
    assert 'data-state="error"' in response.text
    import_routes.AI_JOBS.pop(job_id, None)


def test_ai_request_failure_preserves_photos_for_retry(monkeypatch, ai_import_enabled):
    calls = 0

    async def fake_transcribe(photos, on_progress=None, **club_context):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ValueError("provider unavailable")
        return build_workbooks(sample_payload(), photos)

    monkeypatch.setattr(import_routes, "transcribe_images", fake_transcribe)

    with TestClient(app) as client:
        client.post("/login", data={"username": "admin", "password": "a$$word"})
        response = client.post(
            "/import/ai",
            files={"photos": ("sheet.jpg", b"\xff\xd8\xff\xe0test", "image/jpeg")},
        )
        assert response.status_code == 200
        job_match = re.search(r"/import/ai/jobs/([0-9a-f]{32})/events", response.text)
        assert job_match
        job_id = job_match.group(1)
        trace = client.get(f"/import/ai/jobs/{job_id}/events")
        assert "provider unavailable" in trace.text
        match = re.search(
            r'name="pending_photos" value="([0-9a-f]{32})"', response.text
        )
        assert match
        photo_token = match.group(1)
        assert f"/import/staged/{photo_token}/00.jpg" in response.text

        retry = client.post("/import/ai", data={"pending_photos": photo_token})
        assert retry.status_code == 200
        retry_job = re.search(r"/import/ai/jobs/([0-9a-f]{32})/events", retry.text)
        assert retry_job
        retry_id = retry_job.group(1)
        client.get(f"/import/ai/jobs/{retry_id}/events")
        assert (
            "AI-transskription" in client.get(f"/import/ai/jobs/{retry_id}/review").text
        )
        import_routes.AI_JOBS.pop(job_id, None)
        import_routes.AI_JOBS.pop(retry_id, None)

    assert calls == 2


def test_prompt_carries_club_values_and_notes():
    plain = vision_import._vision_prompt()
    assert '"value": "9"' in plain and "CLUB NOTES" not in plain

    prompt = vision_import._vision_prompt({"sol": {"options": {"value": "25/2"}}}, "  JF = Jens Fup  ")
    assert '"value": "12.5"' in prompt and '"base_price": "1"' in prompt
    assert prompt.rstrip().endswith("do not output Excel.")
    assert "CLUB NOTES" in prompt and "JF = Jens Fup\n" in prompt


def test_ai_job_uses_the_clubs_rules_and_notes(monkeypatch, ai_import_enabled):
    seen = {}

    async def fake_transcribe(photos, on_progress=None, **club_context):
        seen.update(club_context)
        return build_workbooks(sample_payload(), photos)

    monkeypatch.setattr(import_routes, "transcribe_images", fake_transcribe)
    with connect() as db:
        before = tuple(db.execute("SELECT bid_settings,ai_notes FROM club_settings WHERE id=1").fetchone())
        db.execute("""UPDATE club_settings SET bid_settings='{"sol": {"options": {"value": "12"}}}',
                      ai_notes='JF = Jens Fup' WHERE id=1""")
    try:
        with TestClient(app) as client:
            client.post("/login", data={"username": "admin", "password": "a$$word"})
            response = client.post("/import/ai", files={"photos": ("sheet.jpg", b"\xff\xd8\xff\xe0test", "image/jpeg")})
            job_id = re.search(r"/import/ai/jobs/([0-9a-f]{32})/events", response.text).group(1)
            client.get(f"/import/ai/jobs/{job_id}/events")
            import_routes.AI_JOBS.pop(job_id, None)
        assert seen == {"bid_settings": {"sol": {"options": {"value": "12"}}}, "club_notes": "JF = Jens Fup"}
    finally:
        with connect() as db:
            db.execute("UPDATE club_settings SET bid_settings=?,ai_notes=? WHERE id=1", before)


def test_transcribe_images_posts_openai_compatible_request(monkeypatch, tmp_path):
    captured = {}

    class FakeResponse:
        status_code = 200
        content = b"{}"

        def json(self):
            content = "```json\n" + json.dumps(sample_payload()) + "\n```"
            return {"choices": [{"message": {"content": content}}]}

    class FakeClient:
        def __init__(self, timeout):
            captured["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, headers, json):
            captured.update(url=url, headers=headers, json=json)
            return FakeResponse()

    monkeypatch.setenv("WHIST_LLM_BASE_URL", "https://llm.example/v1/")
    monkeypatch.setenv("WHIST_LLM_MODEL", "vision-test")
    monkeypatch.setenv("WHIST_LLM_API_KEY", "secret")
    for name in ("WHIST_LLM_TIMEOUT", "WHIST_LLM_MAX_TOKENS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(vision_import, "DATA", tmp_path)
    monkeypatch.setattr(vision_import.httpx, "AsyncClient", FakeClient)

    progress = []
    result = asyncio.run(
        vision_import.transcribe_images(
            [("sheet.jpg", b"\xff\xd8\xff\xe0test")],
            on_progress=lambda stage, message: progress.append((stage, message)),
        )
    )

    assert captured["url"] == "https://llm.example/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer secret"
    assert captured["json"]["model"] == "vision-test"
    assert captured["json"]["response_format"] == {"type": "json_object"}
    assert captured["json"]["max_tokens"] == 32_000 and captured["timeout"] == 180
    assert captured["json"]["messages"][1]["content"][1]["image_url"]["url"].startswith(
        "data:image/jpeg;base64,"
    )
    assert read_workbook(result["workbook"])["players"] == [
        "Anne",
        "Bo",
        "Clara",
        "Dan",
    ]
    assert [stage for stage, _ in progress] == [
        "configuration",
        "preparation",
        "request",
        "response",
        "model_output",
        "parsing",
        "workbooks",
    ]


def test_llm_config_requires_provider(monkeypatch):
    for key in (
        "WHIST_LLM_BASE_URL",
        "WHIST_LLM_MODEL",
        "WHIST_LLM_API_KEY",
        "WHIST_LLM_TIMEOUT",
        "WHIST_LLM_MAX_IMAGES",
        "WHIST_LLM_MAX_TOTAL_BYTES",
    ):
        monkeypatch.delenv(key, raising=False)

    with pytest.raises(ValueError, match="ikke konfigureret"):
        vision_import.llm_config()


def test_invalid_model_json_is_visible_before_failure(monkeypatch):
    class FakeResponse:
        status_code = 200
        content = b'{}'

        def json(self):
            return {"choices": [{"message": {"content": '{"import": invalid', "reasoning_content": "Checking photo"}}]}

    class FakeClient:
        def __init__(self, timeout):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, headers, json):
            return FakeResponse()

    monkeypatch.setenv("WHIST_LLM_BASE_URL", "https://llm.example/v1/")
    monkeypatch.setenv("WHIST_LLM_MODEL", "vision-test")
    monkeypatch.setattr(vision_import.httpx, "AsyncClient", FakeClient)
    progress = []
    with pytest.raises(ValueError, match="linje"):
        asyncio.run(vision_import.transcribe_images(
            [("sheet.jpg", b"test")],
            on_progress=lambda stage, message: progress.append((stage, message)),
        ))
    assert ("model_output", '{"import": invalid') in progress
    assert ("reasoning", "Checking photo") in progress
