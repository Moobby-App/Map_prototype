"""Fetch Greater Jakarta station points and route geometry from Overpass."""
import argparse
import json
import logging
import re
import unicodedata
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

try:
    from .scrape_krl import ROOT, active_stations, write_json
except ImportError:
    from scrape_krl import ROOT, active_stations, write_json

LOG = logging.getLogger(__name__)
# Includes Rangkasbitung, Merak, Bogor, Cikarang and Purwakarta.
BBOX = "-6.95,105.85,-5.85,107.55"
QUERY = '''[out:json][timeout:180];
(
  nwr[railway=station]({bbox});
  relation[type=route][route~"^(train|subway|light_rail)$"]({bbox});
);
out body center geom;
'''


def name_key(name):
    name = unicodedata.normalize("NFKD", name).casefold()
    name = re.sub(r"\b(stasiun|station|krl|commuterline|commuter|mrt|lrt)\b", "", name)
    return "".join(c for c in name if c.isalnum())


def mode_of(tags):
    if tags.get("station") == "subway" or tags.get("subway") == "yes":
        return "MRT"
    if tags.get("station") == "light_rail" or tags.get("light_rail") == "yes":
        return "LRT"
    return "KRL"


def point(element):
    source = element if "lat" in element else element.get("center", {})
    if "lat" not in source or "lon" not in source:
        return None
    return source["lat"], source["lon"]


def route_segments(members):
    """Join consecutive connected ways; preserve gaps as separate line strings."""
    segments = []
    for member in members:
        if member.get("type") != "way" or member.get("role", "") not in ("", "forward", "backward"):
            continue  # Exclude platform ways and other non-track members.
        geometry = member.get("geometry", [])
        if not geometry or any(p is None or "lon" not in p or "lat" not in p for p in geometry):
            continue
        coords = [[p["lon"], p["lat"]] for p in geometry]
        if len(coords) < 2:
            continue
        if member.get("role") == "backward":
            coords.reverse()
        if segments and segments[-1][-1] == coords[0]:
            segments[-1].extend(coords[1:])
        elif segments and member.get("role", "") == "" and segments[-1][-1] == coords[-1]:
            segments[-1].extend(list(reversed(coords))[1:])
        else:
            segments.append(coords)
    return segments


def convert(payload, kci, overrides=None):
    elements = payload.get("elements")
    if not isinstance(elements, list) or payload.get("remark"):
        raise ValueError("Incomplete/invalid Overpass result; outputs not replaced")
    candidates = defaultdict(list)
    points = {}
    for element in elements:
        tags = element.get("tags", {})
        coord = point(element)
        if tags.get("railway") != "station" or coord is None:
            continue
        osm_id = f"{element['type']}/{element['id']}"
        item = {"id": f"osm:{osm_id}", "name": tags.get("name", osm_id),
                "lat": coord[0], "lng": coord[1], "mode": mode_of(tags)}
        points[osm_id] = item
        names = [v for k, v in tags.items() if k in ("name", "alt_name", "short_name", "official_name", "name:id", "name:en")]
        for name in names:
            for alias in name.split(";"):
                key = name_key(alias)
                if key and osm_id not in candidates[key]:
                    candidates[key].append(osm_id)
    stations, unmatched, used = [], [], set()
    for station in kci:
        sid, name = str(station["sta_id"]), str(station["sta_name"])
        override = (overrides or {}).get(sid)
        matches = [override] if override else candidates.get(name_key(name), [])
        matches = [oid for oid in matches if oid in points and points[oid]["mode"] == "KRL"]
        # Collapse duplicate OSM representations within ~100 m, prefer station nodes.
        matches.sort(key=lambda oid: (not oid.startswith("node/"), oid))
        if len(matches) > 1:
            first = points[matches[0]]
            if all((points[oid]["lat"] - first["lat"]) ** 2 +
                   (points[oid]["lng"] - first["lng"]) ** 2 < 0.001 ** 2 for oid in matches):
                used.update(matches)
                matches = matches[:1]
        if len(matches) != 1:
            reason = "ambiguous: " + ", ".join(matches) if matches else "no match"
            unmatched.append(f"{sid}\t{name}\t{reason}")
            continue
        oid = matches[0]
        used.add(oid)
        stations.append({**points[oid], "id": sid, "name": name, "mode": "KRL"})
    # Add MRT/LRT station points using namespaced IDs; keep KRL IDs exclusively KCI-owned.
    stations.extend(item for oid, item in points.items() if oid not in used and item["mode"] in ("MRT", "LRT"))
    features = []
    for element in elements:
        tags = element.get("tags", {})
        route = tags.get("route")
        if element.get("type") != "relation" or route not in ("train", "subway", "light_rail"):
            continue
        segments = route_segments(element.get("members", []))
        if not segments:
            LOG.warning("OSM relation %s has no usable track geometry", element["id"])
            continue
        features.append({"type": "Feature", "id": f"osm:relation/{element['id']}",
            "properties": {"name": tags.get("name", ""), "ref": tags.get("ref", ""),
                           "mode": ("KRL" if route == "train" and re.search(r"krl|commuter", " ".join(str(v) for v in tags.values()), re.I)
                                    else {"train": "TRAIN", "subway": "MRT", "light_rail": "LRT"}[route]),
                           "route": route,
                           "color": tags.get("colour", ""), "operator": tags.get("operator", ""),
                           "osm_id": element["id"]},
            "geometry": {"type": "MultiLineString", "coordinates": segments}})
    return stations, {"type": "FeatureCollection", "features": features}, unmatched


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--endpoint", default="https://overpass-api.de/api/interpreter")
    parser.add_argument("--bbox", default=BBOX, help="south,west,north,east")
    parser.add_argument("--refresh", action="store_true", help="Replace cached Overpass response")
    parser.add_argument("--offline", action="store_true", help="Use cached response only")
    parser.add_argument("--overrides", type=Path, help='JSON mapping KCI station IDs to OSM IDs, e.g. {"ABC": "node/123"}')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    try:
        if args.offline and args.refresh:
            raise ValueError("--offline and --refresh cannot be combined")
        bounds = [float(n) for n in args.bbox.split(",")]
        if len(bounds) != 4 or not (-90 <= bounds[0] < bounds[2] <= 90 and -180 <= bounds[1] < bounds[3] <= 180):
            raise ValueError("Invalid bounding box")
        cache = args.data_dir / "raw" / "osm.json"
        kci = active_stations(json.loads((args.data_dir / "raw" / "krl" / "stations.json").read_text()))
        if not kci:
            raise ValueError("KCI cache has no active stations")
        query = QUERY.format(bbox=",".join(str(n) for n in bounds))
        if not cache.exists() or args.refresh:
            if args.offline:
                raise ValueError("No cached Overpass response")
            request = urllib.request.Request(args.endpoint,
                data=urllib.parse.urlencode({"data": query}).encode(),
                headers={"User-Agent": "JakartaRailLocalPrototype/0.1", "Accept": "application/json"})
            LOG.info("Querying Overpass (may take several minutes)")
            with urllib.request.urlopen(request, timeout=240) as response:
                payload = json.load(response)
            if not isinstance(payload.get("elements"), list) or payload.get("remark"):
                raise ValueError("Overpass returned an incomplete response; cache preserved")
            write_json(cache, {"query": query, "response": payload})
        cached = json.loads(cache.read_text())
        if cached.get("query") != query:
            raise ValueError("Cached bounding box differs; use --refresh")
        overrides = json.loads(args.overrides.read_text()) if args.overrides else {}
        stations, lines, unmatched = convert(cached["response"], kci, overrides)
        write_json(args.data_dir / "stations.json", stations)
        write_json(args.data_dir / "lines.geojson", lines)
        (args.data_dir / "unmatched.txt").write_text("\n".join(unmatched) + ("\n" if unmatched else ""))
        LOG.info("Wrote %d stations, %d routes; %d unmatched KCI stations", len(stations), len(lines["features"]), len(unmatched))
        for item in unmatched:
            LOG.warning("Unmapped station: %s", item)
    except Exception as error:
        LOG.error("%s", error)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
