#!/usr/bin/env python3
"""Normalize and validate the bundled railway network JSON."""

from __future__ import annotations

import argparse
import heapq
import json
import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
INPUT_JSON = ROOT / "pipeline" / "source" / "open-krl.json"
OUTPUT_DIR = ROOT / "data" / "runtime"
DAY_TYPES = {0: "daily", 1: "weekday", 2: "sunday"}


def decode_polyline(encoded: str, precision: int = 5) -> list[list[float]]:
    """Decode Google polyline with the source's longitude-first axis order."""
    points: list[list[float]] = []
    index = 0
    lon = lat = 0
    scale = 10**precision
    while index < len(encoded):
        deltas = []
        for _ in range(2):
            result = shift = 0
            while True:
                if index >= len(encoded):
                    raise ValueError("truncated encoded polyline")
                value = ord(encoded[index]) - 63
                index += 1
                result |= (value & 0x1F) << shift
                shift += 5
                if value < 0x20:
                    break
            deltas.append(~(result >> 1) if result & 1 else result >> 1)
        lon += deltas[0]
        lat += deltas[1]
        points.append([lon / scale, lat / scale])
    return points


def distance_m(a: list[float] | tuple[float, float], b: list[float] | tuple[float, float]) -> float:
    lon1, lat1 = map(math.radians, a)
    lon2, lat2 = map(math.radians, b)
    dlon, dlat = lon2 - lon1, lat2 - lat1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6_371_000 * 2 * math.asin(math.sqrt(h))


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")


def shortest_path(
    graph: dict[str, list[tuple[str, float]]], start: str, end: str
) -> tuple[float, list[str]] | None:
    queue: list[tuple[float, str]] = [(0.0, start)]
    distances = {start: 0.0}
    previous: dict[str, str] = {}
    while queue:
        cost, node = heapq.heappop(queue)
        if cost != distances.get(node):
            continue
        if node == end:
            route = [end]
            while route[-1] != start:
                route.append(previous[route[-1]])
            route.reverse()
            return cost, route
        for neighbor, edge_cost in graph.get(node, []):
            candidate = cost + edge_cost
            if candidate < distances.get(neighbor, math.inf):
                distances[neighbor] = candidate
                previous[neighbor] = node
                heapq.heappush(queue, (candidate, neighbor))
    return None


def build(input_path: Path = INPUT_JSON, output_dir: Path = OUTPUT_DIR) -> dict[str, Any]:
    source = json.loads(input_path.read_text(encoding="utf-8"))
    lines_raw = source["lines"]
    stations_raw = source["stations"]
    tracks_raw = source["tracks"]
    patterns_raw = source["patterns"]
    trains_raw = source["trains"]

    lines = [
        {"id": index, "name": row[0], "label": row[1], "color": row[2], "mode": row[3], "code": row[4]}
        for index, row in enumerate(lines_raw)
    ]
    stations_by_id = {
        row[0]: {
            "id": row[0], "name": row[1], "lng": row[2] / 1e5, "lat": row[3] / 1e5,
            "line_ids": row[4],
        }
        for row in stations_raw
    }
    stations_by_index = stations_raw
    for station in stations_by_id.values():
        station["modes"] = sorted({lines[index]["mode"] for index in station["line_ids"] if index < len(lines)})

    # Some track records describe shared physical rail under one service tag
    # (for example an intercity train using a KRL station pair). Preserve the
    # exact edge for any line whose own timetable pattern directly uses it.
    direct_pattern_pairs: dict[int, set[frozenset[str]]] = defaultdict(set)
    for train in trains_raw:
        line_index, pattern_index = train[1], train[4]
        pattern = patterns_raw[pattern_index]
        for a_index, b_index in zip(pattern, pattern[1:]):
            direct_pattern_pairs[line_index].add(frozenset((stations_by_index[a_index][0], stations_by_index[b_index][0])))

    valid_tracks: list[dict[str, Any]] = []
    skipped_tracks: list[dict[str, str]] = []
    endpoint_errors: list[tuple[float, str, str]] = []
    graph_by_line: dict[int, dict[str, list[tuple[str, float]]]] = {line["id"]: defaultdict(list) for line in lines}
    direct_by_line: dict[int, set[frozenset[str]]] = {line["id"]: set() for line in lines}

    for track in tracks_raw:
        from_id, to_id, encoded = track[:3]
        if from_id not in stations_by_id or to_id not in stations_by_id:
            skipped_tracks.append({"from": from_id, "to": to_id, "reason": "missing station reference"})
            continue
        points = decode_polyline(encoded, 5)
        if len(points) < 2:
            raise ValueError(f"track {from_id}-{to_id} decoded to fewer than two points")
        a, b = stations_by_id[from_id], stations_by_id[to_id]
        forward = (distance_m(points[0], [a["lng"], a["lat"]]), distance_m(points[-1], [b["lng"], b["lat"]]))
        reverse = (distance_m(points[-1], [a["lng"], a["lat"]]), distance_m(points[0], [b["lng"], b["lat"]]))
        if max(reverse) < max(forward):
            points.reverse()
            errors = reverse
        else:
            errors = forward
        max_error = max(errors)
        endpoint_errors.append((max_error, from_id, to_id))
        if max_error > 150:
            raise ValueError(f"track {from_id}-{to_id} endpoint error {max_error:.1f}m exceeds 150m")

        explicit_mode = track[3] if len(track) > 3 else None
        shared_lines = sorted(set(a["line_ids"]) & set(b["line_ids"]))
        eligible_lines = {
            index for index in shared_lines
            if index < len(lines) and (explicit_mode is None or lines[index]["mode"] == explicit_mode)
        }
        edge_key = frozenset((from_id, to_id))
        eligible_lines.update(
            index for index, edges in direct_pattern_pairs.items()
            if edge_key in edges and index < len(lines)
        )
        eligible_lines = sorted(eligible_lines)
        modes = sorted({lines[index]["mode"] for index in eligible_lines})
        if not modes and explicit_mode:
            modes = [explicit_mode]
        mode_line = next((line for line in lines if line["mode"] in modes), None)
        color = lines[eligible_lines[0]]["color"] if eligible_lines else mode_line["color"] if mode_line else "#617a7d"
        valid_tracks.append({
            "type": "Feature",
            "properties": {
                "from": from_id, "to": to_id, "line_ids": eligible_lines,
                "modes": modes, "color": color, "approximate": False,
            },
            "geometry": {"type": "LineString", "coordinates": points},
        })
        edge_length = sum(distance_m(x, y) for x, y in zip(points, points[1:]))
        for line_index in eligible_lines:
            graph_by_line[line_index][from_id].append((to_id, edge_length))
            graph_by_line[line_index][to_id].append((from_id, edge_length))
            direct_by_line[line_index].add(frozenset((from_id, to_id)))

    trips: list[dict[str, Any]] = []
    line_pattern_usage: set[tuple[int, int]] = set()
    service_counts: Counter[str] = Counter()
    day_counts: Counter[str] = Counter()
    per_day_service: dict[str, Counter[str]] = {"weekday": Counter(), "sunday": Counter()}
    time_over_24h = 0
    max_time_min = 0
    for train in trains_raw:
        train_id, line_index, destination_index, day_code, pattern_index, time_values, route = train[:7]
        if day_code not in DAY_TYPES:
            raise ValueError(f"train {train_id} has unknown day type code {day_code}")
        if not 0 <= line_index < len(lines) or not 0 <= pattern_index < len(patterns_raw):
            raise ValueError(f"train {train_id} has an invalid line or pattern reference")
        pattern = patterns_raw[pattern_index]
        if len(pattern) != len(time_values):
            raise ValueError(f"train {train_id} has {len(time_values)} times for {len(pattern)} stops")
        mode = lines[line_index]["mode"]
        day_type = DAY_TYPES[day_code]
        line_pattern_usage.add((line_index, pattern_index))
        day_counts[day_type] += 1
        service_counts[mode] += 1
        if day_type in ("daily", "weekday"):
            per_day_service["weekday"][mode] += 1
        if day_type in ("daily", "sunday"):
            per_day_service["sunday"][mode] += 1

        elapsed_units = 0
        stops = []
        for index, value in enumerate(time_values):
            if index == 0:
                elapsed_units = value
            else:
                elapsed_units += value
            elapsed_seconds = elapsed_units * 30
            max_time_min = max(max_time_min, elapsed_seconds / 60)
            if elapsed_seconds >= 86400:
                time_over_24h += 1
            station_index = pattern[index]
            if not 0 <= station_index < len(stations_by_index):
                raise ValueError(f"train {train_id} pattern references station index {station_index}")
            station_id = stations_by_index[station_index][0]
            stops.append({"station_id": station_id, "t": elapsed_seconds})

        destination = stations_by_index[destination_index][0] if 0 <= destination_index < len(stations_by_index) else None
        item = {
            "train_id": str(train_id), "line_id": line_index, "mode": mode,
            "day_type": day_type, "pattern_id": pattern_index,
            "name": lines[line_index]["label"], "route": str(route),
            "color": lines[line_index]["color"],
            "dest": stations_by_id[destination]["name"] if destination in stations_by_id else str(destination_index),
            "destination_station_id": destination,
            "via": train[7] if len(train) > 7 and isinstance(train[7], str) else "",
            "terminus": train[8] if len(train) > 8 and isinstance(train[8], str) else "",
            "stops": stops,
        }
        if len(train) > 9 and isinstance(train[9], dict):
            item["service_metadata"] = train[9]
        trips.append(item)

    pair_routes: list[dict[str, Any]] = []
    approximate_features: list[dict[str, Any]] = []
    for line_index, pattern_index in sorted(line_pattern_usage):
        line = lines[line_index]
        pattern = patterns_raw[pattern_index]
        for offset, (a_index, b_index) in enumerate(zip(pattern, pattern[1:])):
            a_id, b_id = stations_by_index[a_index][0], stations_by_index[b_index][0]
            if frozenset((a_id, b_id)) in direct_by_line[line_index]:
                continue
            straight = distance_m(
                [stations_by_id[a_id]["lng"], stations_by_id[a_id]["lat"]],
                [stations_by_id[b_id]["lng"], stations_by_id[b_id]["lat"]],
            )
            path = shortest_path(graph_by_line[line_index], a_id, b_id)
            if path and path[0] <= 2 * straight:
                pair_routes.append({
                    "line_id": line_index, "line": line["name"], "mode": line["mode"], "pattern_id": pattern_index,
                    "stop_pair_index": offset, "from": a_id, "to": b_id,
                    "strategy": "track_path", "path": path[1],
                    "path_m": round(path[0], 1), "straight_m": round(straight, 1),
                })
            else:
                reason = "no same-line path" if not path else "same-line path exceeds 2x straight distance"
                pair_routes.append({
                    "line_id": line_index, "line": line["name"], "mode": line["mode"], "pattern_id": pattern_index,
                    "stop_pair_index": offset, "from": a_id, "to": b_id,
                    "strategy": "approximate", "reason": reason,
                    "straight_m": round(straight, 1),
                })
                approximate_features.append({
                    "type": "Feature",
                    "properties": {
                        "from": a_id, "to": b_id, "line_ids": [line_index],
                        "modes": [line["mode"]], "color": line["color"], "approximate": True,
                    },
                    "geometry": {"type": "LineString", "coordinates": [
                        [stations_by_id[a_id]["lng"], stations_by_id[a_id]["lat"]],
                        [stations_by_id[b_id]["lng"], stations_by_id[b_id]["lat"]],
                    ]},
                })

    # Regression guard against the prior normalized dataset for the known KRL
    # Bogor service. This also pins station ordering and seconds conversion.
    expected_1000 = [
        ("MRI", 18600), ("TEB", 18810), ("CW", 18930),
        ("DRN", 19050), ("PSMB", 19110), ("PSM", 19380),
    ]
    old_path = ROOT / "data" / "fallback" / "trips.json"
    if not old_path.is_file():
        raise FileNotFoundError(f"required regression baseline missing: {old_path}")
    old_rows = json.loads(old_path.read_text(encoding="utf-8"))
    old_trip = next((row for row in old_rows if str(row.get("train_id")) == "1000"), None)
    if old_trip is None:
        raise AssertionError("train 1000 missing from old data/trips.json baseline")
    old_actual = [(stop["station_id"], stop["t"]) for stop in old_trip["stops"][:len(expected_1000)]]
    new_trip = next((row for row in trips if row["train_id"] == "1000"), None)
    if new_trip is None:
        raise AssertionError("train 1000 missing from generated trips")
    new_actual = [(stop["station_id"], stop["t"]) for stop in new_trip["stops"][:len(expected_1000)]]
    if old_actual != expected_1000:
        raise AssertionError(f"old train 1000 baseline differs: {old_actual!r}")
    if new_actual != expected_1000:
        raise AssertionError(f"generated train 1000 regression failed: {new_actual!r}")

    output_dir.mkdir(parents=True, exist_ok=True)
    write_json(output_dir / "lines.json", lines)
    write_json(output_dir / "stations.json", list(stations_by_id.values()))
    write_json(output_dir / "trips.json", trips)
    write_json(output_dir / "segments.geojson", {
        "type": "FeatureCollection", "features": valid_tracks + approximate_features,
    })

    track_errors = sorted(endpoint_errors)
    error_summary = {
        "decoded_track_count": len(endpoint_errors),
        "median_max_endpoint_error_m": round(statistics.median(x[0] for x in track_errors), 1) if track_errors else None,
        "worst_max_endpoint_error_m": round(track_errors[-1][0], 1) if track_errors else None,
        "worst_track": {"from": track_errors[-1][1], "to": track_errors[-1][2]} if track_errors else None,
    }
    worst_details = None
    if track_errors:
        worst_from, worst_to = track_errors[-1][1], track_errors[-1][2]
        feature = next(
            item for item in valid_tracks
            if item["properties"]["from"] == worst_from and item["properties"]["to"] == worst_to
        )
        coords = feature["geometry"]["coordinates"]
        worst_details = {
            "from": {
                "station_lonlat": [stations_by_id[worst_from]["lng"], stations_by_id[worst_from]["lat"]],
                "decoded_endpoint_lonlat": coords[0],
                "error_m": round(distance_m(coords[0], [stations_by_id[worst_from]["lng"], stations_by_id[worst_from]["lat"]]), 1),
            },
            "to": {
                "station_lonlat": [stations_by_id[worst_to]["lng"], stations_by_id[worst_to]["lat"]],
                "decoded_endpoint_lonlat": coords[-1],
                "error_m": round(distance_m(coords[-1], [stations_by_id[worst_to]["lng"], stations_by_id[worst_to]["lat"]]), 1),
            },
            "interpretation": "Endpoint-to-station coordinate offset at BOO; the source file alone cannot establish which coordinate is closer to the platform.",
        }
    weekday_counts = dict(sorted(per_day_service["weekday"].items()))
    sunday_counts = dict(sorted(per_day_service["sunday"].items()))
    validation = {
        "input": str(input_path), "output_dir": str(output_dir),
        "counts": {
            "lines": len(lines), "stations": len(stations_by_id), "tracks_source": len(tracks_raw),
            "tracks_decoded": len(valid_tracks), "patterns": len(patterns_raw), "trains": len(trips),
            "pattern_stop_pairs": sum(len(pattern) - 1 for pattern in patterns_raw),
        },
        "track_endpoints": error_summary,
        "worst_track_endpoint_details": worst_details,
        "skipped_tracks": skipped_tracks,
        "day_type_codes": {str(code): day_type for code, day_type in DAY_TYPES.items()},
        "train_counts_by_day_type": dict(sorted(day_counts.items())),
        "train_counts_by_service_record": dict(sorted(service_counts.items())),
        "train_counts_by_selected_day": {"weekday": weekday_counts, "sunday": sunday_counts},
        "selected_day_totals": {
            "weekday": sum(weekday_counts.values()), "sunday": sum(sunday_counts.values()),
        },
        "time_encoding": {
            "raw_unit_seconds": 30,
            "array_semantics": "first value is the start time; following values are per-stop elapsed-time increments",
            "meta_minutes_note_is_wrong": True,
            "note": "Source time values are 30-second units, not minutes; the source metadata's minutes note is incorrect.",
        },
        "pipeline_notes": [
            "Train time arrays use 30-second units (first start-time value, then per-stop increments).",
            "The source metadata note calling these values minutes is incorrect.",
            "Conversion to trips.json seconds is raw cumulative units multiplied by 30.",
        ],
        "train_1000_regression": {
            "baseline": "data/fallback/trips.json",
            "expected_first_six_stops": [{"station_id": station, "t": seconds} for station, seconds in expected_1000],
            "passed": True,
        },
        "time_values_at_or_after_24h": time_over_24h,
        "max_trip_time_seconds": int(max_time_min * 60),
        "missing_direct_track_pairs": pair_routes,
        "approximate_pairs": [pair for pair in pair_routes if pair["strategy"] == "approximate"],
    }
    write_json(output_dir / "validation.json", validation)
    meta = dict(source.get("meta", {}))
    meta["day_type_legend"] = {"daily": "weekday and Sunday", "weekday": "weekday only", "sunday": "Sunday only"}
    meta["time_encoding"] = "30-second units; source metadata's minutes note is incorrect"
    meta["source_file"] = input_path.name
    write_json(output_dir / "meta.json", meta)
    return validation


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=INPUT_JSON)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    args = parser.parse_args()
    report = build(args.input, args.output_dir)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
