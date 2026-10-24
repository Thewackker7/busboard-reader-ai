"""Compact the upstream Kerala private bus timing JSON files for local use.

Usage: python import_private_bus_schedules.py <upstream-repo> <output.json.gz>
"""
from __future__ import annotations

import gzip
import json
import subprocess
import sys
from pathlib import Path


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: import_private_bus_schedules.py <upstream-repo> <output.json.gz>")
    source_dir = Path(sys.argv[1]).resolve()
    output_path = Path(sys.argv[2]).resolve()
    revision = subprocess.check_output(
        ["git", "-C", str(source_dir), "rev-parse", "HEAD"], text=True
    ).strip()
    buses = []
    for source_file in sorted(source_dir.glob("*.json")):
        source = json.loads(source_file.read_text(encoding="utf-8"))
        for bus in source.get("busSchedules", []):
            route = [str(stop).strip() for stop in bus.get("route", []) if str(stop).strip()]
            schedule = []
            for trip in bus.get("schedule", []):
                stations = [
                    {
                        "station": str(item.get("station", "")).strip(),
                        "arrivalTime": str(item.get("arrivalTime", "")).strip(),
                        "departureTime": str(item.get("departureTime", "")).strip(),
                    }
                    for item in trip.get("stations", [])
                    if str(item.get("station", "")).strip()
                ]
                if stations:
                    schedule.append({"trip": trip.get("trip"), "stations": stations})
            if route or schedule:
                buses.append({
                    "vehicle_number": str(bus.get("Vehicle Number", "")).strip(),
                    "route": route,
                    "schedule": schedule,
                })
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "source": "https://github.com/amith-vp/Kerala-Private-Bus-Timing",
        "source_revision": revision,
        "busSchedules": buses,
    }
    with output_path.open("wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as compressed:
            compressed.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    print(f"Imported {len(buses)} bus records from {source_dir}")


if __name__ == "__main__":
    main()
