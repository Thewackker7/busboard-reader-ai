from __future__ import annotations

import csv
import importlib.util
import sys
import zipfile
from datetime import date
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "scripts" / "import_kochitransport_gtfs.py"
spec = importlib.util.spec_from_file_location("gtfs_importer", SCRIPT)
importer = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = importer
spec.loader.exec_module(importer)


def write_table(archive: zipfile.ZipFile, name: str, headers: list[str], rows: list[dict[str, str]]) -> None:
    import io
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=headers)
    writer.writeheader()
    writer.writerows(rows)
    archive.writestr(name, buffer.getvalue())


def feed_zip(path: Path, end_date: str = "20261231", exceptions: list[dict[str, str]] | None = None) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        write_table(archive, "routes.txt", ["route_id", "agency_id", "route_short_name", "route_long_name"], [
            {"route_id": "R1", "agency_id": "A1", "route_short_name": "1", "route_long_name": "Palakkad local"},
        ])
        write_table(archive, "trips.txt", ["route_id", "service_id", "trip_id", "trip_headsign"], [
            {"route_id": "R1", "service_id": "S1", "trip_id": "T1", "trip_headsign": "Ottapalam"},
        ])
        write_table(archive, "stops.txt", ["stop_id", "stop_name"], [
            {"stop_id": "P", "stop_name": "Palakkad"},
            {"stop_id": "M", "stop_name": "Manakara"},
            {"stop_id": "A", "stop_name": "Parali"},
            {"stop_id": "T", "stop_name": "Thenur"},
            {"stop_id": "O", "stop_name": "Ottapalam"},
        ])
        write_table(archive, "stop_times.txt", ["trip_id", "arrival_time", "departure_time", "stop_id", "stop_sequence"], [
            {"trip_id": "T1", "arrival_time": "09:00:00", "departure_time": "09:00:00", "stop_id": "P", "stop_sequence": "1"},
            {"trip_id": "T1", "arrival_time": "09:10:00", "departure_time": "09:10:00", "stop_id": "M", "stop_sequence": "2"},
            {"trip_id": "T1", "arrival_time": "09:20:00", "departure_time": "09:20:00", "stop_id": "A", "stop_sequence": "3"},
            {"trip_id": "T1", "arrival_time": "09:30:00", "departure_time": "09:30:00", "stop_id": "T", "stop_sequence": "4"},
            {"trip_id": "T1", "arrival_time": "09:45:00", "departure_time": "09:45:00", "stop_id": "O", "stop_sequence": "5"},
        ])
        write_table(archive, "calendar.txt", ["service_id", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "start_date", "end_date"], [
            {"service_id": "S1", "monday": "1", "tuesday": "1", "wednesday": "1", "thursday": "1", "friday": "1", "saturday": "1", "sunday": "1", "start_date": "20260101", "end_date": end_date},
        ])
        if exceptions:
            write_table(archive, "calendar_dates.txt", ["service_id", "date", "exception_type"], [
                {"service_id": "S1", **row} for row in exceptions
            ])


def test_importer_uses_stop_sequence_and_service_calendar(tmp_path):
    archive = tmp_path / "feed.zip"
    feed_zip(archive)
    data = importer.import_gtfs(archive, today=date(2026, 10, 25))
    assert data["data_status"] == "current_gtfs"
    assert data["data_verified"] is True
    assert data["license"] == "ODbL-1.0"
    assert data["attribution"] == "Jungle Bus © OpenStreetMap contributors"
    assert data["coverage_complete"] is False
    assert data["routes"][0]["stops"] == ["Palakkad", "Manakara", "Parali", "Thenur", "Ottapalam"]
    assert data["routes"][0]["board_destinations"] == ["Ottapalam"]
    assert data["routes"][0]["verified"] is True
    assert data["routes"][0]["trip_schedules"][0]["stop_times"][2]["arrival_time"] == "09:20:00"


def test_importer_marks_expired_service_as_stale(tmp_path):
    archive = tmp_path / "stale.zip"
    feed_zip(archive, end_date="20230325")
    data = importer.import_gtfs(archive, today=date(2026, 10, 25))
    assert data["data_status"] == "stale_or_inactive_gtfs"
    assert data["data_verified"] is False
    assert data["routes"][0]["verified"] is True


def test_calendar_date_addition_and_removal_override_weekday_rules(tmp_path):
    archive = tmp_path / "exceptions.zip"
    feed_zip(archive, exceptions=[{"date": "20261025", "exception_type": "2"}])
    removed = importer.import_gtfs(archive, today=date(2026, 10, 25))
    assert removed["data_status"] == "stale_or_inactive_gtfs"

    feed_zip(archive, end_date="20230325", exceptions=[{"date": "20261025", "exception_type": "1"}])
    added = importer.import_gtfs(archive, today=date(2026, 10, 25))
    assert added["data_status"] == "current_gtfs"
    assert added["service_end"] == "2026-10-25"
