"""Crawl KCI from the project root after smoke_krl succeeds."""
import argparse
from collections import defaultdict
from . import krl_client as client
from .common import DATA, ROOT, OUT_OF_SCOPE_STATION_IDS, active, name_key, read_json, seconds, write_json, station_overrides


def normalize(cache, station_rows):
    overrides = station_overrides()
    stations = active(station_rows)
    codes = {str(s["sta_id"]).upper(): str(s["sta_id"]) for s in stations}
    names = defaultdict(set)
    for s in stations: names[name_key(s["sta_name"])].add(str(s["sta_id"]))
    grouped = defaultdict(list)
    unresolved, unknown = set(), set()
    for path in sorted(cache.glob("*.json")):
        station, start, _ = path.stem.rsplit("_", 2)
        start_seconds = int(start[:2])*3600 + int(start[2:])*60
        if station not in codes: unknown.add(station)
        for row in read_json(path)["data"]:
            train = str(row["train_id"])
            t = seconds(row["time_est"])
            if t < 4*3600 and start_seconds >= 12*3600: t += 86400
            grouped[train].append((station, t, row, False))
            dest = str(row["dest"])
            resolved = codes.get(dest.upper())
            if not resolved and len(names[name_key(dest)]) == 1:
                resolved = next(iter(names[name_key(dest)]))
            if resolved:
                terminal = seconds(row["dest_time"])
                while terminal < t: terminal += 86400
                grouped[train].append((resolved, terminal, row, True))
            else:
                unresolved.add(dest)
    trips, excluded, dropped, conflicts = [], [], 0, []
    for train, entries in sorted(grouped.items()):
        # Actual station observations take precedence over inferred terminal times.
        by_station = {}
        for sid, t, row, inferred in sorted(entries, key=lambda e: (e[3], e[1])):
            if sid in by_station:
                old = by_station[sid]
                if not inferred and not old[2] and old[0] != t:
                    conflicts.append(f"{train}/{sid}: {old[0]} vs {t}")
                continue
            by_station[sid] = (t, row, inferred)
        ordered = sorted(by_station.items(), key=lambda e: e[1][0])
        if len(ordered) < 2: dropped += 1; continue
        meta = ordered[0][1][1]
        trip = {"train_id": train, "name": meta["ka_name"], "route": meta["route_name"],
            "color": meta["color"], "dest": meta["dest"],
            "stops": [{"station_id": sid, "t": values[0]} for sid, values in ordered]}
        out_of_scope = sorted({stop["station_id"] for stop in trip["stops"]} & OUT_OF_SCOPE_STATION_IDS)
        if out_of_scope:
            excluded.append({"trip": trip, "reason": "outside Jabodetabek"})
        else:
            trips.append(trip)
    if conflicts:
        raise RuntimeError("Conflicting station times (ambiguous train reuse); normalization stopped: " + "; ".join(conflicts[:10]))
    return trips, {"trips": len(trips), "excluded": len(excluded), "excluded_trips": excluded,
                   "station_overrides": sorted(overrides), "dropped": dropped,
                   "unresolved_dest": sorted(unresolved), "unknown_station_ids": sorted(unknown)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--normalize-only", action="store_true")
    parser.add_argument("--skip-station", action="append", default=[], metavar="ID",
                        help="Temporarily skip missing windows for this station; outputs remain incomplete.")
    args = parser.parse_args()
    if __import__('pathlib').Path.cwd().resolve() != ROOT:
        raise RuntimeError(f"Run Python from {ROOT}")
    smoke = read_json(DATA / "raw/krl/smoke_report.json")
    window = smoke["window_minutes"]
    station_path = DATA / "raw/krl/stations.json"
    if not station_path.exists(): write_json(station_path, client.fetch_stations())
    station_rows = read_json(station_path)["data"]
    stations = active(station_rows)
    schedule_dir = DATA / "raw/krl/schedules"
    schedule_dir.mkdir(parents=True, exist_ok=True)
    windows = [(m, min(m+window, 1440)) for m in range(240, 1440, window)]
    total = len(stations)*len(windows)
    failures, missing = 0, []
    print(f"{len(stations)} active stations, {window}-minute windows, {total} requests. Chrome may open.", flush=True)
    for index, (s, bounds) in enumerate(((s, bounds) for s in stations for bounds in windows), 1):
        sid = str(s["sta_id"])
        if not __import__('re').fullmatch(r"[A-Za-z0-9_-]+", sid): raise ValueError("Unsafe station ID")
        start, end = [f"{m//60:02}:{m%60:02}" for m in bounds]
        path = schedule_dir / f"{sid}_{start.replace(':','')}_{end.replace(':','')}.json"
        if path.exists():
            payload = read_json(path)
            if not isinstance(payload.get("data"), list): raise RuntimeError(f"Invalid cache {path.name}")
            failures = 0
            print(f"[{index}/{total}] {sid} {start}-{end} -> {len(payload['data'])} rows (cached)", flush=True)
            continue
        if args.normalize_only: missing.append(path.name); continue
        if sid.upper() in {value.upper() for value in args.skip_station}:
            missing.append(path.name)
            print(f"[{index}/{total}] {sid} {start}-{end} -> skipped explicitly (NOT empty data)", flush=True)
            continue
        try:
            payload = client.fetch_schedules(sid, start, end)
            # Validate time schema before preserving a response for normalization.
            for row in payload["data"]:
                for field in ("train_id", "ka_name", "route_name", "color", "dest", "time_est", "dest_time"):
                    if field not in row: raise RuntimeError(f"Ambiguous schema: missing {field}")
                seconds(row["time_est"]); seconds(row["dest_time"])
            write_json(path, payload)
            failures = 0
            print(f"[{index}/{total}] {sid} {start}-{end} -> {len(payload['data'])} rows", flush=True)
        except client.CloudflareError:
            raise
        except client.APIError as error:
            failures += 1; missing.append(path.name)
            print(f"[{index}/{total}] {sid}: {error} ({failures}/5 consecutive failures)", flush=True)
            if failures >= 5: raise RuntimeError("5 consecutive failures; stop. Rerun to resume.")
    if missing: raise RuntimeError(f"{len(missing)} windows missing; cache preserved. Rerun before normalization.")
    trips, report = normalize(schedule_dir, station_rows)
    excluded = report.pop("excluded_trips")
    write_json(DATA / "trips.json", trips)
    write_json(DATA / "trips_excluded.json", excluded)
    write_json(DATA / "raw/krl/normalization_report.json", report)
    print(report, flush=True)

if __name__ == "__main__":
    try: main()
    except Exception as error:
        print("STOP:", str(error), flush=True)
        raise SystemExit(1)
