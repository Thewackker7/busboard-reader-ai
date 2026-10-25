"""Conservative, provenance-aware route matching for BusBoard Reader AI."""
from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from difflib import SequenceMatcher
from typing import Any

DASHES = "\u2010\u2011\u2012\u2013\u2014\u2212"


def normalize_text(value: str | None) -> str:
    """Unicode-safe comparison key; retains letters and marks in every script."""
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value)).casefold().strip()
    text = "".join(" " if unicodedata.category(char)[0] in {"P", "S"} else char for char in text)
    return " ".join(text.split())


def normalize_place_name(value: str | None) -> str:
    return normalize_text(value)


def normalize(value: str | None) -> str:
    """Backward-compatible name used by the API place search."""
    return normalize_text(value)


def normalize_board_text(value: str | None) -> str:
    """Keep route separators visible while folding dash and whitespace variants."""
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value)).casefold().strip()
    text = re.sub(f"[{re.escape(DASHES)}]", "-", text)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s+\bto\b\s+", " - ", text)
    text = re.sub(r"\s*[-/]\s*", " - ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _damerau_distance_one(left: str, right: str) -> bool:
    """Accept one OCR typo, including a transposed adjacent pair."""
    if left == right:
        return True
    if abs(len(left) - len(right)) > 1:
        return False
    rows = len(left) + 1
    cols = len(right) + 1
    matrix = [[0] * cols for _ in range(rows)]
    for i in range(rows):
        matrix[i][0] = i
    for j in range(cols):
        matrix[0][j] = j
    for i in range(1, rows):
        for j in range(1, cols):
            cost = 0 if left[i - 1] == right[j - 1] else 1
            matrix[i][j] = min(matrix[i - 1][j] + 1, matrix[i][j - 1] + 1, matrix[i - 1][j - 1] + cost)
            if i > 1 and j > 1 and left[i - 1] == right[j - 2] and left[i - 2] == right[j - 1]:
                matrix[i][j] = min(matrix[i][j], matrix[i - 2][j - 2] + 1)
    return matrix[-1][-1] <= 1


def canonical_stop(value: str | None, aliases: dict[str, list[str]]) -> str | None:
    value_n = normalize_place_name(value)
    if not value_n:
        return None
    names = [(canonical, [canonical, *variants]) for canonical, variants in aliases.items()]
    for canonical, variants in names:
        if any(value_n == normalize_place_name(name) for name in variants):
            return canonical

    # Strip only known UI/address wrappers. Do not collapse a district name into
    # its similarly named town unless that exact alias is explicitly configured.
    wrappers = (" district kerala", " district tamil nadu", " kerala", " tamil nadu", " bus stand", " bus stop")
    for wrapper in wrappers:
        if value_n.endswith(wrapper):
            stripped = value_n[: -len(wrapper)].strip()
            for canonical, variants in names:
                if any(stripped == normalize_place_name(name) for name in variants):
                    return canonical

    # A district alone is not the same entity as a town with the same name.
    if value_n.endswith(" district"):
        return None

    # Board labels may include a short service descriptor after a known stop.
    partial = set()
    for canonical, variants in names:
        for name in variants:
            key = normalize_place_name(name)
            if len(key) >= 4 and re.search(rf"(?<!\w){re.escape(key)}(?!\w)", value_n):
                partial.add(canonical)
    if len(partial) == 1:
        return next(iter(partial))

    # A single unambiguous edit catches common OCR typos without broad fuzzy matching.
    fuzzy = set()
    for canonical, variants in names:
        if any(len(value_n) >= 6 and _damerau_distance_one(value_n, normalize_place_name(name)) for name in variants):
            fuzzy.add(canonical)
    if len(fuzzy) == 1:
        return next(iter(fuzzy))
    scores = sorted(
        ((SequenceMatcher(None, value_n, normalize_place_name(name)).ratio(), canonical)
         for canonical, variants in names for name in variants),
        reverse=True,
    )
    if scores and scores[0][0] >= 0.90 and (len(scores) == 1 or scores[0][0] - scores[1][0] >= 0.06):
        return scores[0][1]
    return None


def _place_occurrences(value: str, aliases: dict[str, list[str]]) -> list[tuple[int, int, str]]:
    haystack = normalize_text(value)
    found: list[tuple[int, int, str]] = []
    for canonical, variants in aliases.items():
        for raw_name in [canonical, *variants]:
            needle = normalize_text(raw_name)
            if not needle:
                continue
            pattern = re.compile(rf"(?<!\w){re.escape(needle)}(?!\w)")
            found.extend((m.start(), m.end(), canonical) for m in pattern.finditer(haystack))
    found.sort(key=lambda item: (item[0], -(item[1] - item[0]), item[2]))
    selected: list[tuple[int, int, str]] = []
    for match in found:
        if selected and match[0] < selected[-1][1]:
            continue
        selected.append(match)
    return selected


def parse_board_route(board_text: str | None, aliases: dict[str, list[str]], english_destination: str | None = None) -> dict[str, str | None]:
    """Parse known place names conservatively; hyphens alone never imply a route."""
    original = "" if board_text is None else str(board_text).strip()
    parts = re.split(r"\s*(?:[-/]|(?i:\bto\b))\s*", original)
    if len(parts) >= 2:
        left = canonical_stop(parts[0], aliases)
        right = canonical_stop(parts[-1], aliases)
        # A separator is a route delimiter only when both sides resolve. If only
        # the right side resolves, it is still a plausible single destination.
        if left and right and left != right:
            return {"origin": left, "destination": right}
        translated = canonical_stop(english_destination, aliases)
        if translated:
            return {"origin": left, "destination": translated} if left and left != translated else {"origin": None, "destination": translated}
        if right:
            return {"origin": None, "destination": right}
        return {"origin": None, "destination": None}
    occurrences = _place_occurrences(original, aliases)
    if len(occurrences) >= 2:
        origin = occurrences[0][2]
        destination = occurrences[-1][2]
        if origin != destination:
            return {"origin": origin, "destination": destination}
    if len(occurrences) == 1 and english_destination:
        translated = canonical_stop(english_destination, aliases)
        if translated and translated != occurrences[0][2]:
            return {"origin": occurrences[0][2], "destination": translated}
    destination = canonical_stop(original, aliases)
    if not destination and english_destination:
        destination = canonical_stop(english_destination, aliases)
    return {"origin": None, "destination": destination}


def service_rule_is_active(rule: dict[str, Any], today: date | None = None) -> bool:
    return _service_active(rule, today or date.today())


def _service_active(rule: dict[str, Any], today: date) -> bool:
    exceptions = rule.get("exceptions", {})
    exception = exceptions.get(today.strftime("%Y%m%d"), exceptions.get(today.isoformat()))
    if str(exception) == "1":
        return True
    if str(exception) == "2":
        return False
    try:
        start = datetime.strptime(str(rule.get("start_date", "")), "%Y%m%d").date()
        end = datetime.strptime(str(rule.get("end_date", "")), "%Y%m%d").date()
    except ValueError:
        try:
            start = date.fromisoformat(str(rule.get("start_date", "")))
            end = date.fromisoformat(str(rule.get("end_date", "")))
        except ValueError:
            return False
    weekdays = rule.get("weekdays", [])
    return start <= today <= end and today.weekday() in weekdays


def route_is_current(route: dict[str, Any], today: date | None = None) -> bool:
    today = today or date.today()
    if route.get("demo_only") or route.get("verified") is not True or not route.get("source"):
        return False
    rules = route.get("service_rules", [])
    if rules:
        return any(_service_active(rule, today) for rule in rules)
    try:
        start = date.fromisoformat(str(route.get("valid_from", "")))
        end = date.fromisoformat(str(route.get("valid_to", "")))
    except ValueError:
        return False
    return start <= today <= end


def _dataset_current(route_data: dict[str, Any], today: date) -> bool:
    if route_data.get("data_verified") is not True:
        return False
    if route_data.get("data_status") not in {"current_gtfs", "verified_routes"}:
        return False
    end_value = route_data.get("service_end") or route_data.get("valid_to")
    if end_value:
        try:
            if date.fromisoformat(str(end_value)) < today:
                return False
        except ValueError:
            return False
    return True


def board_routes(board_text: str, routes: list[dict[str, Any]], aliases: dict[str, list[str]], english_destination: str | None = None) -> list[dict[str, Any]]:
    parsed = parse_board_route(board_text, aliases, english_destination)
    destination = parsed["destination"]
    if not destination:
        return []
    matches = []
    for route in routes:
        for board_name in route.get("board_destinations", []):
            resolved = canonical_stop(board_name, aliases) or board_name
            if normalize_place_name(destination) == normalize_place_name(resolved):
                matches.append(route)
                break
    return matches


def _route_segment(route: dict[str, Any], start: str, destination: str, aliases: dict[str, list[str]] | None = None) -> list[str] | None:
    stops = route.get("stops", [])
    resolved = [canonical_stop(stop, aliases or {}) or stop for stop in stops]
    if start not in resolved:
        return None
    start_i = resolved.index(start)
    for dest_i in range(start_i + 1, len(resolved)):
        if resolved[dest_i] == destination:
            return stops[start_i : dest_i + 1]
    return None


def find_matching_route(origin: str, destination: str, board_text: str, route_data: dict[str, Any], *, english_destination: str | None = None, english_route: str | None = None, today: date | None = None) -> tuple[dict[str, Any] | None, list[dict[str, Any]], str | None]:
    today = today or date.today()
    aliases = route_data.get("stop_aliases", {})
    origin_name = canonical_stop(origin, aliases)
    destination_name = canonical_stop(destination, aliases)
    # OCR may correctly preserve Malayalam while our route records use
    # canonical English names. Gemini's separate English route interpretation
    # is therefore the matching input; the original board text is retained for
    # review and is used whenever no translation is available.
    matching_text = english_route.strip() if english_route and english_route.strip() else board_text
    parsed = parse_board_route(matching_text, aliases, english_destination)
    if not origin_name or not destination_name:
        return None, [], "The selected origin or destination could not be resolved to a known place or stop."
    if origin_name == destination_name:
        return None, [], "Choose two different places to verify a journey."
    if not parsed["destination"]:
        return None, [], "The board text is ambiguous or does not contain a recognized destination."
    if not _dataset_current(route_data, today):
        return None, [], route_data.get("stale_reason") or "The available route records are sample-only, incomplete, stale, or not verified for current service."

    candidates = board_routes(matching_text, route_data.get("routes", []), aliases, english_destination)
    candidates = [route for route in candidates if route_is_current(route, today)]
    if not candidates:
        return None, [], "No current verified route record matches the destination shown on the board."
    for route in candidates:
        segment = _route_segment(route, origin_name, destination_name, aliases)
        if segment:
            return route, candidates, None
    if len(candidates) != 1:
        return None, candidates, "Multiple current routes share this board destination, so the route direction cannot be identified confidently."
    return None, candidates, "The identified route is current, but its ordered stops do not show your selected journey in this direction."


def find_journey_options(origin: str, destination: str, routes: list[dict[str, Any]], max_transfers: int = 1, limit: int = 4) -> list[dict[str, Any]]:
    """Find simple direct or one-transfer journey options in ordered route patterns."""
    if origin == destination:
        return [{"stops": [origin], "routes": [], "transfers": 0, "stop_count": 0}]
    options: list[dict[str, Any]] = []
    queue: list[tuple[str, list[str], list[str], list[str]]] = [(origin, [], [], [origin])]
    visited: set[tuple[str, tuple[str, ...]]] = set()
    while queue and len(options) < 100:
        current, chosen, used, path = queue.pop(0)
        if max(0, len(chosen) - 1) > max_transfers:
            continue
        state = (current, tuple(chosen))
        if state in visited:
            continue
        visited.add(state)
        for route in routes:
            route_id = str(route.get("id", ""))
            stops = route.get("stops", [])
            if route_id in used or current not in stops:
                continue
            start = stops.index(current)
            for end in range(start + 1, len(stops)):
                next_stop = stops[end]
                segment = stops[start + 1 : end + 1]
                if any(stop in path for stop in segment):
                    continue
                new_path = path + segment
                new_routes = chosen + [route_id]
                if next_stop == destination:
                    options.append({"stops": new_path, "routes": new_routes, "transfers": max(0, len(new_routes) - 1), "stop_count": len(new_path) - 1})
                elif len(new_routes) - 1 < max_transfers:
                    queue.append((next_stop, new_routes, used + [route_id], new_path))
    unique = {(tuple(item["stops"]), tuple(item["routes"])): item for item in options}
    return sorted(unique.values(), key=lambda item: (item["transfers"], item["stop_count"]))[:limit]


def _format_options(options: list[dict[str, Any]], scanned_route_id: str | None) -> list[dict[str, Any]]:
    formatted = []
    for option in options:
        if scanned_route_id and len(option["routes"]) == 1 and option["routes"][0] == scanned_route_id:
            continue
        formatted.append({
            "stops": option["stops"],
            "route_count": len(option["routes"]),
            "transfers": option["transfers"],
            "stop_count": option["stop_count"],
            "route_ids": option["routes"],
            "label": "Direct route" if option["transfers"] == 0 else f"{option['transfers']} transfer(s)",
        })
    return formatted[:3]


def verify_journey(origin_input: str, destination_input: str, detected_text: str, route_data: dict[str, Any], confidence: str | None = None, *, english_destination: str | None = None, english_route: str | None = None) -> dict[str, Any]:
    today = date.today()
    aliases = route_data.get("stop_aliases", {})
    matching_text = english_route.strip() if english_route and english_route.strip() else detected_text
    parsed = parse_board_route(matching_text, aliases, english_destination)
    normalized = normalize_board_text(detected_text)
    route, candidates, reason = find_matching_route(origin_input, destination_input, detected_text, route_data, english_destination=english_destination, english_route=english_route, today=today)

    if confidence and confidence.casefold() == "low":
        route = None
        status, reason = "unable_to_verify", "The board reading is unclear. Review the recognized text and retry."
    elif route:
        status, reason = "match", f"Current route data lists {origin_input.strip()} before {destination_input.strip()} on the same trip."
    elif candidates and len(candidates) == 1 and _dataset_current(route_data, today) and route_is_current(candidates[0], today) and route_data.get("coverage_complete") is True:
        status = "mismatch"
    else:
        status = "unable_to_verify"
        if candidates and len(candidates) == 1 and _dataset_current(route_data, today):
            reason = route_data.get("coverage_reason") or "The current source does not claim complete coverage, so a missing stop cannot confirm that no bus serves this journey."

    matched_stops = _route_segment(route, canonical_stop(origin_input, aliases) or "", canonical_stop(destination_input, aliases) or "", aliases) if route else []
    route_id = route.get("id") if route else (candidates[0].get("id") if status == "mismatch" and candidates else None)
    current_routes = [item for item in route_data.get("routes", []) if _dataset_current(route_data, today) and route_is_current(item, today)]
    options = find_journey_options(canonical_stop(origin_input, aliases) or "", canonical_stop(destination_input, aliases) or "", current_routes)
    alternatives = _format_options(options, route_id)
    source = (route or (candidates[0] if route_id else {})).get("source") if (route or route_id) else None
    verified = bool(status in {"match", "mismatch"} and source)
    def place_id(value: str | None) -> str | None:
        if not value:
            return None
        for record in route_data.get("place_records", []):
            if normalize_place_name(record.get("name")) == normalize_place_name(value):
                return record.get("id")
        return None
    origin_name = canonical_stop(origin_input, aliases)
    destination_name = canonical_stop(destination_input, aliases)
    title = {"match": "This bus appears to match your journey", "mismatch": "This identified route does not match your journey", "unable_to_verify": "This journey could not be verified"}[status]
    message = reason or "The route could not be verified from the available evidence."
    result = {
        "status": status,
        "verification_status": status.upper(),
        "title": title,
        "message": message,
        "reason": message,
        "board_text_original": detected_text or "",
        "board_text_normalized": normalized,
        "board_text_english": matching_text if english_route else None,
        "selected_origin": origin_input,
        "selected_destination": destination_input,
        "selected_origin_id": place_id(origin_name),
        "selected_destination_id": place_id(destination_name),
        "board_origin": parsed["origin"],
        "board_destination": parsed["destination"],
        "board_origin_id": place_id(parsed["origin"]),
        "board_destination_id": place_id(parsed["destination"]),
        "detected_destination": detected_text or None,
        "normalized_destination": parsed["destination"],
        "matched_route_id": route_id,
        "matched_route": route_id,
        "matched_stops": matched_stops or [],
        "alternatives": alternatives,
        "data_source": source,
        "data_verified": verified,
        "data_notice": route_data.get("notice", ""),
    }
    if route:
        result["route_evidence"] = {
            "route_name": route.get("route_name"),
            "operator": route.get("operator"),
            "source_name": route.get("source_name") or source,
            "source_url": route.get("source_url"),
            "current_confirmation_name": route.get("current_confirmation_name"),
            "current_confirmation_url": route.get("current_confirmation_url"),
            "last_verified_at": route.get("last_verified_at"),
            "verification_method": route.get("verification_method"),
        }
    return result
