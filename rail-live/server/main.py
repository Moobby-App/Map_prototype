"""Local railway map server. Run from rail-live with uvicorn server.main:app."""

import asyncio
from collections import defaultdict
from datetime import datetime, timedelta
import os
import sys
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import jwt
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUTPUT = ROOT / "output"
sys.path.insert(0, str(ROOT.parent))
from scrapers import krl_client

# Prefer the project configuration; support launching from the workspace root.
ENV_FILE = ROOT / ".env" if (ROOT / ".env").exists() else ROOT.parent / ".env"
load_dotenv(ENV_FILE)
ORIGIN = "http://localhost:8000"
TOKEN_TTL = 1800
WIB = ZoneInfo("Asia/Jakarta")
LIVE_STATIONS = ("MRI", "JNG", "THB", "DP", "BKS")
LIVE_POLL_SECONDS = 120
LIVE_BACKOFF_MAX_SECONDS = 900

_live_state = {
    "updated_at": None,
    "stations_polled": [],
    "status": "starting",
    "raw_by_station": {},
    "errors": {},
}

app = FastAPI(title="Jakarta Railway Live Map")
app.mount("/static", StaticFiles(directory=ROOT / "web"), name="static")


@app.get("/")
def index():
    return FileResponse(ROOT / "web" / "index.html")


@app.get("/data/{filename}")
def railway_data(filename: str):
    # Expose only normalized outputs, never raw responses or credentials.
    if filename not in {"stations.json", "trips.json", "segments.geojson"}:
        raise HTTPException(404, "Unknown railway data file")
    path = DATA / filename
    if not path.is_file():
        raise HTTPException(404, "Railway data not generated yet")
    return FileResponse(path, media_type="application/json", headers={"Cache-Control": "no-store"})


@app.get("/output/{filename}")
def generated_railway_data(filename: str):
    # Serve only generated map assets; the input bundle and validation internals
    # are not exposed by this endpoint.
    if filename not in {"lines.json", "stations.json", "trips.json", "segments.geojson", "meta.json"}:
        raise HTTPException(404, "Unknown generated railway data file")
    path = OUTPUT / filename
    if not path.is_file():
        raise HTTPException(404, "Railway output not generated yet")
    return FileResponse(path, media_type="application/json", headers={"Cache-Control": "no-store"})


def _seconds_of_day(value):
    if not isinstance(value, str):
        return None
    pieces = value.strip().split(":")
    if len(pieces) not in (2, 3):
        return None
    try:
        hour, minute = int(pieces[0]), int(pieces[1])
        second = int(pieces[2]) if len(pieces) == 3 else 0
    except ValueError:
        return None
    if hour > 47 or minute > 59 or second > 59:
        return None
    return hour * 3600 + minute * 60 + second


def _clock_label(value):
    value = max(0, int(value))
    return f"{value // 3600:02d}:{(value // 60) % 60:02d}"


def _now_service_seconds(now=None):
    now = now or datetime.now(WIB)
    seconds = now.hour * 3600 + now.minute * 60 + now.second
    return seconds + (86400 if now.hour < 4 else 0)


def _nearest_day(value, near):
    if value is None:
        return None
    return min((value + offset * 86400 for offset in (-2, -1, 0, 1, 2)), key=lambda candidate: abs(candidate - near))


def _day_types(day_type):
    return {"daily", day_type}


def _static_trips():
    for path in (OUTPUT / "trips.json", DATA / "trips.json"):
        if path.is_file():
            try:
                value = __import__("json").loads(path.read_text(encoding="utf-8"))
                if isinstance(value, list):
                    return value
            except (OSError, ValueError):
                continue
    return []


def _summarize_live(raw_by_station, requested_day_type, now=None):
    now = now or datetime.now(WIB)
    clock = _now_service_seconds(now)
    window_start, window_end = clock - 600, clock + 3600
    eligible = _day_types(requested_day_type)
    expected = defaultdict(list)
    train_lookup = {}
    for trip in _static_trips():
        if trip.get("mode") != "krl" or trip.get("day_type") not in eligible:
            continue
        train_id = str(trip.get("train_id", ""))
        train_lookup.setdefault(train_id, []).append(trip)
        for stop in trip.get("stops", []):
            if isinstance(stop.get("t"), int):
                expected[str(stop.get("station_id", "")).upper()].append(
                    {"train_id": train_id, "t": stop["t"], "trip": trip,
                     "station_id": str(stop.get("station_id", "")).upper()}
                )

    delays, missing, extra = [], [], []
    for station in LIVE_STATIONS:
        payload = raw_by_station.get(station)
        if not isinstance(payload, dict):
            continue
        rows = payload.get("data", [])
        if not isinstance(rows, list):
            continue
        seen = set()
        for row in rows:
            if not isinstance(row, dict) or row.get("train_id") is None:
                continue
            train_id = str(row["train_id"])
            seen.add(train_id)
            candidates = [item for item in expected.get(station, []) if item["train_id"] == train_id]
            api_time = _seconds_of_day(row.get("time_est"))
            if not candidates:
                extra.append({"station_id": station, "train_id": train_id,
                              "time_est": row.get("time_est"), "route": row.get("route_name")})
                continue
            if api_time is not None:
                scored = []
                for item in candidates:
                    scheduled = _nearest_day(item["t"], api_time)
                    api_aligned = _nearest_day(api_time, scheduled)
                    scored.append((abs(api_aligned - scheduled), scheduled, api_aligned, item))
                _, scheduled, actual, item = min(scored, key=lambda candidate: candidate[0])
                delta = actual - scheduled
                if delta:
                    delays.append({"station_id": station, "train_id": train_id,
                        "field": "time_est", "scheduled_time": _clock_label(scheduled),
                        "kci_time": row.get("time_est"), "delay_seconds": delta,
                        "route": item["trip"].get("route")})

            # KCI also reports the terminal arrival. Compare only when the static
            # trip has a known destination stop; don't infer a destination from text.
            api_terminal = _seconds_of_day(row.get("dest_time"))
            if api_terminal is not None:
                for trip in train_lookup.get(train_id, []):
                    destination_id = str(trip.get("destination_station_id", "")).upper()
                    terminal = next((stop.get("t") for stop in trip.get("stops", [])
                                     if str(stop.get("station_id", "")).upper() == destination_id
                                     and isinstance(stop.get("t"), int)), None)
                    if terminal is None:
                        continue
                    scheduled = _nearest_day(terminal, api_terminal)
                    actual = _nearest_day(api_terminal, scheduled)
                    delta = actual - scheduled
                    if delta:
                        delays.append({"station_id": station, "train_id": train_id,
                            "field": "dest_time", "scheduled_time": _clock_label(scheduled),
                            "kci_time": row.get("dest_time"), "delay_seconds": delta,
                            "route": trip.get("route")})
                    break

        for item in expected.get(station, []):
            scheduled = item["t"]
            if window_start <= scheduled <= window_end and item["train_id"] not in seen:
                missing.append({"station_id": station, "train_id": item["train_id"],
                                "scheduled_time": _clock_label(scheduled),
                                "route": item["trip"].get("route")})
    return {"delays": delays, "missing": missing, "extra": extra}


async def _poll_live_once():
    now = datetime.now(WIB)
    service_seconds = _now_service_seconds(now)
    start_time = _clock_label(service_seconds - 600)
    end_time = _clock_label(service_seconds + 3600)
    polled, errors = [], {}
    updated_rows = dict(_live_state["raw_by_station"])
    for station in LIVE_STATIONS:
        try:
            payload = await asyncio.to_thread(krl_client.fetch_schedules, station, start_time, end_time)
            updated_rows[station] = payload
            polled.append(station)
        except Exception as error:
            errors[station] = f"{type(error).__name__}: {error}"
    _live_state.update({
        "updated_at": datetime.now(WIB).isoformat(timespec="seconds"),
        "stations_polled": polled,
        "status": "unreachable" if errors else "available",
        "raw_by_station": updated_rows,
        "errors": errors,
    })
    return not errors


async def _live_poll_loop():
    backoff = 15
    while True:
        success = await _poll_live_once()
        if success:
            backoff = 15
            await asyncio.sleep(LIVE_POLL_SECONDS)
        else:
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, LIVE_BACKOFF_MAX_SECONDS)


@app.on_event("startup")
async def start_live_poller():
    app.state.live_poller = asyncio.create_task(_live_poll_loop())


@app.on_event("shutdown")
async def stop_live_poller():
    task = getattr(app.state, "live_poller", None)
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@app.get("/api/live")
def live_status(day_type: str | None = None):
    if day_type is None:
        today = datetime.now(WIB)
        day_type = "sunday" if today.weekday() == 6 else "weekday"
    if day_type not in {"weekday", "sunday"}:
        raise HTTPException(400, "day_type must be weekday or sunday")
    summary = _summarize_live(_live_state["raw_by_station"], day_type)
    return {
        "updated_at": _live_state["updated_at"],
        "stations_polled": list(_live_state["stations_polled"]),
        "status": _live_state["status"],
        **summary,
    }


@app.get("/token")
def token(response: Response):
    response.headers["Cache-Control"] = "no-store"
    team_id = os.getenv("TEAM_ID", "").strip()
    key_id = os.getenv("KEY_ID", "").strip()
    key_path = os.getenv("MAPKIT_PRIVATE_KEY_PATH", "").strip()
    if not all((team_id, key_id, key_path)):
        raise HTTPException(
            503, "Map credentials missing. Configure TEAM_ID, KEY_ID and "
            "MAPKIT_PRIVATE_KEY_PATH in .env or rail-live/.env.",
            headers={"Cache-Control": "no-store"},
        )
    path = Path(key_path).expanduser()
    if not path.is_absolute():
        path = ROOT / path
    now = int(time.time())
    try:
        signed = jwt.encode(
            {"iss": team_id, "iat": now, "exp": now + TOKEN_TTL,
             "origin": ORIGIN, "scope": "mapkit_js"},
            path.read_text(), algorithm="ES256", headers={"kid": key_id},
        )
    except (OSError, ValueError, TypeError, jwt.PyJWTError):
        raise HTTPException(
            503, "Map signing key could not be loaded. Check the configured .p8 key.",
            headers={"Cache-Control": "no-store"},
        ) from None
    return {"token": signed, "expires_at": now + TOKEN_TTL}
