"""Poll KCI station schedules and compare them with the packaged timetable.

Run from the project root: ``python3 -m scrapers.probe_live``.
The existing KCI client and Cloudflare session implementation are used unchanged.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta
import json
from pathlib import Path
import signal
import time
from zoneinfo import ZoneInfo

from . import krl_client

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "rail-live" / "output" / "probe"
TRIPS_PATH = ROOT / "rail-live" / "output" / "trips.json"
META_PATH = ROOT / "rail-live" / "output" / "meta.json"
TZ = ZoneInfo("Asia/Jakarta")
DEFAULT_STATIONS = ("MRI", "JNG", "THB", "DP", "BKS")
_stop = False


def _handle_stop(_signum, _frame):
    global _stop
    _stop = True


def _clock(value: datetime) -> str:
    """KCI accepts HH:MM; use 24:xx after midnight rollover where needed."""
    minutes = value.hour * 60 + value.minute
    if value.date() > datetime.now(TZ).date():
        minutes += 1440
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _day_types(now: datetime) -> set[str]:
    # Saturday was not captured. The live probe compares Sunday only on Sunday;
    # weekday captures apply Monday-Friday.
    if now.weekday() == 6:
        return {"daily", "sunday"}
    if now.weekday() == 5:
        return set()
    return {"daily", "weekday"}


def _load_static(today: datetime):
    trips = json.loads(TRIPS_PATH.read_text(encoding="utf-8"))
    eligible_days = _day_types(today)
    index = defaultdict(list)
    for trip in trips:
        if trip.get("mode") != "krl" or trip.get("day_type") not in eligible_days:
            continue
        train_id = str(trip.get("train_id", ""))
        for stop in trip.get("stops", []):
            index[(train_id, str(stop.get("station_id", "")).upper())].append(
                {"t": stop.get("t"), "route": trip.get("route"), "name": trip.get("name"),
                 "day_type": trip.get("day_type")}
            )
    return index, len(trips), eligible_days


def _time_seconds(value):
    if not isinstance(value, str):
        return None
    pieces = value.strip().split(":")
    if len(pieces) not in (2, 3):
        return None
    try:
        nums = [int(piece) for piece in pieces]
    except ValueError:
        return None
    if len(nums) == 2:
        return nums[0] * 3600 + nums[1] * 60
    return nums[0] * 3600 + nums[1] * 60 + nums[2]


def _static_compare(records, static_index):
    compared = []
    for record in records:
        if not isinstance(record, dict):
            continue
        train_id = str(record.get("train_id", ""))
        station = str(record.get("_probe_station", "")).upper()
        api_sec = _time_seconds(record.get("time_est"))
        options = static_index.get((train_id, station), [])
        if api_sec is None or not options:
            compared.append({"train_id": train_id, "station": station,
                             "time_est": record.get("time_est"), "matched": False,
                             "reason": "unparseable time" if api_sec is None else "no train/station match"})
            continue
        scored = []
        for option in options:
            if not isinstance(option.get("t"), int):
                continue
            # Compare closest day occurrence so 00:xx can be aligned to 24:xx.
            choices = [option["t"] + day * 86400 for day in (-1, 0, 1)]
            scheduled = min(choices, key=lambda t: abs(t - api_sec))
            scored.append((abs(api_sec - scheduled), scheduled, option))
        if not scored:
            compared.append({"train_id": train_id, "station": station, "time_est": record.get("time_est"),
                             "matched": False, "reason": "static stop has no integer seconds"})
            continue
        delta, scheduled, option = min(scored, key=lambda row: row[0])
        compared.append({"train_id": train_id, "station": station, "time_est": record.get("time_est"),
                         "static_seconds": scheduled, "delta_seconds": api_sec - scheduled,
                         "matched": True, "within_30_seconds": delta <= 30,
                         "route": option["route"], "static_day_type": option["day_type"]})
    return compared


def _summarize(records, errors, static_index):
    rows = []
    for item in records:
        for row in item.get("response", {}).get("data", []):
            if isinstance(row, dict):
                rows.append({**row, "_probe_station": item["station"]})
    compared = _static_compare(rows, static_index)
    matched = [row for row in compared if row.get("matched")]
    unmatched = [row for row in compared if not row.get("matched")]
    deltas = [row["delta_seconds"] for row in matched]
    delta_counts = Counter(deltas)
    report = {
        "snapshot_at": records[0]["captured_at"] if records else None,
        "requests": len(records) + len(errors),
        "successful_requests": len(records),
        "failed_requests": len(errors),
        "rows": len(rows),
        "matched_static_rows": len(matched),
        "unmatched_static_rows": len(unmatched),
        "exact_static_matches": sum(value == 0 for value in deltas),
        "within_30_seconds": sum(abs(value) <= 30 for value in deltas),
        "delta_seconds": {str(k): v for k, v in sorted(delta_counts.items())},
        "errors": errors,
        "unmatched_samples": unmatched[:100],
        "comparison_samples": compared[:200],
    }
    return report


def _make_report(cycles, static_index, metadata):
    station_cycle = defaultdict(list)
    for cycle in cycles:
        for item in cycle:
            station_cycle[item["station"]].append(item)

    consecutive = {}
    for station, samples in station_cycle.items():
        diffs = {"pairs": 0, "time_est_changed": 0, "dest_time_changed": 0,
                 "appeared": 0, "vanished": 0, "details": []}
        for before, after in zip(samples, samples[1:]):
            old_rows = (before.get("response") or {}).get("data", [])
            new_rows = (after.get("response") or {}).get("data", [])
            def by_id(rows):
                result = {}
                for row in rows if isinstance(rows, list) else []:
                    if isinstance(row, dict) and row.get("train_id") is not None:
                        result[str(row["train_id"])] = row
                return result
            old, new = by_id(old_rows), by_id(new_rows)
            appeared = sorted(new.keys() - old.keys())
            vanished = sorted(old.keys() - new.keys())
            shared = sorted(old.keys() & new.keys())
            time_changes = [tid for tid in shared if old[tid].get("time_est") != new[tid].get("time_est")]
            dest_changes = [tid for tid in shared if old[tid].get("dest_time") != new[tid].get("dest_time")]
            diffs["pairs"] += 1
            diffs["appeared"] += len(appeared)
            diffs["vanished"] += len(vanished)
            diffs["time_est_changed"] += len(time_changes)
            diffs["dest_time_changed"] += len(dest_changes)
            if appeared or vanished or time_changes or dest_changes:
                diffs["details"].append({"from": before["captured_at"], "to": after["captured_at"],
                    "appeared": appeared, "vanished": vanished, "time_est_changed": time_changes,
                    "dest_time_changed": dest_changes})
        consecutive[station] = diffs

    static_compare = {}
    all_rows = []
    for cycle in cycles:
        for item in cycle:
            if item.get("response"):
                all_rows.append(item)
    for station in station_cycle:
        # Snapshot by station, using the earliest captured cycle, makes the
        # static comparison easy to audit and avoids weighting repeated trains.
        first = station_cycle[station][0]
        rows = [{**row, "_probe_station": station}
                for row in (first.get("response") or {}).get("data", []) if isinstance(row, dict)]
        compared = _static_compare(rows, static_index)
        matches = [x for x in compared if x.get("matched")]
        static_compare[station] = {
            "snapshot_at": first["captured_at"], "feed_rows": len(rows),
            "matched_to_static_train_and_station": len(matches),
            "unmatched": len(compared) - len(matches),
            "exact_time_matches": sum(x.get("delta_seconds") == 0 for x in matches),
            "within_30_seconds": sum(abs(x.get("delta_seconds", 10**9)) <= 30 for x in matches),
            "delta_seconds": [x["delta_seconds"] for x in matches],
            "details": compared,
        }
    return {"created_at": datetime.now(TZ).isoformat(), "metadata": metadata,
            "consecutive_snapshot_diffs": consecutive, "static_timetable_comparison": static_compare,
            "interpretation": "See diff counts; no changes observed is not evidence of realtime delay adjustment."}


def _write_report(report):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["# KCI live probe report", "", f"Generated: {report['created_at']}", "",
             "## Consecutive snapshot differences", "",
             "| Station | Pairs | time_est changes | dest_time changes | Appeared | Vanished |",
             "|---|---:|---:|---:|---:|---:|"]
    for station, diff in report["consecutive_snapshot_diffs"].items():
        lines.append(f"| {station} | {diff['pairs']} | {diff['time_est_changed']} | {diff['dest_time_changed']} | {diff['appeared']} | {diff['vanished']} |")
    lines += ["", "## First snapshot vs static Sunday timetable", "",
              "| Station | Feed rows | Train/station matches | Exact times | Within 30 sec | Delta seconds (feed − static) |",
              "|---|---:|---:|---:|---:|---|"]
    for station, info in report["static_timetable_comparison"].items():
        lines.append(f"| {station} | {info['feed_rows']} | {info['matched_to_static_train_and_station']} | {info['exact_time_matches']} | {info['within_30_seconds']} | {info['delta_seconds'][:50]} |")
    lines += ["", "The row-level match requires the same train ID and station code; time deltas are in seconds.",
              "A feed with no consecutive time changes is not labeled real-time.", ""]
    (OUT / "report.md").write_text("\n".join(lines), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration-minutes", type=float, default=60)
    parser.add_argument("--interval-minutes", type=float, default=2)
    parser.add_argument("--stations", nargs="+", default=list(DEFAULT_STATIONS))
    args = parser.parse_args()
    if args.duration_minutes < 0 or args.interval_minutes <= 0:
        parser.error("duration must be nonnegative and interval must be positive")

    signal.signal(signal.SIGINT, _handle_stop)
    signal.signal(signal.SIGTERM, _handle_stop)
    OUT.mkdir(parents=True, exist_ok=True)
    snapshots_path = OUT / "snapshots.jsonl"
    metadata = json.loads(META_PATH.read_text(encoding="utf-8"))
    started = datetime.now(TZ)
    static_index, trip_count, eligible_days = _load_static(started)
    cadence = args.interval_minutes * 60
    duration = args.duration_minutes * 60
    cycles = []
    run_started_mono = time.monotonic()
    print(f"KCI probe: {len(args.stations)} stations, every {args.interval_minutes:g} min, "
          f"duration {args.duration_minutes:g} min; day types={sorted(eligible_days)}. "
          f"Appending raw rows to {snapshots_path}", flush=True)

    cycle_no = 0
    while not _stop:
        cycle_started_mono = time.monotonic()
        captured = datetime.now(TZ)
        window_start, window_end = captured - timedelta(minutes=10), captured + timedelta(minutes=60)
        start_text, end_text = _clock(window_start), _clock(window_end)
        cycle = []
        for station in args.stations:
            entry = {"captured_at": captured.isoformat(), "station": station,
                     "window_wib": {"from": start_text, "to": end_text}}
            try:
                payload = krl_client.fetch_schedules(station, start_text, end_text)
                entry["http_status"] = krl_client.last_status
                entry["response"] = payload
                print(f"[{cycle_no + 1}] {station}: {len(payload.get('data', []))} rows "
                      f"(HTTP {krl_client.last_status})", flush=True)
            except Exception as error:  # Preserve each failure and continue other stations.
                entry["error"] = {"type": type(error).__name__, "message": str(error)}
                print(f"[{cycle_no + 1}] {station}: ERROR {type(error).__name__}: {error}", flush=True)
            with snapshots_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n")
                stream.flush()
            cycle.append(entry)
        cycles.append(cycle)
        cycle_no += 1
        report = _make_report(cycles, static_index, {
            "started_at": started.isoformat(), "station_ids": args.stations,
            "duration_minutes_requested": args.duration_minutes,
            "interval_minutes": args.interval_minutes, "snapshot_cycles": cycle_no,
            "trip_count_all_day_types": trip_count, "eligible_day_types": sorted(eligible_days),
            "static_path": str(TRIPS_PATH.relative_to(ROOT)), "timezone": "Asia/Jakarta",
            "kci_source": "scrapers.krl_client.fetch_schedules (unchanged)",
            "meta_built_at": metadata.get("built_at"),
        })
        _write_report(report)
        elapsed = time.monotonic() - cycle_started_mono
        print(f"Cycle {cycle_no} archived; report updated. Next in {max(0, cadence-elapsed):.0f}s.", flush=True)
        if _stop or (time.monotonic() - run_started_mono >= duration):
            break
        remaining = max(0, cadence - elapsed)
        while remaining > 0 and not _stop:
            nap = min(1.0, remaining)
            time.sleep(nap)
            remaining -= nap

    completed = datetime.now(TZ)
    report["metadata"]["completed_at"] = completed.isoformat()
    report["metadata"]["elapsed_minutes"] = round((completed - started).total_seconds() / 60, 2)
    report["metadata"]["snapshot_cycles"] = cycle_no
    report["metadata"]["stopped_early"] = _stop
    _write_report(report)
    print(f"Probe finished: {cycle_no} cycles in {report['metadata']['elapsed_minutes']} min. "
          f"Reports: {OUT / 'report.md'} and {OUT / 'report.json'}", flush=True)


if __name__ == "__main__":
    main()
