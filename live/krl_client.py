"""KCI requests using the unchanged reference clearance helper."""
import random
import re
import time
from typing import get_args
from curl_cffi import requests
from curl_cffi.requests.impersonate import BrowserTypeLiteral
from . import cf_session

BASE_URL = "https://www.kci.id/api/krl"
TARGETS = [s for s in get_args(BrowserTypeLiteral) if re.fullmatch(r"chrome\d+", s)]
IMPERSONATE = "chrome150" if "chrome150" in TARGETS else max(TARGETS, key=lambda s: int(s[6:]))
HEADERS = {"Accept": "application/json, text/plain, */*", "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7", "Referer": "https://www.kci.id/", "Origin": "https://www.kci.id"}
_last = None
last_status = None

class APIError(RuntimeError):
    pass

class CloudflareError(APIError):
    pass


def fetch_api(endpoint, params=None, _retried=False):
    global _last, last_status
    cookies, user_agent = cf_session.get_session()
    if not cookies.get("cf_clearance"):
        raise CloudflareError("Challenge tidak menghasilkan cf_clearance.")
    if _last is not None:
        time.sleep(max(0, random.uniform(1, 2) - (time.monotonic() - _last)))
    try:
        response = requests.get(f"{BASE_URL}/{endpoint}", headers={**HEADERS, "User-Agent": user_agent},
            cookies=cookies, params=params, impersonate=IMPERSONATE, timeout=25)
    except Exception as error:
        raise APIError(f"[NET] Gagal menghubungi {endpoint}: {type(error).__name__}") from None
    finally:
        _last = time.monotonic()
    last_status = response.status_code
    stale = response.status_code in (403, 503) or "just a moment" in response.text.lower()
    if stale:
        if not _retried:
            print("[CF] Cookie ditolak/kedaluwarsa - menyelesaikan challenge ulang...", flush=True)
            cf_session.invalidate()
            cf_session.get_session(force=True)
            return fetch_api(endpoint, params, _retried=True)
        raise CloudflareError(f"[HTTP {response.status_code}] Masih terblokir Cloudflare setelah solve ulang.")
    if response.status_code != 200:
        # Verified against GGL: KCI uses this exact JSON response for no data.
        # Preserve evidence; do not classify arbitrary 404 pages as empty schedules.
        if endpoint == "schedules" and response.status_code == 404 and "json" in response.headers.get("content-type", ""):
            try:
                empty = response.json()
            except ValueError:
                empty = None
            if empty == {"status": 404, "data": "Data not found"}:
                return {"data": [], "_http_status": 404, "_source_response": empty}
        raise APIError(f"[HTTP {response.status_code}] Endpoint '{endpoint}' menolak permintaan.")
    ctype = response.headers.get("content-type", "")
    if "json" not in ctype:
        raise APIError(f"[FORMAT] Endpoint '{endpoint}' balas '{ctype}', bukan JSON.")
    try:
        payload = response.json()
    except ValueError:
        raise APIError(f"[PARSE] Respons '{endpoint}' bukan JSON valid.") from None
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list) or not all(isinstance(r, dict) for r in payload["data"]):
        raise APIError(f"[FORMAT] Skema {endpoint} bukan objek dengan array data.")
    return payload


def fetch_stations():
    return fetch_api("stations")


def fetch_schedules(station_id, time_from="06:00", time_to="09:00"):
    return fetch_api("schedules", {"stationid": station_id, "timefrom": time_from, "timeto": time_to})
