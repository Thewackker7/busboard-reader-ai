"""Import an ODbL GTFS ZIP into ordered, service-date-aware route records.

Usage: python backend/scripts/import_kochitransport_gtfs.py feed.zip output.json
The importer does not download or silently refresh data. Recheck the publisher's
feed date and attribution before deploying a newly downloaded archive.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

SOURCE_URL = "https://github.com/Jungle-Bus/KochiTransport"
FEED_URL = "https://jungle-bus.github.io/KochiTransport/KochiTransport.zip"
ATTRIBUTION = "Jungle Bus © OpenStreetMap contributors"
LICENSE = "ODbL-1.0"
COVERAGE = "Greater Cochin Development Authority subset; intercity buses, airport feeders, Kochi Metro and Water Metro excluded."


def _rows(archive: zipfile.ZipFile, filename: str) -> list[dict[str, str]]:
    matches = [name for name in archive.namelist() if name.casefold().endswith("/" + filename.casefold()) or name.casefold() == filename.casefold()]
    if not matches:
        return []
    with archive.open(matches[0]) as raw:
        import io
        return list(csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")))


def _parse_gtfs_date(value: str | None) -> date | None:
    try:
        return datetime.strptime(str(value), "%Y%m%d").date()
    except (ValueError, TypeError):
        return None


def _active(rule: dict[str, Any], day: date) -> bool:
    exception = rule.get("exceptions", {}).get(day.strftime("%Y%m%d"))
    if str(exception) == "1":
        return True
    if str(exception) == "2":
        return False
    start, end = _parse_gtfs_date(rule.get("start_date")), _parse_gtfs_date(rule.get("end_date"))
    return bool(start and end and start <= day <= end and day.weekday() in rule.get("weekdays", []))


def import_gtfs(feed_zip: str | Path, *, today: date | None = None) -> dict[str, Any]:
    today = today or date.today()
    with zipfile.ZipFile(feed_zip) as archive:
        stops = {row["stop_id"]: row for row in _rows(archive, "stops.txt") if row.get("stop_id") and row.get("stop_name")}
        routes = {row["route_id"]: row for row in _rows(archive, "routes.txt") if row.get("route_id")}
        agencies = {row.get("agency_id", ""): row.get("agency_name", "") for row in _rows(archive, "agency.txt")}
        trips = _rows(archive, "trips.txt")
        stop_times = _rows(archive, "stop_times.txt")
        calendars = _rows(archive, "calendar.txt")
        exceptions = _rows(archive, "calendar_dates.txt")

    rules: dict[str, dict[str, Any]] = {}
    weekday_columns = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    for row in calendars:
        service_id = row.get("service_id", "")
        if service_id:
            rules[service_id] = {
                "service_id": service_id,
                "start_date": row.get("start_date", ""),
                "end_date": row.get("end_date", ""),
                "weekdays": [index for index, column in enumerate(weekday_columns) if row.get(column) == "1"],
                "exceptions": {},
            }
    for row in exceptions:
        service_id = row.get("service_id", "")
        if not service_id:
            continue
        rule = rules.setdefault(service_id, {"service_id": service_id, "start_date": "", "end_date": "", "weekdays": [], "exceptions": {}})
        rule["exceptions"][row.get("date", "")] = row.get("exception_type", "")

    times_by_trip: dict[str, list[dict[str, str]]] = {}
    for row in stop_times:
        if row.get("trip_id") and row.get("stop_id") in stops:
            times_by_trip.setdefault(row["trip_id"], []).append(row)
    for rows in times_by_trip.values():
        rows.sort(key=lambda row: (int(row.get("stop_sequence") or 0), row.get("stop_id", "")))

    patterns: dict[tuple[str, str, tuple[str, ...]], dict[str, Any]] = {}
    service_end_dates: list[date] = []
    service_start_dates: list[date] = []
    added_service_dates = [
        parsed for row in exceptions if row.get("exception_type") == "1"
        if (parsed := _parse_gtfs_date(row.get("date"))) is not None
    ]
    for rule in rules.values():
        start, end = _parse_gtfs_date(rule.get("start_date")), _parse_gtfs_date(rule.get("end_date"))
        if start:
            service_start_dates.append(start)
        if end:
            service_end_dates.append(end)
    for trip in trips:
        trip_id, route_id = trip.get("trip_id", ""), trip.get("route_id", "")
        ordered = times_by_trip.get(trip_id, [])
        if len(ordered) < 2 or route_id not in routes:
            continue
        stop_ids = tuple(row["stop_id"] for row in ordered)
        headsign = (trip.get("trip_headsign") or "").strip()
        key = (route_id, headsign, stop_ids)
        pattern = patterns.setdefault(key, {
            "id": "gtfs-" + hashlib.sha1((route_id + "|" + trip_id + "|" + "|".join(stop_ids)).encode("utf-8")).hexdigest()[:14],
            "gtfs_route_id": route_id,
            "trip_ids": [],
            "operator": agencies.get(routes[route_id].get("agency_id", "")) or routes[route_id].get("agency_id") or "GTFS agency",
            "route_name": routes[route_id].get("route_long_name") or routes[route_id].get("route_short_name") or route_id,
            "headsign": headsign,
            "board_destinations": [],
            "stops": [stops[stop_id]["stop_name"].strip() for stop_id in stop_ids],
            "stop_ids": list(stop_ids),
            "service_rules": [],
            "trip_schedules": [],
            "source": SOURCE_URL,
            "license": LICENSE,
            "attribution": ATTRIBUTION,
            "verified": False,
            "demo_only": False,
        })
        pattern["trip_ids"].append(trip_id)
        rule = rules.get(trip.get("service_id", ""))
        if rule and rule not in pattern["service_rules"]:
            pattern["service_rules"].append(rule)
        pattern["trip_schedules"].append({
            "trip_id": trip_id,
            "service_rule": rule or {},
            "stop_times": [{
                "arrival_time": row.get("arrival_time") or None,
                "departure_time": row.get("departure_time") or None,
            } for row in ordered],
        })
        terminal = pattern["stops"][-1]
        for candidate in [headsign, terminal]:
            if candidate and candidate not in pattern["board_destinations"]:
                pattern["board_destinations"].append(candidate)

    output_routes = list(patterns.values())
    for route in output_routes:
        route["verified"] = bool(route["source"] and route["service_rules"])
    active = [route for route in output_routes if route["verified"] and any(_active(rule, today) for rule in route["service_rules"])]
    service_end_dates.extend(added_service_dates)
    service_start_dates.extend(added_service_dates)
    valid_to = max(service_end_dates).isoformat() if service_end_dates else None
    valid_from = min(service_start_dates).isoformat() if service_start_dates else None
    return {
        "data_status": "current_gtfs" if active else "stale_or_inactive_gtfs",
        "data_verified": bool(active),
        "verified_on": today.isoformat(),
        "valid_from": valid_from,
        "service_end": valid_to,
        "source": SOURCE_URL,
        "feed_url": FEED_URL,
        "license": LICENSE,
        "attribution": ATTRIBUTION,
        "coverage": COVERAGE,
        "coverage_complete": False,
        "coverage_reason": "KochiTransport covers only a subset of Greater Kochi, so a missing route or stop in this feed cannot prove that no service exists.",
        "notice": f"Community GTFS, calendar checked for {today.isoformat()}; coverage: {COVERAGE} Verify with the operator before travel.",
        "stale_reason": "This GTFS feed has no service active for today or its published service dates have expired.",
        "stop_aliases": {row["stop_name"].strip(): [row["stop_name"].strip()] for row in stops.values()},
        "routes": output_routes,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("feed_zip", type=Path)
    parser.add_argument("output_json", type=Path)
    args = parser.parse_args()
    result = import_gtfs(args.feed_zip)
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Imported {len(result['routes'])} ordered route patterns ({result['data_status']}).")


if __name__ == "__main__":
    main()
