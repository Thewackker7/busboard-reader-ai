
from __future__ import annotations

import json
import os
from difflib import get_close_matches
from pathlib import Path
from typing import Literal

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .route_engine import verify_journey
from .route_engine import canonical_stop, normalize, normalize_place_name, route_is_current, service_rule_is_active
from .route_assistant import RouteAssistError, suggest_major_stops
from .vision import VisionError, read_bus_board

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR.parent / ".env")
ROUTE_DATA_PATH = BASE_DIR / "data" / "routes.json"

ALLOWED_MIME_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
}
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_SPEECH_CHARS = 1_000
SARVAM_TTS_URL = "https://api.sarvam.ai/text-to-speech/stream"
SARVAM_TIMEOUT_SECONDS = 20


class SpeechRequest(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_SPEECH_CHARS)
    language: Literal["en-IN", "ml-IN"]


class RouteAssistRequest(BaseModel):
    origin: str = Field(min_length=1, max_length=100)
    destination: str = Field(min_length=1, max_length=100)


async def request_sarvam_speech(text: str, language: str) -> bytes:
    """Fetch Sarvam streaming MP3 audio without exposing upstream details."""
    if language not in {"en-IN", "ml-IN"}:
        raise ValueError("Unsupported speech language.")
    cleaned_text = text.strip()
    if not cleaned_text:
        raise ValueError("Speech text cannot be empty.")
    if len(cleaned_text) > MAX_SPEECH_CHARS:
        raise ValueError(f"Speech text must be {MAX_SPEECH_CHARS} characters or fewer.")
    api_key = os.getenv("SARVAM_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("Sarvam API key is not configured. Add SARVAM_API_KEY to backend/.env and restart the backend.")
    model = os.getenv("SARVAM_TTS_MODEL", "bulbul:v3").strip() or "bulbul:v3"
    payload = {
        "text": cleaned_text,
        "language_code": language,
        "speaker": "shubh",
        "model": model,
        "output_audio_codec": "mp3",
    }
    try:
        timeout = httpx.Timeout(SARVAM_TIMEOUT_SECONDS, connect=8)
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "POST", SARVAM_TTS_URL,
                headers={"api-subscription-key": api_key, "Content-Type": "application/json"},
                json=payload,
            ) as upstream:
                status_code = upstream.status_code
                if status_code >= 400:
                    await upstream.aread()
                    raise httpx.HTTPStatusError("Sarvam TTS request failed", request=upstream.request, response=upstream)
                audio = b"".join([chunk async for chunk in upstream.aiter_bytes()])
    except httpx.TimeoutException as exc:
        raise TimeoutError("Sarvam speech request timed out. Please try again.") from exc
    except httpx.RequestError as exc:
        raise ConnectionError("Could not reach Sarvam speech service. Check the backend network connection and retry.") from exc
    except httpx.HTTPStatusError as exc:
        raise exc
    if not audio:
        raise RuntimeError("Sarvam returned empty audio.")
    return audio


def is_valid_image_bytes(image_bytes: bytes, mime_type: str) -> bool:
    """Cheap signature check before sending untrusted bytes to an external API."""
    signatures = {
        "image/jpeg": (b"\xff\xd8\xff",),
        "image/png": (b"\x89PNG\r\n\x1a\n",),
        "image/webp": (b"RIFF",),
    }
    if not image_bytes.startswith(signatures[mime_type]):
        return False
    return mime_type != "image/webp" or len(image_bytes) >= 12 and image_bytes[8:12] == b"WEBP"

with ROUTE_DATA_PATH.open("r", encoding="utf-8") as file:
    DEMO_ROUTE_DATA = json.load(file)
GTFS_DATA_PATH = BASE_DIR / "data" / "kochitransport_gtfs.json"
PLACES_PATH = BASE_DIR / "data" / "kerala_places.json"
with PLACES_PATH.open("r", encoding="utf-8") as file:
    PLACE_RECORDS = json.load(file)


def _load_route_data() -> dict[str, object]:
    """Prefer imported GTFS; otherwise use curated source-backed records, never demo paths."""
    base_aliases = dict(DEMO_ROUTE_DATA.get("stop_aliases", {}))
    for place in PLACE_RECORDS:
        name = place.get("name", "").strip()
        if name and name not in base_aliases:
            base_aliases[name] = [name]
    if GTFS_DATA_PATH.exists():
        try:
            imported = json.loads(GTFS_DATA_PATH.read_text(encoding="utf-8"))
            aliases = {**base_aliases, **imported.get("stop_aliases", {})}
            return {**imported, "stop_aliases": aliases, "place_records": PLACE_RECORDS}
        except (OSError, json.JSONDecodeError):
            pass
    return {**DEMO_ROUTE_DATA, "stop_aliases": base_aliases, "place_records": PLACE_RECORDS}


ROUTE_DATA = _load_route_data()
ACTIVE_ROUTE_RECORDS = [
    route for route in ROUTE_DATA.get("routes", [])
    if ROUTE_DATA.get("data_status") in {"current_gtfs", "verified_routes"} and route_is_current(route)
]
ACTIVE_STOP_KEYS = {
    normalize(stop)
    for route in ACTIVE_ROUTE_RECORDS for stop in route.get("stops", [])
    if normalize(stop)
}
BUS_PLACE_SUGGESTIONS = list(PLACE_RECORDS)
_seen_place_keys = {normalize(item["name"]) for item in BUS_PLACE_SUGGESTIONS}
for stop in sorted({stop for route in ACTIVE_ROUTE_RECORDS for stop in route.get("stops", [])}, key=str.casefold):
    if normalize(stop) not in _seen_place_keys:
        BUS_PLACE_SUGGESTIONS.append({"name": stop, "district": None})
        _seen_place_keys.add(normalize(stop))

app = FastAPI(
    title="BusBoard Reader AI API",
    version="0.1.0",
)

# Allow the local Vite frontend to communicate with FastAPI.
allowed_origins = [
    "http://localhost:5173",
    "http://localhost:5174",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:5174",
]

# Optionally allow a configured frontend origin too.
configured_origin = os.getenv("FRONTEND_ORIGIN")
if configured_origin and configured_origin not in allowed_origins:
    allowed_origins.append(configured_origin)

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "service": "busboard-reader-ai",
    }


@app.get("/api/routes")
def get_routes() -> dict[str, object]:
    return {
        "stops": sorted({stop for route in ACTIVE_ROUTE_RECORDS for stop in route.get("stops", [])}, key=str.casefold),
        "route_count": len(ACTIVE_ROUTE_RECORDS),
        "data_status": ROUTE_DATA.get("data_status", "unknown"),
        "notice": ROUTE_DATA.get("notice", ""),
    }


@app.get("/api/places")
def get_places(q: str = "") -> dict[str, object]:
    """Return curated place records and imported GTFS stops, if present."""
    query = normalize_place_name(q)
    if len(query) >= 2:
        places = [
            place for place in BUS_PLACE_SUGGESTIONS
            if query in normalize_place_name(place.get("name"))
            or query in normalize_place_name(place.get("district"))
        ]
        if not places and len(query) >= 4:
            close_names = set(get_close_matches(query, [normalize_place_name(place["name"]) for place in BUS_PLACE_SUGGESTIONS], n=8, cutoff=0.76))
            places = [place for place in BUS_PLACE_SUGGESTIONS if normalize_place_name(place["name"]) in close_names]
    else:
        places = list(PLACE_RECORDS)
    return {"places": places}


@app.get("/api/buses")
def get_bus_options(origin: str, destination: str, limit: int = 12) -> dict[str, object]:
    """List currently active source-backed paths; schedule times are omitted unless imported."""
    if not origin.strip() or not destination.strip():
        raise HTTPException(status_code=422, detail="Origin and destination are required.")
    aliases = ROUTE_DATA.get("stop_aliases", {})
    origin_name = canonical_stop(origin, aliases)
    destination_name = canonical_stop(destination, aliases)
    options: list[dict[str, object]] = []
    if origin_name and destination_name and ROUTE_DATA.get("data_status") in {"current_gtfs", "verified_routes"}:
        for route in ACTIVE_ROUTE_RECORDS:
            stops = [canonical_stop(stop, aliases) or stop for stop in route.get("stops", [])]
            if origin_name not in stops:
                continue
            start = stops.index(origin_name)
            end = next((i for i in range(start + 1, len(stops)) if stops[i] == destination_name), None)
            if end is None:
                continue
            trip_schedules = [
                item for item in route.get("trip_schedules", [])
                if service_rule_is_active(item.get("service_rule", {}))
            ]
            if not trip_schedules:
                trip_schedules = [{"trip_id": (route.get("trip_ids") or [None])[0], "stop_times": []}]
            for trip in trip_schedules:
                times = trip.get("stop_times", [])
                departure = (times[start].get("departure_time") or times[start].get("arrival_time")) if len(times) > start else None
                arrival = (times[end].get("arrival_time") or times[end].get("departure_time")) if len(times) > end else None
                options.append({
                    "vehicle_number": route.get("operator") or "Operator not listed",
                    "trip_number": trip.get("trip_id"),
                    "stops": route.get("stops", [])[start:end + 1],
                    "departure_time": departure,
                    "arrival_time": arrival,
                    "timing_available": bool(departure and arrival),
                    "route_id": route.get("id"),
                    "source": route.get("source"),
                })
    options.sort(key=lambda option: (not option["timing_available"], str(option["departure_time"] or ""), str(option["vehicle_number"])))
    safe_limit = max(1, min(limit, 50))
    notice = ROUTE_DATA.get("notice") or "No current, licensed timetable feed is installed. Route results cannot be verified."
    return {
        "origin": origin,
        "destination": destination,
        "count": len(options),
        "options": options[:safe_limit],
        "source": ROUTE_DATA.get("source"),
        "notice": notice,
    }


async def _build_route_assist(origin: str, destination: str) -> dict[str, object]:
    aliases = ROUTE_DATA.get("stop_aliases", {})
    origin_key = normalize_place_name(canonical_stop(origin, aliases) or origin)
    destination_key = normalize_place_name(canonical_stop(destination, aliases) or destination)
    if origin_key == destination_key:
        raise RouteAssistError("Choose two different places for the route check.", 422)

    dataset_options = get_bus_options(origin, destination, limit=8)["options"]
    dataset_routes = [option["stops"] for option in dataset_options]
    suggestion = await suggest_major_stops(origin, destination, dataset_routes)
    route_stop_keys = {normalize_place_name(stop) for route in dataset_routes for stop in route}
    stops = []
    for stop in suggestion["major_stops"]:
        stop_key = normalize_place_name(canonical_stop(stop, aliases) or stop)
        if stop_key in {origin_key, destination_key}:
            continue
        if stop_key in route_stop_keys:
            evidence = "listed_on_matching_timetable_route"
        elif stop_key in ACTIVE_STOP_KEYS:
            evidence = "listed_elsewhere_in_timetable_data"
        else:
            evidence = "gemini_suggestion"
        stops.append({"name": stop, "evidence": evidence})

    return {
        "origin": origin,
        "destination": destination,
        "summary": suggestion["summary"],
        "confidence": suggestion["confidence"],
        "major_stops": stops,
        "timetable_routes": dataset_routes,
        "notice": "Gemini suggestions do not certify service. Stops marked as listed are backed by the active route dataset; Gemini-only suggestions remain unconfirmed.",
    }


@app.post("/api/route-assist")
async def route_assist(request: RouteAssistRequest) -> dict[str, object]:
    origin = request.origin.strip()
    destination = request.destination.strip()
    if not origin or not destination:
        raise HTTPException(status_code=422, detail="Origin and destination are required.")
    try:
        return await _build_route_assist(origin, destination)
    except RouteAssistError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@app.post("/api/speech")
async def speech(request: SpeechRequest) -> Response:
    try:
        audio = await request_sarvam_speech(request.text, request.language)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except ConnectionError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status in {401, 403}:
            detail = "Sarvam rejected the API key. Check SARVAM_API_KEY in backend/.env."
        elif status == 429:
            detail = "Sarvam rate limit or credits are exhausted. Wait and check your Sarvam account quota."
        elif 500 <= status <= 599:
            detail = "Sarvam speech service is temporarily unavailable. Please retry shortly."
        else:
            detail = "Sarvam rejected the speech request. Please try shorter text."
        raise HTTPException(status_code=status if status in {401, 403, 429} else 503, detail=detail) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return Response(content=audio, media_type="audio/mpeg", headers={"Cache-Control": "no-store"})


@app.post("/api/verify")
async def verify(
    origin: str = Form(...),
    destination: str = Form(...),
    photo: UploadFile = File(...),
) -> dict[str, object]:
    if not origin.strip() or not destination.strip():
        raise HTTPException(
            status_code=422,
            detail="Origin and destination are required.",
        )

    if photo.content_type not in ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=415,
            detail="Please upload a JPG, PNG, or WebP image.",
        )

    image_bytes = await photo.read()

    if not image_bytes:
        raise HTTPException(
            status_code=400,
            detail="The uploaded file is empty.",
        )

    if len(image_bytes) > MAX_FILE_BYTES:
        raise HTTPException(
            status_code=413,
            detail="Image is too large. Please upload an image under 10 MB.",
        )
    if not is_valid_image_bytes(image_bytes, photo.content_type):
        raise HTTPException(status_code=400, detail="The uploaded file is not a valid image for its declared type.")

    try:
        scan = await read_bus_board(
            image_bytes,
            photo.content_type,
            demo_destination=destination,
        )
    except VisionError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        # Avoid exposing API keys or internal SDK details.
        raise HTTPException(
            status_code=502,
            detail=(
                "Image recognition failed. "
                "Check the API key, model availability, "
                "network connection, and API quota."
            ),
        ) from exc

    result = verify_journey(
        origin_input=origin,
        destination_input=destination,
        detected_text=scan["detected_text"],
        route_data=ROUTE_DATA,
        confidence="low" if scan.get("recognition_uncertain") else None,
        english_destination=scan.get("destination_english"),
    )

    result["scan"] = scan
    return result


@app.post("/api/verify-manual")
async def verify_manual(
    origin: str = Form(...),
    destination: str = Form(...),
    confirmed_destination: str = Form(...),
    original_detected_text: str = Form(""),
) -> dict[str, object]:
    """Re-check a user-confirmed board transcription without another AI request."""
    if not origin.strip() or not destination.strip() or not confirmed_destination.strip():
        raise HTTPException(status_code=422, detail="Origin, destination, and the confirmed board text are required.")
    result = verify_journey(
        origin_input=origin,
        destination_input=destination,
        detected_text=confirmed_destination,
        route_data=ROUTE_DATA,
        confidence=None,
    )
    result["scan"] = {
        "detected_text": confirmed_destination.strip(),
        "original_transcription": original_detected_text.strip(),
        "confidence": None,
        "notes": "Destination text was confirmed by the user; Gemini was not called again.",
        "provider": "manual_review",
        "demo_mode": False,
        "user_confirmed": True,
    }
    return result
