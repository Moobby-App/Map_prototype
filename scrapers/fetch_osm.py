"""Single cached Overpass query; conservative matches and explicit approximate lines."""
from collections import Counter, defaultdict
import json
import math
import urllib.parse
import urllib.request
from rapidfuzz.fuzz import ratio
from .common import DATA, ROOT, OUT_OF_SCOPE_STATION_IDS, active, name_key, read_json, write_json, station_overrides

BBOX = "-6.75,106.2,-5.95,107.25"
STATION_ALIASES = {
    "UI": ("Universitas Indonesia",),
    "UP": ("Universitas Pancasila",),
    "TPK": ("Tanjung Priok",),
}
QUERY = f'''[out:json][timeout:180];
(node[railway~"^(station|halt)$"]({BBOX});way[railway~"^(station|halt)$"]({BBOX});
relation[route~"^(train|subway|light_rail)$"]({BBOX}););
out body center; >; out body geom;'''


def distance_km(a, b):
    lat1, lat2 = math.radians(a["lat"]), math.radians(b["lat"])
    dlat, dlng = lat2-lat1, math.radians(b["lng"]-a["lng"])
    h = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlng/2)**2
    return 6371 * 2 * math.asin(math.sqrt(h))


def validate_override(sid, point, known):
    """Reject corridor coordinates that are out of station order or far off line."""
    if sid in ("UP", "UI"):
        before_id, after_id = (("LNA", "UI") if sid == "UP" else ("UP", "POC"))
        if sid == "UI":
            before_id = "UP"
        before, after = known.get(before_id), known.get(after_id)
        if before and after:
            vx, vy = after["lng"]-before["lng"], after["lat"]-before["lat"]
            wx, wy = point["lng"]-before["lng"], point["lat"]-before["lat"]
            fraction = (vx*wx + vy*wy) / (vx*vx + vy*vy)
            cross_track_km = abs(vx*wy-vy*wx) / math.hypot(vx, vy) * 111
            if not 0 < fraction < 1 or cross_track_km > 3:
                raise ValueError(f"{sid} override is not between {before_id} and {after_id}")
    elif sid == "TPK":
        north_end = [known[s] for s in ("JAKK", "KPB") if s in known]
        if not north_end or min(distance_km(point, item) for item in north_end) > 15:
            raise ValueError("TPK override must be within 15 km of Jakarta Kota/Kampung Bandan")


def convert(payload, station_rows, trips, overrides=None):
    if payload.get("remark") or not isinstance(payload.get("elements"), list):
        raise RuntimeError("Overpass incomplete/ambiguous response; stop.")
    # out produces duplicate elements; retain the richest representation.
    elements = {}
    for item in payload["elements"]:
        key = item["type"], item["id"]
        elements[key] = {**elements.get(key, {}), **item}
    candidates = []
    for item in elements.values():
        tags = item.get("tags", {})
        if tags.get("railway") not in ("station", "halt"): continue
        coord = item if "lat" in item else item.get("center", {})
        if "lat" not in coord or "lon" not in coord: continue
        # Prevent subway/light-rail namesakes from replacing KRL stations.
        if tags.get("station") in ("subway", "light_rail") or tags.get("subway") == "yes": continue
        aliases = [name_key(v) for k,v in tags.items() if k in ("name", "alt_name", "official_name", "name:id", "name:en", "short_name") for v in v.split(";")]
        if aliases: candidates.append((item, coord, aliases))
    stations, unmatched = [], []
    known = {s["id"]: s for s in read_json(DATA / "stations.json")}
    known.update({sid: {"id": sid, **point} for sid, point in (overrides or {}).items()})
    for station in active(station_rows):
        key = name_key(station["sta_name"])
        sid = str(station["sta_id"])
        if sid in OUT_OF_SCOPE_STATION_IDS:
            continue
        if sid in (overrides or {}):
            point = overrides[sid]
            validate_override(sid, point, known)
            stations.append({"id": sid, "name": station["sta_name"], "lat": point["lat"], "lng": point["lng"], "mode": "krl"})
            known[sid] = stations[-1]
            continue
        alias_keys = [name_key(alias) for alias in STATION_ALIASES.get(sid, ())]
        target_keys = [key, *alias_keys]
        ranked = sorted([(max(ratio(target, alias) for target in target_keys for alias in aliases), item, coord)
                         for item,coord,aliases in candidates], key=lambda v: -v[0])
        # Explicit aliases win exact matches; remaining candidates keep fuzzy fallback.
        explicit = [(100.0, item, coord) for item, coord, aliases in candidates if set(target_keys) & set(aliases)]
        if explicit:
            explicit_ids = {(x[1]["type"], x[1]["id"]) for x in explicit}
            ranked = explicit + [r for r in ranked if (r[1]["type"], r[1]["id"]) not in explicit_ids]
        if not ranked or ranked[0][0] < 92:
            neighbours = {"UP": ("LNA", "UI"), "UI": ("UP", "POC"), "TPK": ("JAKK", "KPB")}.get(sid, ())
            neighbour_points = [known[n] for n in neighbours if n in known]
            suggestions = []
            for _, item, coord in ranked[:5]:
                point = {"lat": coord["lat"], "lng": coord["lon"]}
                distance = min((distance_km(point, n) for n in neighbour_points), default=float("nan"))
                suggestions.append(f"{item.get('tags', {}).get('name', '?')} ({point['lat']}, {point['lng']}), {distance:.2f} km from nearest neighbour")
            unmatched.append(f"{sid}\t{station['sta_name']}\toutside bbox or no confident name match")
            if suggestions and sid in STATION_ALIASES:
                print(f"Confirm a candidate for {sid} ({station['sta_name']}): " + "; ".join(suggestions))
            continue
        best = ranked[0]
        alternatives = [r for r in ranked[1:] if r[0] >= best[0]-4 and
                        (abs(r[2]['lat']-best[2]['lat']) > .001 or abs(r[2]['lon']-best[2]['lon']) > .001)]
        if alternatives:
            unmatched.append(f"{station['sta_id']}\t{station['sta_name']}\tambiguous OSM matches")
            continue
        point = {"lat": best[2]["lat"], "lng": best[2]["lon"]}
        validate_override(sid, point, known)
        stations.append({"id": sid, "name": station["sta_name"], "lat": point["lat"], "lng": point["lng"], "mode": "krl"})
        known[sid] = stations[-1]
    index = {s["id"]: s for s in stations}
    grouped = defaultdict(list)
    for trip in trips: grouped[trip["route"]].append(trip)
    features, methods = [], []
    for route, group in sorted(grouped.items()):
        patterns = Counter(tuple(s["station_id"] for s in trip["stops"]) for trip in group)
        pattern = max(patterns, key=lambda p: (patterns[p], len(p)))
        if any(sid not in index for sid in pattern):
            methods.append({"line": route, "method": "omitted", "reason": "most common trip has unmatched stations"})
            continue
        points = [[index[sid]["lng"], index[sid]["lat"]] for sid in pattern]
        if len(points) < 2: continue
        # Do not infer relation identity from geometric proximity. Require route-name equality.
        matches = [e for e in elements.values() if e["type"] == "relation" and e.get("tags", {}).get("route") == "train" and
                   name_key(route) in [name_key(e.get("tags", {}).get(k, "")) for k in ("name", "ref")] and name_key(route)]
        parts = []
        if len(matches) == 1:
            for member in matches[0].get("members", []):
                if member["type"] != "way" or member.get("role", "") not in ("", "forward", "backward"): continue
                way = elements.get(("way", member["ref"]), {})
                geom = way.get("geometry", [])
                if len(geom) >= 2 and all(p and "lon" in p and "lat" in p for p in geom):
                    coords = [[p["lon"], p["lat"]] for p in geom]
                    parts.append(coords[::-1] if member.get("role") == "backward" else coords)
        approximate = not parts
        geometry = {"type": "MultiLineString", "coordinates": parts} if parts else {"type": "LineString", "coordinates": points}
        props = {"line_id": route, "name": route, "color": group[0]["color"], "mode": "krl", "approximate": approximate}
        features.append({"type": "Feature", "properties": props, "geometry": geometry})
        methods.append({"line": route, "method": "most common trip, straight segments" if approximate else "exact OSM relation name/ref", "approximate": approximate})
    return stations, {"type": "FeatureCollection", "features": features}, unmatched, methods


def main():
    from pathlib import Path
    if Path.cwd().resolve() != ROOT: raise RuntimeError(f"Run Python from {ROOT}")
    station_rows = read_json(DATA / "raw/krl/stations.json")["data"]
    trips = read_json(DATA / "trips.json")
    cache = DATA / "raw/osm/overpass.json"
    if cache.exists(): payload = read_json(cache)
    else:
        request = urllib.request.Request("https://overpass-api.de/api/interpreter", data=urllib.parse.urlencode({"data": QUERY}).encode(), headers={"User-Agent": "JakartaRailLocal/0.2", "Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=240) as response: payload = json.load(response)
        if payload.get("remark") or not isinstance(payload.get("elements"), list): raise RuntimeError("Overpass incomplete response; stop without caching")
        write_json(cache, payload)
    overrides = station_overrides()
    stations, lines, unmatched, methods = convert(payload, station_rows, trips, overrides)
    write_json(DATA / "stations.json", stations)
    write_json(DATA / "lines.geojson", lines)
    (DATA / "unmatched_stations.txt").write_text("\n".join(unmatched)+"\n")
    write_json(DATA / "raw/osm/line_methods.json", methods)
    print(f"Stations={len(stations)}, unmatched={len(unmatched)}, lines={len(lines['features'])}")
    for method in methods: print(method)

if __name__ == "__main__":
    try: main()
    except Exception as error:
        print("STOP:", str(error))
        raise SystemExit(1)
