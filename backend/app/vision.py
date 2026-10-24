"""Gemini-backed bus-board recognition with bounded, safe failures."""
from __future__ import annotations

import asyncio
import json
import os
import socket
from typing import Any

import httpx
from google import genai
from google.genai import errors, types


BUS_BOARD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "detected_text": {"type": "string"},
        "destination_english": {"type": "string"},
        "recognition_uncertain": {"type": "boolean"},
        "notes": {"type": "string"},
    },
    "required": ["detected_text", "destination_english", "recognition_uncertain", "notes"],
}

PROMPT = """Transcribe the visible destination/route text from this bus board, preserving the original script and spelling in detected_text. If it shows two known places, retain both names and the separator. Give an English translation only when you can read it; do not invent a missing name, route, stop, confidence score, or service. Set recognition_uncertain true if key letters are unreadable or multiple readings are plausible. Keep uncertainty details in notes. Return the requested JSON only."""

class VisionError(RuntimeError):
    """A frontend-safe recognition failure with an appropriate HTTP status."""

    def __init__(self, message: str, status_code: int = 503) -> None:
        super().__init__(message)
        self.status_code = status_code


def _demo_enabled() -> bool:
    return os.getenv("BUSBOARD_DEMO_MODE", "").strip().casefold() in {"1", "true", "yes", "on"}


def _normalise_result(result: dict[str, Any]) -> dict[str, Any]:
    original = str(result.get("detected_text") or "").strip()
    english = str(result.get("destination_english") or "").strip()
    if not original and english:
        original = english
    return {
        "detected_text": original,
        "original_transcription": original,
        "destination_english": english or None,
        "confidence": None,
        "recognition_uncertain": result.get("recognition_uncertain") is True,
        "notes": str(result.get("notes", "")).strip(),
        "provider": "gemini",
        "demo_mode": False,
    }


def _api_error(error: errors.APIError) -> VisionError:
    if error.code == 429:
        return VisionError("Gemini quota or rate limit is exhausted. Wait and retry, or check your Gemini API quota.", 429)
    if error.code in {401, 403}:
        return VisionError("Gemini rejected the API key. Check GEMINI_API_KEY in backend/.env and restart the backend.", 503)
    if error.code == 404:
        return VisionError("The configured Gemini model is unavailable for this API key. Check GEMINI_MODEL in backend/.env.", 503)
    if 500 <= error.code <= 599:
        return VisionError("Gemini is temporarily unavailable. Please retry shortly.", 503)
    return VisionError("Gemini rejected the recognition request. Check the configured model and try another image.", 502)


async def read_bus_board(image_bytes: bytes, mime_type: str, *, demo_destination: str = "") -> dict[str, Any]:
    """Read a board with Gemini without blocking FastAPI's event loop.

    The SDK async client uses a 15-second HTTP deadline and one attempt.  The
    outer deadline is intentionally slightly longer to include request setup;
    this prevents the SDK's default retry budget from turning an upstream error
    into an opaque 50-second spinner.
    """
    if _demo_enabled():
        destination = os.getenv("BUSBOARD_DEMO_DESTINATION", "").strip() or demo_destination.strip()
        return {
            "detected_text": destination,
            "original_transcription": destination,
            "confidence": None,
            "recognition_uncertain": not bool(destination),
            "destination_english": destination or None,
            "notes": "Simulated recognition; Gemini was not called.",
            "provider": "demo",
            "demo_mode": True,
        }

    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key or api_key == "put_your_key_here":
        raise VisionError("Gemini API key not configured. Add GEMINI_API_KEY to backend/.env, then restart the backend.")

    # This low-latency multimodal model is the project default.  It can be
    # overridden explicitly through GEMINI_MODEL; the app never silently
    # switches models after an upstream error.
    model = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite").strip() or "gemini-3.5-flash-lite"
    http_options = types.HttpOptions(
        timeout=15_000,
        retry_options=types.HttpRetryOptions(attempts=1),
    )
    client = genai.Client(api_key=api_key, http_options=http_options)
    try:
        async with client.aio as aclient:
            response = await asyncio.wait_for(
                aclient.models.generate_content(
                    model=model,
                    contents=[PROMPT, types.Part.from_bytes(data=image_bytes, mime_type=mime_type)],
                    config=types.GenerateContentConfig(
                        temperature=0,
                        max_output_tokens=180,
                        response_mime_type="application/json",
                        response_json_schema=BUS_BOARD_SCHEMA,
                    ),
                ),
                timeout=20,
            )
    except (asyncio.TimeoutError, httpx.TimeoutException) as exc:
        raise VisionError("Gemini image recognition timed out after 20 seconds. Please retry; if it persists, check your network and Gemini service status.", 504) from exc
    except errors.APIError as exc:
        raise _api_error(exc) from exc
    except (httpx.NetworkError, socket.gaierror) as exc:
        raise VisionError("Could not reach Gemini. Check your internet connection, DNS, or proxy settings, then retry.") from exc
    except (ValueError, TypeError) as exc:
        raise VisionError("Gemini request could not be prepared. Check GEMINI_MODEL and the installed google-genai SDK.", 502) from exc
    except Exception as exc:
        raise VisionError("Gemini image recognition failed unexpectedly. Please retry or check the backend logs.", 502) from exc

    try:
        raw = response.text or ""
        parsed = json.loads(raw)
    except (AttributeError, json.JSONDecodeError) as exc:
        raise VisionError("Gemini returned an unreadable response. Please try a clearer bus-board photo.", 502) from exc
    if not isinstance(parsed, dict):
        raise VisionError("Gemini returned an unexpected response. Please try a clearer bus-board photo.", 502)
    return _normalise_result(parsed)
