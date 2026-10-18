# Railway data pipeline

Run `python3 rail-live/validate_network.py` from the workspace root to rebuild
the normalized files in this directory and print the validation report. The
input is the single source file `rail-live/asset-krl-IRDI4A5B.json`.

Train time arrays store a start-time value followed by per-stop elapsed-time
increments. Each raw unit is 30 seconds. The source metadata's note calling
these values minutes is incorrect. The pipeline converts cumulative raw units
to seconds by multiplying by 30 and keeps times beyond midnight on the extended
timeline.

The validator checks every decodable track against its linked stations with a
150 m endpoint limit, reports median and worst endpoint errors, skips tracks
with missing station references, and records a route decision for every pattern
pair without a direct edge. Alternate paths are constrained to the same line;
unroutable pairs use a dashed approximate connector. It also compares the first
six stops of train 1000 against the legacy normalized file as a regression
check. The legacy file is read only.

`trips.json` retains each trip's `day_type`: `daily`, `weekday`, or `sunday`.
Daily trips are included with either selected day, so a trip never appears twice
in one map view. Service type `kai` remains in the output and is hidden by
default in the frontend.
