import asyncio

from google.genai import errors

from app import vision


PNG = b"\x89PNG\r\n\x1a\n" + b"test-image"


def test_missing_api_key_is_actionable(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("BUSBOARD_DEMO_MODE", "false")
    try:
        asyncio.run(vision.read_bus_board(PNG, "image/png"))
    except vision.VisionError as error:
        assert "not configured" in str(error)
    else:
        raise AssertionError("expected VisionError")


def test_timeout_is_bounded_and_explained(monkeypatch):
    class SlowAio:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return None
        @property
        def models(self): return self
        async def generate_content(self, **kwargs):
            raise asyncio.TimeoutError()
    class Client:
        aio = SlowAio()

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("BUSBOARD_DEMO_MODE", "false")
    monkeypatch.setattr(vision.genai, "Client", lambda **kwargs: Client())
    try:
        asyncio.run(vision.read_bus_board(PNG, "image/png"))
    except vision.VisionError as error:
        assert error.status_code == 504
        assert "timed out" in str(error).lower()
    else:
        raise AssertionError("expected VisionError")


def test_quota_error_is_not_retried_or_hidden(monkeypatch):
    class FailingAio:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return None
        @property
        def models(self): return self
        async def generate_content(self, **kwargs):
            raise errors.ClientError(429, {"error": {"message": "quota"}}, None)
    class Client:
        aio = FailingAio()

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("BUSBOARD_DEMO_MODE", "false")
    monkeypatch.setattr(vision.genai, "Client", lambda **kwargs: Client())
    try:
        asyncio.run(vision.read_bus_board(PNG, "image/png"))
    except vision.VisionError as error:
        assert error.status_code == 429
        assert "quota" in str(error).lower()
    else:
        raise AssertionError("expected VisionError")


def test_invalid_model_output_is_safe(monkeypatch):
    class Response:
        text = "[]"
    class Aio:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return None
        @property
        def models(self): return self
        async def generate_content(self, **kwargs): return Response()
    class Client:
        aio = Aio()

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("BUSBOARD_DEMO_MODE", "false")
    monkeypatch.setattr(vision.genai, "Client", lambda **kwargs: Client())
    try:
        asyncio.run(vision.read_bus_board(PNG, "image/png"))
    except vision.VisionError as error:
        assert "unexpected response" in str(error)
    else:
        raise AssertionError("expected VisionError")
import json
from pathlib import Path

from app import vision


def test_normalise_result_preserves_original_script_and_does_not_invent_confidence():
    result = vision._normalise_result({
        "detected_text": "പാലക്കാട് - കന്യാകുമാരി",
        "destination_english": "Palakkad - Kanyakumari",
        "recognition_uncertain": False,
        "notes": "",
    })
    assert result["detected_text"] == "പാലക്കാട് - കന്യാകുമാരി"
    assert result["original_transcription"] == "പാലക്കാട് - കന്യാകുമാരി"
    assert result["destination_english"] == "Palakkad - Kanyakumari"
    assert result["confidence"] is None
    assert result["recognition_uncertain"] is False
