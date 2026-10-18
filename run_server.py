"""Start the local map from any working directory using this script's path."""
from pathlib import Path
import sys

PROJECT = Path(__file__).resolve().parent / "rail-live"
sys.path.insert(0, str(PROJECT))

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server.main:app", host="127.0.0.1", port=8000,
                reload=True, reload_dirs=[str(PROJECT)])
