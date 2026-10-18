"""Local railway map server. Run from rail-live with uvicorn server.main:app."""

import os
import time
from pathlib import Path

import jwt
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
# Prefer the project configuration; support launching from the workspace root.
ENV_FILE = ROOT / ".env" if (ROOT / ".env").exists() else ROOT.parent / ".env"
load_dotenv(ENV_FILE)
ORIGIN = "http://localhost:8000"
TOKEN_TTL = 1800

app = FastAPI(title="Jakarta Railway Live Map")
app.mount("/static", StaticFiles(directory=ROOT / "web"), name="static")


@app.get("/")
def index():
    return FileResponse(ROOT / "web" / "index.html")


@app.get("/data/{filename}")
def railway_data(filename: str):
    # Expose only normalized outputs, never raw responses or credentials.
    if filename not in {"stations.json", "trips.json", "segments.geojson"}:
        raise HTTPException(404, "Unknown railway data file")
    path = DATA / filename
    if not path.is_file():
        raise HTTPException(404, "Railway data not generated yet")
    return FileResponse(path, media_type="application/json", headers={"Cache-Control": "no-store"})


@app.get("/token")
def token(response: Response):
    response.headers["Cache-Control"] = "no-store"
    team_id = os.getenv("TEAM_ID", "").strip()
    key_id = os.getenv("KEY_ID", "").strip()
    key_path = os.getenv("MAPKIT_PRIVATE_KEY_PATH", "").strip()
    if not all((team_id, key_id, key_path)):
        raise HTTPException(
            503, "Map credentials missing. Configure TEAM_ID, KEY_ID and "
            "MAPKIT_PRIVATE_KEY_PATH in .env or rail-live/.env.",
            headers={"Cache-Control": "no-store"},
        )
    path = Path(key_path).expanduser()
    if not path.is_absolute():
        path = ROOT / path
    now = int(time.time())
    try:
        signed = jwt.encode(
            {"iss": team_id, "iat": now, "exp": now + TOKEN_TTL,
             "origin": ORIGIN, "scope": "mapkit_js"},
            path.read_text(), algorithm="ES256", headers={"kid": key_id},
        )
    except (OSError, ValueError, TypeError, jwt.PyJWTError):
        raise HTTPException(
            503, "Map signing key could not be loaded. Check the configured .p8 key.",
            headers={"Cache-Control": "no-store"},
        ) from None
    return {"token": signed, "expires_at": now + TOKEN_TTL}
