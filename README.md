# BusBoard Reader AI — MVP starter

A hackathon MVP: choose a journey, upload a bus-board photo, read its destination with Gemini, verify it against a small route dataset, and show route options from that dataset.

## Stack
- Frontend: React + Vite
- Backend: FastAPI
- Vision: Gemini API (key kept on backend)
- Route verification: deterministic Python logic and JSON route data

## Important route-data note
`backend/app/data/routes.json` contains **illustrative demo routes only** so the app can be tested locally. They are not a claim of verified public bus timings or services. Before presenting a route result as real, replace them with locally verified route/stop sequences.

## 1. Requirements
- Python 3.11+
- Node.js 20+
- Gemini API key from https://aistudio.google.com/

## 2. Start backend (Arch Linux / Linux)
```bash
cd ~/Documents/vibathon02/busboard-reader-ai
source .venv/bin/activate
cd backend
# First setup only: pip install -r requirements.txt
# Copy .env.example to .env and add GEMINI_API_KEY there.
uvicorn app.main:app --reload --port 8000
```

If `GEMINI_API_KEY` is missing, the backend still starts and `/api/health` works, but image scanning returns a clear configuration error.

For a deterministic local demo that does not call Gemini, set
`BUSBOARD_DEMO_MODE=true` in `backend/.env` and restart the backend. Results
are visibly labelled **DEMO MODE — simulated recognition**; disable it again
for real recognition.

## Sarvam voice assistance

The result card requests an MP3 from FastAPI and plays it with the browser
Audio API. FastAPI sends the text to Sarvam's TTS API only; it is never sent to
Gemini. Configure the backend `.env` (do not commit this file):

```bash
SARVAM_API_KEY=your_key_here
SARVAM_TTS_MODEL=bulbul:v3
```

Malayalam uses `ml-IN`, speaker `shubh`, and `bulbul:v3`; English uses `en-IN`.
This requires outbound access to Sarvam and a valid Sarvam account/key. It is
not an offline eSpeak fallback.

## 3. Start frontend in another terminal
```bash
cd frontend
npm install
npm run dev
```
Open the local URL Vite prints (usually http://localhost:5173).

## 4. Start the Flutter Android app

The mobile client is a separate app in `mobile/`; it uses the same FastAPI
backend and never contains Gemini or Sarvam credentials.

```bash
cd ~/Documents/vibathon02/busboard-reader-ai/mobile
flutter pub get
# Android emulator: 10.0.2.2 reaches the computer running FastAPI.
flutter run --dart-define=API_BASE_URL=http://10.0.2.2:8000
```

For a physical Android device, start FastAPI on the computer's LAN interface
and replace the address below with the computer's reachable LAN IP (while
keeping the firewall appropriately restricted):

```bash
cd ~/Documents/vibathon02/busboard-reader-ai/backend
uvicorn app.main:app --host 0.0.0.0 --port 8000
cd ../mobile
flutter run --dart-define=API_BASE_URL=http://192.168.x.x:8000
```

The Android manifest permits cleartext HTTP only to make local development
against FastAPI practical. Use HTTPS and remove that development setting for a
production deployment.

## 5. Run checks
```bash
cd ~/Documents/vibathon02/busboard-reader-ai
source .venv/bin/activate
cd backend && pytest -q
cd ../frontend && npm test && npm run build
cd ../mobile && flutter analyze && flutter test
```

## API endpoints
- `GET /api/health`
- `GET /api/routes`
- `POST /api/verify` (`origin`, `destination`, `photo` as multipart form fields)

## MVP limits
- No live bus tracking or arrival predictions.
- A scanned board is not enough to prove a route; the result must match known route data.
- When text/route matching is uncertain, the app returns `unable_to_verify` instead of guessing.

## Private bus timetable lookup

The web planner also has a **Find private bus timings** action. It searches the bundled compact schedules imported from [amith-vp/Kerala-Private-Bus-Timing](https://github.com/amith-vp/Kerala-Private-Bus-Timing) and shows matching vehicles, listed stops, and any arrival/departure times present for the selected stop pair. The upstream README warns that the converted dataset may be incomplete or contain parsing errors; entries are not live departures. See `backend/app/data/PRIVATE_BUS_SCHEDULES_SOURCE.md` for source revision and refresh instructions.

After a timetable search, **Ask Gemini to check major stops** can suggest an additional corridor using the origin, destination, and any matching dataset routes. The same assistant runs after a board scan when the local route data has no connection. The app labels each suggested stop according to whether it appears on a matching timetable route, elsewhere in the imported data, or only in Gemini's suggestion. Gemini does not verify actual bus service or timings. This requires `GEMINI_API_KEY` in `backend/.env`; optionally set `GEMINI_ROUTE_MODEL` to choose a model independently from board recognition.
