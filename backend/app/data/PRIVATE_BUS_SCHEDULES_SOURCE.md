# Route data sources and verification limits

## Kerala Private Bus Timing

The source repository describes JSON schedules converted from Motor Vehicles Department RTI PDFs. It provides ordered stop lists and extracted arrival/departure times, but warns that coverage may be incomplete and conversion may contain parsing errors. The public repository currently has no `LICENSE` file. The imported compressed file is retained locally as a historical user workspace artifact, but the application no longer loads, serves, or uses it for place suggestions or verification until the rights holder publishes reuse terms. Do not redistribute it.

Source: https://github.com/amith-vp/Kerala-Private-Bus-Timing

## KochiTransport GTFS

The Jungle Bus feed is GTFS ZIP data with `routes.txt`, `trips.txt`, `stop_times.txt`, `stops.txt`, and `calendar.txt`. Its stop sequence is recovered from `trip_id` and numeric `stop_sequence`; service dates and exceptions are checked. The feed is licensed ODbL-1.0, requires attribution `Jungle Bus © OpenStreetMap contributors`, and adapted data must stay under ODbL. Coverage is a Greater Cochin Development Authority subset. The publisher says it excludes intercity buses (including mofussil/KSRTC), airport feeders, Metro and Water Metro.

The repository history documents a 2022-09 feed update. Transitland records the archived feed version's scheduled service from 2022-09-26 through 2023-03-25. That feed is stale for the current app and is not bundled or treated as evidence.

Sources:
- https://github.com/Jungle-Bus/KochiTransport
- https://www.transit.land/feeds/f-t9y-kochitransport/versions/91ec808ca708765dda298ee931c7d18b281df6de

## Importing a refreshed GTFS ZIP

After checking that the publisher has supplied a current feed and retaining the ODbL attribution/share-alike terms, run:

```powershell
python backend/scripts/import_kochitransport_gtfs.py C:\data\KochiTransport.zip backend\app\data\kochitransport_gtfs.json
```

The importer emits ordered trip patterns and marks the dataset current only when at least one imported service is active on the import date. It honors `calendar.txt` and `calendar_dates.txt`. The API uses only active GTFS routes. The frontend still labels Gemini's corridor suggestions as unverified. The feed is not statewide and cannot establish that absent routes do not exist.

Until a current feed is supplied, demo routes remain examples only; journey verification returns `UNABLE_TO_VERIFY` with a reason. Gemini suggestions may help identify likely major towns but never certify a bus, stop, service, or timetable.
