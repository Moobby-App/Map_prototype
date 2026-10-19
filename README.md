# Mooby Map

Mooby Map is a KRL-only railway map for Jakarta. It shows timetable-based train
movement over rail geometry, uses the real WIB clock, models station dwell, and
polls KCI in the background for live service status. Train positions are schedule
estimates, not GPS tracking.

## Run the map

### Requirements

- Python 3.11 or newer
- An Apple MapKit JS key with its matching Team ID, Key ID, and `.p8` private key
- Internet access for Apple MapKit JS and KCI live polling
- Google Chrome installed for Cloudflare clearance when KCI requires it

Clone the repository and run these commands from its root:

```sh
git clone https://github.com/Moobby-App/Map_prototype.git
cd Map_prototype
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` with your Apple `TEAM_ID` and `KEY_ID`, and set
`MAPKIT_PRIVATE_KEY_PATH` to your matching `.p8` file. Keep the private key out
of Git. The default path in the template is `keys/AuthKey.p8`; relative paths
are resolved from the repository root. Set `CHROME_PATH` only if Chrome is not
at the default macOS location. `MAPKIT_ORIGIN` must remain
`http://localhost:8000` when opening the app at that address.

Start the server:

```sh
python run_server.py
```

Open <http://localhost:8000>. Use `localhost` exactly because the MapKit token
is signed for that origin. If Apple credentials are not configured, the server
still serves the app page, but MapKit cannot initialize.

## Review the data

The committed files in `data/runtime/` are the generated map assets. A fresh
clone can serve them immediately after installing dependencies and configuring
MapKit. `data/fallback/` contains the older normalized KRL dataset used only if
a current runtime asset is missing. Raw KCI and OSM captures are local pipeline
caches and are ignored by Git.

The current map data comes from `pipeline/source/open-krl.json`. To rebuild the
runtime files after changing that input, run from the repository root:

```sh
python -m pipeline.scripts.validate_network
```

The separate KRL/OSM collection pipeline is available under
`pipeline/scripts/`. Its full crawl requires a successful smoke run first:

```sh
python -m pipeline.scripts.smoke_krl
python -u -m pipeline.scripts.run_pipeline
```

That pipeline updates the fallback dataset. It uses Chrome to solve a KCI
Cloudflare challenge when needed and caches raw responses under `data/raw/`.
The app's background poller also contacts KCI. Set `CHROME_PATH` if the
installed Chrome executable is elsewhere.

OpenStreetMap-derived geometry requires attribution:
© OpenStreetMap contributors, <https://www.openstreetmap.org/copyright>.

## Check the project

Run the Python and JavaScript tests from the repository root:

```sh
python -m unittest discover -s tests -v
node --test tests/*.mjs
```

Check Python and browser JavaScript syntax:

```sh
python -m compileall -q run_server.py server live pipeline tests legacy
node --check web/app.js
node --check web/simulation.js
```

## Runtime endpoints

- `/` serves the map page; `/static/` serves its frontend files.
- `/token` returns a short-lived MapKit JS authorization token.
- `/api/live` returns background KCI poll status and schedule comparisons.
- `/output/stations.json`, `/output/trips.json`, and
  `/output/segments.geojson` serve current generated data. The frontend falls
  back to the matching `/data/` paths when current files are unavailable.

The service clock uses `Asia/Jakarta`. Timetables include service hours after
midnight, and train motion includes dwell at intermediate stations and at the
terminus.
