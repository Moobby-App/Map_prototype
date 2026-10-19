"""Validate real normalized files and print the schema handoff previews."""
import math
from collections import Counter
from .common import DATA, RAW, read_json


def main():
    stations = read_json(DATA / "stations.json")
    trips = read_json(DATA / "trips.json")
    lines = read_json(DATA / "lines.geojson")
    index = {s["id"]: s for s in stations if all(isinstance(s.get(k), (int,float)) and math.isfinite(s[k]) for k in ("lat", "lng")) and abs(s["lat"]) <= 90 and abs(s["lng"]) <= 180}
    undrawable = []
    for trip in trips:
        missing = sorted({s["station_id"] for s in trip["stops"] if s["station_id"] not in index})
        times = [s["t"] for s in trip["stops"]]
        if missing or len(times) < 2 or any(not isinstance(t, (int,float)) or not math.isfinite(t) or t < 0 for t in times) or times != sorted(times):
            undrawable.append({"train_id": trip["train_id"], "missing_stations": missing})
    excluded_path = DATA / "trips_excluded.json"
    excluded = read_json(excluded_path) if excluded_path.exists() else []
    print({"stations":len(stations), "trips":len(trips), "excluded":len(excluded),
           "lines":len(lines["features"]), "drawable":len(trips)-len(undrawable), "undrawable":len(undrawable)})
    grouped = Counter(tuple(row["missing_stations"]) for row in undrawable)
    print("Undrawable trips by distinct missing-station set:")
    for missing, count in sorted(grouped.items(), key=lambda item: (-item[1], item[0])):
        print(f"{count}: {', '.join(missing) if missing else '(invalid stop times or fewer than 2 stops)'}")
    methods_path = RAW / "osm/line_methods.json"
    if methods_path.exists():
        methods = read_json(methods_path)
        print("Approximate lines:", sum(bool(item.get("approximate")) for item in methods))
    return bool(undrawable)

if __name__ == "__main__":
    try: raise SystemExit(main())
    except (OSError, ValueError, KeyError) as error:
        print("STOP: data handoff unavailable:", str(error))
        raise SystemExit(1)
