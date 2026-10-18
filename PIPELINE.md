# Real KRL pipeline

Run every Python command from `/Users/nfahrisalim/Public/Mooby Map`.
The reference nodriver helper is copied unchanged from
`/Users/nfahrisalim/test/cf_session.py` into root `scrapers/cf_session.py`.
It caches clearance in `.cf_session.json` at the current working directory.
Never execute its `__main__` entry point: the reference contains a cookie
preview there. The pipeline only calls its functions and never prints values.

Install: `rail-live/.venv/bin/python -m pip install -r requirements.txt`.

1. `rail-live/.venv/bin/python -m scrapers.smoke_krl`
2. `rail-live/.venv/bin/python -u -m scrapers.run_pipeline`

Smoke uses MRI and compares 06–09 against three one-hour windows. Its cached
report selects the full-crawl window size. Full crawl refuses to proceed
without the report. Existing successful schedule caches are skipped. One
Cloudflare refresh is allowed; repeated challenge stops immediately. Five
consecutive other request failures stop the crawl. Incomplete runs do not
replace normalized trips; rerun the same command to resume.

The modules are in root `scrapers/`; outputs are in **rail-live/data/** to
match the existing server. No web or server changes are made. The previous
`rail-live/scrapers/` scripts are legacy: use the root commands above.

Individual stages:

- `rail-live/.venv/bin/python -m scrapers.scrape_krl`
- `rail-live/.venv/bin/python -m scrapers.scrape_krl --normalize-only`
- `rail-live/.venv/bin/python -m scrapers.fetch_osm`
- `rail-live/.venv/bin/python -m scrapers.validate_data`

Raw schedules: `rail-live/data/raw/krl/schedules/<STA>_<HHMM>_<HHMM>.json`.
OSM: one request cached in `rail-live/data/raw/osm/overpass.json`. Errors and
rate limits stop without retrying the Overpass request. Delete its cache only
if you deliberately want to refresh. Bounding box excludes Merak and Central
Java; unmatched or ambiguous station names are explicitly listed in
`unmatched_stations.txt`; no coordinates are invented. Name matching requires
score >=92 and a 4-point margin against geographically distinct alternatives.

Trip schema: `{train_id,name,route,color,dest,stops:[{station_id,t}]}`. `t` is
seconds since midnight, with overnight terminal observations shifted forward.
Schedule observations are assigned to their requested service-day windows,
not their arbitrary station traversal order. Terminal name/code matches are
exact after punctuation normalization; unresolved values remain in the report.
Identical station rows deduplicate; conflicting observed times for the same
train/station stop normalization instead of guessing.

Stations: `{id,name,lat,lng,mode:"krl"}`. GeoJSON properties:
`{line_id,name,color,mode:"krl",approximate}`. Exact unique OSM relation name/ref
matches use member track ways; otherwise the most common stop pattern per
route produces explicitly approximate straight segments. Routes whose most
common pattern has missing coordinates are omitted and reported.
`raw/osm/line_methods.json` records the method per line.

Validation reports every undrawable trip and prints file counts and the first
800 characters of each output. It exits nonzero for missing coordinates.
OSM attribution is required: https://www.openstreetmap.org/copyright.

The current frontend expects an older schema (`line`, string stop `time`).
It will need a separate adapter update for `route` and numeric `t`; this task
intentionally does not change web/ or server/.

Tests: `rail-live/.venv/bin/python -m unittest discover -s tests -v`.
