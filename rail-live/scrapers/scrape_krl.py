"""Resumable KCI acquisition and service-day normalization."""
import argparse
import importlib
import json
import logging
import random
import re
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG = logging.getLogger(__name__)
BASE = "https://www.kci.id/api/krl"


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def rows(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ValueError("Expected API object with a data array; cache not written")
    if not all(isinstance(row, dict) for row in payload["data"]):
        raise ValueError("API data contains non-object rows")
    return payload["data"]


def active_stations(payload):
    return [s for s in rows(payload) if str(s.get("fg_enable", 0)) != "0"]


def windows():
    for start in range(4, 24, 3):
        yield f"{start:02}:00", f"{min(start + 3, 24):02}:00"


def minutes(value):
    match = re.fullmatch(r"(\d{1,2}):([0-5]\d)(?::([0-5]\d))?", str(value).strip())
    if not match or int(match[1]) > 47:
        raise ValueError(f"Invalid schedule time: {value!r}")
    total = int(match[1]) * 60 + int(match[2])
    # Service day starts at 04:00. Keep post-midnight stops after evening ones.
    return total + 1440 if total < 240 else total


def normalize(cache, stations):
    known = {str(s["sta_id"]) for s in stations}
    grouped = defaultdict(dict)
    for path in sorted(cache.glob("*_????_????.json")):
        station_id = path.stem.rsplit("_", 2)[0]
        if station_id not in known:
            LOG.warning("Unmapped station ID %s in %s", station_id, path.name)
        for row in rows(json.loads(path.read_text())):
            if not row.get("train_id"):
                LOG.warning("Missing train_id in %s", path.name)
                continue
            try:
                minute = minutes(row.get("time_est"))
            except ValueError as error:
                LOG.warning("%s (%s)", error, path.name)
                continue
            grouped[str(row["train_id"])].setdefault((station_id, minute), row)
    trips = []
    for train_id, stops in sorted(grouped.items()):
        ordered = sorted(stops.items(), key=lambda entry: (entry[0][1], entry[0][0]))
        meta = ordered[0][1]
        if len(ordered) < 2:
            LOG.warning("Train %s has fewer than 2 stops", train_id)
        trips.append({"train_id": train_id, "line": meta.get("route_name") or meta.get("ka_name", ""),
                      "color": meta.get("color", ""), "dest": meta.get("dest", ""),
                      # Extended HH:MM preserves next-day ordering (e.g. 24:12).
                      "stops": [{"station_id": sid, "time": f"{minute // 60:02}:{minute % 60:02}"}
                                for ((sid, minute), _) in ordered]})
    return trips


class KCIClient:
    def __init__(self, profile="chrome", helper=None, transport=None, sleeper=time.sleep):
        if helper is None:
            try:
                helper = importlib.import_module(f"{__package__}.cf_session" if __package__ else "cf_session")
            except ModuleNotFoundError as error:
                raise RuntimeError("Missing scrapers/cf_session.py. Supply the existing helper with get_session() and invalidate().") from error
        if transport is None:
            from curl_cffi import requests
            from curl_cffi.requests.impersonate import BrowserTypeLiteral
            from typing import get_args
            if profile not in get_args(BrowserTypeLiteral):
                raise RuntimeError(f"Installed curl_cffi does not support {profile!r}. Choose a supported profile with --impersonate chrome (or a supported versioned profile).")
            transport = requests.Session()
        self.helper, self.transport, self.profile = helper, transport, profile
        self.sleeper = sleeper
        self.last_request = None
        self.credentials = None

    def get(self, endpoint, params=None):
        for attempt in range(2):
            if self.credentials is None:
                session = self.helper.get_session()
                # Accepted helper contracts: (cookies, user_agent), dict, or session object.
                if isinstance(session, tuple) and len(session) == 2:
                    cookies, user_agent = session
                elif isinstance(session, dict):
                    cookies = session.get("cookies")
                    user_agent = session.get("user_agent") or session.get("ua")
                else:
                    cookies = getattr(session, "cookies", None)
                    user_agent = getattr(session, "headers", {}).get("User-Agent")
                if cookies is None or not user_agent:
                    raise RuntimeError("get_session() must supply cookies and user agent; see README helper contract.")
                self.credentials = cookies, user_agent
            if self.last_request is not None:
                self.sleeper(max(0, random.uniform(1, 2) - (time.monotonic() - self.last_request)))
            cookies, user_agent = self.credentials
            try:
                response = self.transport.get(BASE + endpoint, params=params, cookies=cookies,
                    headers={"User-Agent": user_agent, "Referer": "https://www.kci.id/",
                             "Origin": "https://www.kci.id", "Accept": "application/json"},
                    impersonate=self.profile, timeout=45)
            except Exception as error:
                if "impersonat" in str(error).lower():
                    raise RuntimeError(f"curl_cffi cannot use {self.profile!r}. Install a supporting version or explicitly select --impersonate chrome.") from error
                raise
            finally:
                self.last_request = time.monotonic()
            blocked = response.status_code in (403, 503) or "just a moment" in response.text.lower()
            if blocked:
                if attempt == 0:
                    self.helper.invalidate()
                    self.credentials = None
                    continue
                raise RuntimeError("KCI challenge persists after one session refresh; existing caches preserved.")
            response.raise_for_status()
            payload = response.json()
            rows(payload)
            return payload


def run(args):
    cache = args.data_dir / "raw" / "krl"
    stations_file = cache / "stations.json"
    client = None
    def get_client():
        nonlocal client
        if client is None:
            client = KCIClient(args.impersonate)
        return client
    if not stations_file.exists():
        if args.normalize_only:
            raise RuntimeError(f"Missing station cache: {stations_file}")
        write_json(stations_file, get_client().get("/stations"))
    stations = active_stations(json.loads(stations_file.read_text()))
    if not stations:
        raise RuntimeError("No active stations; refusing to replace trips.json")
    missing = []
    for station in stations:
        sid = str(station["sta_id"])
        if not re.fullmatch(r"[A-Za-z0-9_-]+", sid):
            raise ValueError(f"Unsafe station ID {sid!r}")
        for start, end in windows():
            path = cache / f"{sid}_{start.replace(':', '')}_{end.replace(':', '')}.json"
            if path.exists():
                rows(json.loads(path.read_text()))
                continue
            if args.normalize_only:
                missing.append(path.name)
                continue
            LOG.info("Fetching %s %s–%s", sid, start, end)
            write_json(path, get_client().get("/schedules", {"stationid": sid, "timefrom": start, "timeto": end}))
    if missing:
        raise RuntimeError(f"Incomplete cache: {len(missing)} windows missing; trips.json not replaced")
    trips = normalize(cache, stations)
    write_json(args.data_dir / "trips.json", trips)
    LOG.info("Wrote %d trips across %d active stations", len(trips), len(stations))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--normalize-only", action="store_true")
    parser.add_argument("--impersonate", default="chrome")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    try:
        run(args)
    except Exception as error:
        LOG.error("%s", error)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
