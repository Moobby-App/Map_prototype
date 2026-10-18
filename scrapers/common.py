import json
import re
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
# Keep outputs where the existing FastAPI allowlist serves them.
DATA = ROOT / "rail-live" / "data"
OUT_OF_SCOPE_STATION_IDS = {"MER", "YK", "SK", "SLO"}

def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)

def read_json(path):
    return json.loads(path.read_text())

def active(rows):
    return [r for r in rows if r.get("fg_enable") and str(r["fg_enable"]).lower() not in ("0", "false", "no")]

def seconds(value):
    match = re.fullmatch(r"(\d{1,2}):([0-5]\d)(?::([0-5]\d))?", str(value))
    if not match or int(match[1]) > 47:
        raise ValueError(f"Ambiguous time format: {value!r}")
    return int(match[1])*3600 + int(match[2])*60 + int(match[3] or 0)

def name_key(value):
    return re.sub(r"[^A-Z0-9]", "", re.sub(r"\b(STASIUN|STATION)\b", "", str(value).upper()))

def station_overrides():
    """Load validated station coordinate overrides shared by scraping stages."""
    path = DATA / "station_overrides.json"
    if not path.exists(): return {}
    result = read_json(path)
    if not isinstance(result, dict): raise ValueError(f"{path} must contain an object")
    for sid, point in result.items():
        if not isinstance(point, dict) or not all(isinstance(point.get(k), (int, float)) for k in ("lat", "lng")):
            raise ValueError(f"Invalid coordinate override for {sid}")
        if not (-90 <= point["lat"] <= 90 and -180 <= point["lng"] <= 180):
            raise ValueError(f"Coordinate override out of range for {sid}")
    return result
