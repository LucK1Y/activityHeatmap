# activityHeatmap

Small Python CLI project that turns GPX and FIT activity files into yearly GitHub-style activity heatmaps.

## Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

```bash
python activity_heatmap.py /path/to/file-or-directory [--output-dir /path/to/output-folder]
```

- If you pass a directory, the program recursively scans for `.gpx` and `.fit` files.
- If you pass a file, it processes it only when the extension is supported.
- Output images are saved as `activity-heatmap-<year>.png` in the input directory (or the file's parent directory when input is a file).
- Use `--output-dir` to write all generated PNG files to a different directory.

## Behavior notes

- Every activity is treated as sport.
- Daily totals are the sum of all activity distances recorded for the same calendar day.
- GPX distances are calculated from track points.
- GPX distances use `gpxpy` built-in smoothing and length calculation, with a point-based fallback when needed.
- FIT distances prefer recorded distance fields (`distance`/`total_distance`) when present; otherwise distance is calculated from coordinates.
- Timestamps are converted to calendar dates in UTC. Timezone-aware timestamps are converted to UTC first; naive timestamps are treated as UTC.
- Unreadable or malformed files are skipped with a printed reason so processing can continue.
- Duplicate files are currently counted separately (no reliable cross-format deduplication key is enforced by the tool).
- File processing uses a `tqdm` progress bar showing progress and ETA.
