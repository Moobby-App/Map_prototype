"""Probe MRI before allowing a full crawl."""
import json
from . import krl_client as client
from .common import DATA, active, seconds, name_key, write_json


def main():
    print("Chrome dapat terbuka untuk clearance Cloudflare. Profile:", client.IMPERSONATE, flush=True)
    stations = client.fetch_stations()
    write_json(DATA / "raw/krl/stations.json", stations)
    windows = [("06:00", "07:00"), ("06:00", "09:00"), ("07:00", "08:00"), ("08:00", "09:00")]
    results = {}
    for start, end in windows:
        payload = client.fetch_schedules("MRI", start, end)
        results[start, end] = payload["data"]
        write_json(DATA / f"raw/krl/smoke/MRI_{start.replace(':','')}_{end.replace(':','')}.json", payload)
        print(f"HTTP {client.last_status} MRI {start}-{end}: {len(payload['data'])} rows", flush=True)
    rows = results["06:00", "07:00"]
    if not rows:
        raise RuntimeError("MRI smoke returned no rows; cannot verify schema.")
    print("3 RAW SAMPLES:\n" + json.dumps(rows[:3], ensure_ascii=False, indent=2), flush=True)
    for field in ("time_est", "dest_time"):
        values = sorted({str(r[field]) for group in results.values() for r in group})
        for value in values: seconds(value)
        print(field, "formats:", sorted({"HH:MM:SS" if v.count(':') == 2 else "HH:MM" for v in values}), "examples:", values[:3])
    stations = active(stations["data"])
    codes = {str(s["sta_id"]).upper() for s in stations}
    names = {name_key(s["sta_name"]) for s in stations}
    destinations = sorted({str(r["dest"]) for group in results.values() for r in group})
    print("dest classification:", {v: "code" if v.upper() in codes else "name" if name_key(v) in names else "unresolved" for v in destinations})
    key = lambda r: json.dumps(r, sort_keys=True, ensure_ascii=False)
    union = {key(r) for pair, group in results.items() if pair != ("06:00", "09:00") for r in group}
    broad = {key(r) for r in results["06:00", "09:00"]}
    # Ignore 09:00 boundary if the API uses inclusive windows.
    union = {r for r in union if seconds(json.loads(r)["time_est"]) < 9*3600}
    broad = {r for r in broad if seconds(json.loads(r)["time_est"]) < 9*3600}
    consistent = union == broad
    chosen = 180 if consistent else 60
    print(f"Window comparison: hourly union={len(union)}, 3h={len(broad)}, missing={len(union-broad)}, extra={len(broad-union)}")
    print(f"SELECTED: {chosen} minutes. Consistency only tested at MRI 06:00-09:00; not a network-wide row-limit guarantee.", flush=True)
    write_json(DATA / "raw/krl/smoke_report.json", {"station": "MRI", "window_minutes": chosen, "consistent": consistent, "hourly_rows": len(union), "three_hour_rows": len(broad)})

if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("STOP:", str(error), flush=True)
        raise SystemExit(1)
