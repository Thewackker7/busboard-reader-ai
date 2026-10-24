"""Gemini assistant for suggesting, never certifying, route stop sequences."""
from __future__ import annotations

import asyncio
import json
import os
import socket
from typing import Any

import httpx
from google import genai
from google.genai import errors, types

from .route_engine import normalize_place_name


ROUTE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "major_stops": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    },
    "required": ["major_stops", "summary", "confidence"],
}


class RouteAssistError(RuntimeError):
    def __init__(self, message: str, status_code: int = 503) -> None:
        super().__init__(message)
        self.status_code = status_code


async def suggest_major_stops(
    origin: str,
    destination: str,
    dataset_routes: list[list[str]],
) -> dict[str, Any]:
    """Ask Gemini for a likely major-stop corridor alongside supplied dataset evidence."""
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key or api_key == "put_your_key_here":
        raise RouteAssistError("Add GEMINI_API_KEY to backend/.env to use the Gemini route assistant.")

    model = (
        os.getenv("GEMINI_ROUTE_MODEL", "").strip()
        or os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite").strip()
        or "gemini-3.5-flash-lite"
    )
    prompt = f"""Suggest only the major towns or well-known bus stops likely to lie between these endpoints in India.
Origin: {origin}
Destination: {destination}

Published timetable routes found in the local dataset (may be empty or incomplete):
{json.dumps(dataset_routes[:8], ensure_ascii=False)}

Use geography and general route knowledge to give a plausible corridor, but do not invent bus operators, service times, or claim a bus stops at every named place. If the path is ambiguous or you cannot name reliable major stops, return an empty major_stops list and low confidence. The local timetable routes are evidence; do not replace or contradict them silently. Return JSON matching the schema."""

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
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        temperature=0,
                        max_output_tokens=360,
                        response_mime_type="application/json",
                        response_json_schema=ROUTE_SCHEMA,
                    ),
                ),
                timeout=18,
            )
    except (asyncio.TimeoutError, httpx.TimeoutException) as exc:
        raise RouteAssistError("Gemini route assistance timed out. The timetable results are still available.", 504) from exc
    except errors.APIError as exc:
        if exc.code == 429:
            message, status = "Gemini quota is exhausted. The timetable results are still available.", 429
        elif exc.code in {401, 403}:
            message, status = "Gemini rejected the API key. Check GEMINI_API_KEY in backend/.env.", 503
        elif exc.code == 404:
            message, status = "The configured Gemini model is unavailable. Check GEMINI_ROUTE_MODEL or GEMINI_MODEL.", 503
        else:
            message, status = "Gemini could not suggest stops right now. The timetable results are still available.", 503
        raise RouteAssistError(message, status) from exc
    except (httpx.NetworkError, socket.gaierror) as exc:
        raise RouteAssistError("Could not reach Gemini. Check the backend network connection.") from exc
    except Exception as exc:
        raise RouteAssistError("Gemini route assistance failed. The timetable results are still available.", 502) from exc

    try:
        parsed = json.loads(response.text or "")
    except (AttributeError, json.JSONDecodeError) as exc:
        raise RouteAssistError("Gemini returned an unreadable route suggestion.", 502) from exc
    if not isinstance(parsed, dict):
        raise RouteAssistError("Gemini returned an unexpected route suggestion.", 502)

    confidence = str(parsed.get("confidence", "low")).casefold()
    if confidence not in {"high", "medium", "low"}:
        confidence = "low"
    stops: list[str] = []
    seen = {normalize_place_name(origin), normalize_place_name(destination)}
    for raw_stop in parsed.get("major_stops", []):
        stop = str(raw_stop).strip()
        stop_key = normalize_place_name(stop)
        if stop and stop_key not in seen and len(stop) <= 80:
            stops.append(stop)
            seen.add(stop_key)
        if len(stops) == 8:
            break
    return {
        "major_stops": stops,
        "summary": str(parsed.get("summary", "")).strip()[:320],
        "confidence": confidence,
    }
