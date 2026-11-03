import pytest
from fastapi.testclient import TestClient

from whistapp import vision_import
from whistapp.main import app

LLM_ENV = ("WHIST_LLM_BASE_URL", "WHIST_LLM_MODEL", "WHIST_LLM_API_KEY", "WHIST_LLM_TIMEOUT",
           "WHIST_LLM_MAX_IMAGES", "WHIST_LLM_MAX_TOTAL_BYTES", "WHIST_LLM_MAX_TOKENS")


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(vision_import, "DATA", tmp_path)
    for name in LLM_ENV:
        monkeypatch.delenv(name, raising=False)
    with TestClient(app) as test_client:
        test_client.post("/login", data={"username": "admin", "password": "a$$word"})
        yield test_client


def test_admin_can_add_update_and_remove_llm_without_displaying_key(client, tmp_path):
    page = client.get("/admin/ai")
    assert page.status_code == 200
    assert "Gem AI-udbyder" in page.text
    assert 'type="password"' in page.text

    saved = client.post(
        "/admin/ai/llm",
        data={"base_url": "https://llm.example/v1/", "model": "vision-model", "api_key": "private-test-key"},
        follow_redirects=False,
    )
    assert saved.status_code == 303
    assert saved.headers["location"] == "/admin/ai?saved=llm"

    page = client.get(saved.headers["location"])
    assert "vision-model" in page.text
    assert "private-test-key" not in page.text
    assert vision_import.llm_config()["api_key"] == "private-test-key"

    updated = client.post(
        "/admin/ai/llm",
        data={"base_url": "https://llm.example/v1", "model": "vision-model-2", "api_key": ""},
        follow_redirects=False,
    )
    assert updated.status_code == 303
    assert vision_import.llm_config()["model"] == "vision-model-2"
    assert vision_import.llm_config()["api_key"] == "private-test-key"

    removed = client.post("/admin/ai/llm/remove", follow_redirects=False)
    assert removed.status_code == 303
    page = client.get(removed.headers["location"])
    assert "AI-udbyderen er fjernet" in page.text
    assert "private-test-key" not in page.text
    assert "private-test-key" not in (tmp_path / "llm.ini").read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="slået fra"):
        vision_import.llm_config()


def test_admin_rejects_invalid_llm_url_without_saving_key(client, tmp_path):
    response = client.post(
        "/admin/ai/llm", data={"base_url": "not-a-url", "model": "vision-model", "api_key": "must-not-leak"},
    )
    assert response.status_code == 200
    assert "Angiv en gyldig LLM-adresse" in response.text
    assert "must-not-leak" not in response.text
    assert not (tmp_path / "llm.ini").exists()


def test_admin_sets_request_limits_and_they_survive_removing_the_provider(client):
    assert vision_import.llm_limits() == {"timeout": 180, "max_images": 10,
                                          "max_total_bytes": 20_000_000, "max_tokens": 32_000}
    client.post("/admin/ai/llm", data={"base_url": "https://llm.example/v1", "model": "m", "api_key": ""})
    saved = client.post("/admin/ai/limits", data={"max_images": "4", "max_total_mb": "8",
                                                  "max_tokens": "16000", "timeout": "120"},
                        follow_redirects=False)
    assert saved.status_code == 303
    expected = {"timeout": 120, "max_images": 4, "max_total_bytes": 8_000_000, "max_tokens": 16_000}
    assert {key: vision_import.llm_config()[key] for key in expected} == expected
    assert 'name="max_tokens" type="number" min="1000" max="200000" step="1000" value="16000"' in client.get("/admin/ai").text

    rejected = client.post("/admin/ai/limits", data={"max_images": "4", "max_total_mb": "8",
                                                     "max_tokens": "16000", "timeout": "5000"})
    assert "Ventetid skal være mellem 10 og 900" in rejected.text
    assert vision_import.llm_limits()["timeout"] == 120

    client.post("/admin/ai/llm/remove")
    assert vision_import.llm_limits() == expected


def test_club_cannot_get_ai_import_before_a_provider_exists(client):
    response = client.post("/admin/ai/clubs/1", data={"allow_ai_import": "true", "ai_notes": ""})
    assert response.status_code == 400
    assert "Konfigurér først en AI-udbyder." in response.text
