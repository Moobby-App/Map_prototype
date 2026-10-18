"""Build deduplicated straight segments from drawable KRL trips."""
import math
from collections import Counter, defaultdict

from .common import DATA, ROOT, read_json, write_json


def coordinate_index(stations):
    return {
        station["id"]: station
        for station in stations
        if all(isinstance(station.get(key), (int, float)) and math.isfinite(station[key])
               for key in ("lat", "lng"))
        and abs(station["lat"]) <= 90 and abs(station["lng"]) <= 180
    }


def is_drawable(trip, stations):
    stops = trip.get("stops", [])
    times = [stop.get("t") for stop in stops]
    return (
        len(stops) >= 2
        and all(stop.get("station_id") in stations for stop in stops)
        and all(isinstance(time, (int, float)) and math.isfinite(time) and time >= 0 for time in times)
        and times == sorted(times)
    )


def build_segments(trips, station_rows):
    stations = coordinate_index(station_rows)
    colors = defaultdict(Counter)
    drawable_count = 0

    for trip in trips:
        if not is_drawable(trip, stations):
            continue
        drawable_count += 1
        color = trip.get("color") or "#808080"
        stop_ids = [stop["station_id"] for stop in trip["stops"]]
        for first, second in zip(stop_ids, stop_ids[1:]):
            if first == second:
                continue
            pair = tuple(sorted((first, second)))
            colors[pair][color] += 1

    features = []
    for (from_id, to_id), color_counts in sorted(colors.items()):
        start, end = stations[from_id], stations[to_id]
        features.append({
            "type": "Feature",
            "properties": {
                "from": from_id,
                "to": to_id,
                "color": color_counts.most_common(1)[0][0],
                "approximate": True,
            },
            "geometry": {
                "type": "LineString",
                "coordinates": [[start["lng"], start["lat"]], [end["lng"], end["lat"]]],
            },
        })
    return {"type": "FeatureCollection", "features": features}, drawable_count


def main():
    if __import__("pathlib").Path.cwd().resolve() != ROOT:
        raise RuntimeError(f"Run Python from {ROOT}")
    stations = read_json(DATA / "stations.json")
    trips = read_json(DATA / "trips.json")
    segments, drawable_count = build_segments(trips, stations)
    write_json(DATA / "segments.geojson", segments)
    print(f"Wrote {len(segments['features'])} unique segments from {drawable_count} drawable trips")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("STOP:", str(error))
        raise SystemExit(1)
