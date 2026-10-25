import asyncio
from unittest.mock import AsyncMock

import httpx

from fastapi.testclient import TestClient

from app import main

PNG = b"\x89PNG\r\n\x1a\n" + b"test-image"


def post_verify(client, *, photo=PNG, content_type="image/png"):
    return client.post(
        "/api/verify",
        data={"origin": "Ottapalam", "destination": "Thrissur"},
        files={"photo": ("board.png", photo, content_type)},
    )


def test_health_routes_places_and_bus_data_contract():
    with TestClient(main.app) as client:
        assert client.get("/api/health").json()["status"] == "ok"
        routes = client.get("/api/routes").json()
        assert routes["route_count"] == 1
        assert routes["data_status"] == "verified_routes"
        assert "source-backed" in routes["notice"]
        kanyakumari = next(p for p in client.get("/api/places?q=KANYAKUMARI").json()["places"] if p["name"] == "Kanyakumari")
        assert kanyakumari["id"] == "place:kanyakumari"
        buses = client.get("/api/buses", params={"origin": "Palakkad", "destination": "Kanyakumari"}).json()
        assert buses["count"] == 1
        assert buses["options"][0]["route_id"] == "ksrtc-palakkad-kanyakumari-minnal-deluxe"
        assert buses["options"][0]["stops"][1:3] == ["Thrissur", "Angamaly"]


def test_verify_keeps_original_text_and_never_verifies_demo_route(monkeypatch):
    monkeypatch.setattr(main, "read_bus_board", AsyncMock(return_value={
        "detected_text": "THRISSUR", "original_transcription": "THRISSUR",
        "route_english": "Thrissur", "destination_english": "Thrissur", "confidence": None,
        "recognition_uncertain": False, "notes": "", "provider": "gemini", "demo_mode": False,
    }))
    with TestClient(main.app) as client:
        response = post_verify(client)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "unable_to_verify"
    assert body["verification_status"] == "UNABLE_TO_VERIFY"
    assert body["board_text_original"] == "THRISSUR"
    assert body["board_text_normalized"] == "thrissur"
    assert body["selected_origin"] == "Ottapalam"
    assert body["selected_destination"] == "Thrissur"
    assert body["data_verified"] is False


def test_verify_rejects_empty_or_invalid_image():
    with TestClient(main.app) as client:
        assert post_verify(client, photo=b"").status_code == 400
        assert post_verify(client, photo=b"not an image").status_code == 400
        assert post_verify(client, content_type="text/plain").status_code == 415


def test_verify_exposes_safe_gemini_error_and_cors(monkeypatch):
    async def quota(*args, **kwargs):
        raise main.VisionError("Gemini quota or rate limit is exhausted. Wait and retry, or check your Gemini API quota.", 429)

    monkeypatch.setattr(main, "read_bus_board", quota)
    with TestClient(main.app) as client:
        response = post_verify(client)
        preflight = client.options("/api/verify", headers={
            "Origin": "http://localhost:5174",
            "Access-Control-Request-Method": "POST",
        })
    assert response.status_code == 429
    assert "quota" in response.json()["detail"].lower()
    assert preflight.headers["access-control-allow-origin"] == "http://localhost:5174"


def test_demo_mode_is_explicit_and_still_inconclusive(monkeypatch):
    monkeypatch.setenv("BUSBOARD_DEMO_MODE", "true")
    with TestClient(main.app) as client:
        response = post_verify(client)
    assert response.status_code == 200
    assert response.json()["scan"]["demo_mode"] is True
    assert response.json()["status"] == "unable_to_verify"


def test_manual_review_rechecks_without_gemini_and_preserves_corrected_text():
    with TestClient(main.app) as client:
        response = client.post("/api/verify-manual", data={
            "origin": "Ottapalam", "destination": "Thrissur",
            "confirmed_destination": "thrissur", "original_detected_text": "THRISSURR",
        })
    assert response.status_code == 200
    assert response.json()["status"] == "unable_to_verify"
    assert response.json()["board_text_original"] == "thrissur"
    assert response.json()["scan"]["user_confirmed"] is True
    assert response.json()["scan"]["original_transcription"] == "THRISSURR"


def test_speech_returns_sarvam_audio_for_malayalam_and_english(monkeypatch):
    request = AsyncMock(side_effect=[b"ID3malayalam", b"ID3english"])
    monkeypatch.setattr(main, "request_sarvam_speech", request)
    with TestClient(main.app) as client:
        malayalam = client.post("/api/speech", json={"text": "ഈ ബസ് പൊരുത്തപ്പെടുന്നു", "language": "ml-IN"})
        english = client.post("/api/speech", json={"text": "This bus matches.", "language": "en-IN"})
    assert malayalam.status_code == 200 and malayalam.headers["content-type"] == "audio/mpeg"
    assert english.status_code == 200
    assert request.await_args_list[0].args[1] == "ml-IN"
    assert request.await_args_list[1].args[1] == "en-IN"


def test_speech_rejects_invalid_text_and_language():
    with TestClient(main.app) as client:
        assert client.post("/api/speech", json={"text": "", "language": "en-IN"}).status_code == 422
        assert client.post("/api/speech", json={"text": "x" * 1001, "language": "en-IN"}).status_code == 422
        assert client.post("/api/speech", json={"text": "hello", "language": "fr"}).status_code == 422


def test_speech_handles_missing_key_timeout_and_upstream_errors(monkeypatch):
    async def missing_key(*args): raise RuntimeError("Sarvam API key is not configured.")
    monkeypatch.setattr(main, "request_sarvam_speech", missing_key)
    with TestClient(main.app) as client:
        assert client.post("/api/speech", json={"text": "hello", "language": "en-IN"}).status_code == 503

    async def timeout(*args): raise TimeoutError("Sarvam speech request timed out.")
    monkeypatch.setattr(main, "request_sarvam_speech", timeout)
    with TestClient(main.app) as client:
        assert client.post("/api/speech", json={"text": "hello", "language": "en-IN"}).status_code == 504


def test_speech_maps_sarvam_auth_rate_limit_and_service_errors(monkeypatch):
    async def fail(_text, _language):
        response = httpx.Response(STATUS, request=httpx.Request("POST", "https://api.sarvam.ai/text-to-speech/stream"))
        raise httpx.HTTPStatusError("upstream", request=response.request, response=response)

    monkeypatch.setattr(main, "request_sarvam_speech", fail)
    with TestClient(main.app) as client:
        for status, expected in [(401, 401), (403, 403), (429, 429), (500, 503)]:
            globals()["STATUS"] = status
            response = client.post("/api/speech", json={"text": "hello", "language": "en-IN"})
            assert response.status_code == expected
