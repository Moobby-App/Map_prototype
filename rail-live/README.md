# Jakarta Railway Live Map

FastAPI, short-lived ES256 Apple Maps tokens, schedule scraping, station markers,
rail overlays and simulated train playback on an interactive Jakarta map.

## Run locally

From the parent `Mooby Map` directory, use the existing environment:

```sh
rail-live/.venv/bin/python run_server.py
```

This launcher sets the import path automatically. Configuration loads from
`rail-live/.env` when present, otherwise from `Mooby Map/.env`. Exported shell
variables take precedence. Relative signing-key paths always resolve from
`rail-live/`. The root `.env.example` is available for root-level setup; do
not overwrite an existing configured `.env`.

Alternatively, from `Mooby Map`, run uvicorn directly:

```sh
rail-live/.venv/bin/python -m uvicorn server.main:app --app-dir rail-live --host 127.0.0.1 --port 8000 --reload
```

From this directory:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

Put your Apple Maps private signing key in `keys/`, and fill in `TEAM_ID`,
`KEY_ID`, and `MAPKIT_PRIVATE_KEY_PATH` in `.env`. Relative key paths resolve
from this directory. The key must support Apple Maps and ES256 signing.
Do not commit your credentials or private key.

```sh
python -m uvicorn server.main:app --host 127.0.0.1 --port 8000 --reload
```

Open **http://localhost:8000**. Use this exact origin: `127.0.0.1` in the
browser does not match the token's `http://localhost:8000` origin claim.
Internet access is required for Apple's CDN and map tiles.
Restart the server after changing `.env`.

`GET /token` returns `{ "token": "…", "expires_at": 1234567890 }` with
`Cache-Control: no-store`. Tokens last 30 minutes and include `scope: mapkit_js`.
The SDK requests a fresh token through its authorization callback when needed.
Without credentials the server still serves the page; `/token` returns 503
and the page displays setup guidance. No private files are served as static assets.

## Verify

```sh
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
```

Tests use a temporary P-256 signing key; no Apple credentials or network needed.
An authenticated map rendering check requires your actual Apple credentials.

## Step 3: Map and train playback

Restart the server and reload http://localhost:8000 after updating the code.
The frontend requests `/data/stations.json`, `/data/lines.geojson`, and
`/data/trips.json`. Only these three normalized files are exposed; raw responses
and credentials remain private.

Any missing normalized data file (or empty trips) triggers a complete, clearly
labeled synthetic demo dataset with three trains on
a schematic central Jakarta corridor, with fallback station points and route
geometry included in the frontend. They move immediately at the initial
06:00 simulation time and default 60x speed. Demo fixtures never overwrite data
files. Malformed or failed trip requests show an error instead of silently
substituting a demo.

Play/Pause, 1x/10x/60x, and the time slider control the same simulation clock.
The timeline includes extended service hours beyond midnight. It stops at the
end and suspends time progression while the tab is hidden. Reduced-motion
preferences start playback paused. Select stations or train arrows for details.

Real trips require coordinates for every stop; incomplete trips are skipped
with a visible count. Between stops, the engine projects stations onto nearby
route geometry belonging to the trip line (exact normalized ID, ref, or name)
and interpolates by cumulative distance between the projected endpoints. It
rejects distant or excessive detours and reports straight-line fallbacks. This
is a geometric approximation, not track-level routing or GPS tracking. Internal time
is seconds since 00:00, with 86400 added on chronological midnight rollover.
Optional stop `arrival` and `departure` strings enable station dwell; the
scraper’s single `time` value implies zero dwell. Map annotation animation is
limited to approximately 15 fps. The
permanent Indonesian disclaimer identifies positions as schedule estimates.

Verify the geometry and frontend control integration with Node.js:

```sh
node --test tests/test_simulation.mjs
```

## Step 2: KRL schedules and OSM geometry

Install the updated dependencies in your virtual environment:

```sh
python -m pip install -r requirements-dev.txt
python -m scrapers.scrape_krl
python -m scrapers.fetch_osm
```

`cf_session.get_session(force=False)` returns `(cookies, user_agent)`.
On a cache miss it opens **installed Google Chrome**, headed, via
Playwright; complete the challenge if prompted. No bundled Chromium download
is needed. Cookies are stored in `scrapers/.cf_cache.json` with mode `0600`,
expire after at most one hour (or earlier cookie expiry), and are gitignored.
`invalidate()` deletes that cache; `force=True` always opens a fresh browser.
Cookie values are never logged.

The scraper defaults to curl_cffi's `chrome` alias, which selects the newest
Chrome profile supported by your installed curl_cffi version. The session
helper accepts any installed Chrome version and preserves its exact User-Agent.
The TLS profile may differ from the browser version; Cloudflare can still reject
it, in which case the scraper refreshes clearance once and stops if blocked.
You can explicitly choose another supported profile, for example:

```sh
python -m scrapers.scrape_krl --impersonate chrome120
```

The scraper caches the station list in `data/raw/krl/stations.json` and each
station/window response in `<station_id>_<HHMM>_<HHMM>.json`. IDs rather than
names make filenames stable and preserve the join key. Seven windows cover
04:00–24:00, ending with 22:00–24:00. Existing valid files are skipped; malformed
files fail clearly so you can remove and refetch them. Requests are spaced by
1–2 seconds, including the single permitted challenge retry. Failed runs
preserve successful caches and do not replace `trips.json`.

```sh
python -m scrapers.scrape_krl --normalize-only
python -m scrapers.fetch_osm --offline
```

Normalization requires a complete cache and groups by train ID, deduplicating
station/time pairs. Times use a **04:00 service-day boundary**: 00:12 becomes
24:12 and 01:30 becomes 25:30. These extended `HH:MM` values preserve midnight
ordering without adding a date field; Step 3 must accept hours above 23. Times
before 04:00 are only available if the API includes them in the requested
windows; this does not invent missing overnight rows. The cache represents
one timetable snapshot; remove `data/raw/krl/` before collecting a new one to
avoid mixing revisions. Single-stop trips are retained and warned about, as
are unknown station IDs and malformed schedule times.

Overpass uses a Greater Jakarta bounding box covering Merak, Rangkasbitung,
Bogor and Cikarang. Its raw result is cached in `data/raw/osm.json`. Use
`--refresh` to fetch again, or `--bbox south,west,north,east` to change coverage.
The query includes station nodes and centers of station ways/relations. KCI
names match normalized OSM names and aliases; distinct ambiguous matches are
left for manual correction rather than guessed. Nearby duplicate OSM station
representations are collapsed. KRL points retain KCI station IDs. Recognizable
MRT/LRT points use namespaced OSM IDs; incomplete OSM mode tags can limit these.

Unmatched KCI names and candidate IDs go to `data/unmatched.txt`. Resolve them
with a JSON mapping of KCI IDs to OSM IDs, then rerun using the cached data:

```json
{"KCI_STATION_ID": "node/123456"}
```

```sh
python -m scrapers.fetch_osm --offline --overrides station_overrides.json
```

`lines.geojson` contains one MultiLineString per route relation. Connected
consecutive way members are joined (including reversed ways); gaps stay
separate and platform ways are excluded. Train relations include intercity
services: routes tagged/named KRL or commuter use mode `KRL`, other trains use
`TRAIN`. The result is raw route geometry, not yet a train-to-route matching
index. Preserve OpenStreetMap attribution when showing this data:
© OpenStreetMap contributors, https://www.openstreetmap.org/copyright.

Offline tests cover midnight rollover, deduplication, challenge retry, cookie
cache lifecycle, ambiguous station matching, overrides and disconnected route
geometry. Live KCI/Cloudflare and Overpass acquisition must be verified locally.

### Current schema inspection

At the time of this update, `data/stations.json`, `data/trips.json`, and
`data/lines.geojson` were absent; no actual downloaded schema was verified.
The UI explicitly warns and uses mock data when a file is absent. Adapters in
`web/app.js` validate the following supported contract, and report invalid
records rather than infer undocumented fields:

- `normalizeStations`: array of `{id, name, lat, lng, mode}` → station ID map.
- `normalizeTrips`: array of `{train_id, line, color, dest, stops}`; each stop
  has `station_id` and `time`, or `arrival`/`departure` in `HH:MM[:SS]` (extended
  hours supported) → stops with arrival/departure seconds and measured segments.
- `normalizeLines`: GeoJSON FeatureCollection with LineString/MultiLineString
  `[lng, lat]` coordinates; optional `id`, `name`, `ref`, `line`, `color`/`colour`
  properties → measured route parts with matching keys and display styles.

Server URLs: `/data/stations.json`, `/data/trips.json`, `/data/lines.geojson`.
They return application/json with no-store, or 404 for absent files.
