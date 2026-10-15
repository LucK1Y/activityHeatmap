#!/usr/bin/env python3
"""Generate yearly activity heatmaps from GPX and FIT files."""

from __future__ import annotations

import argparse
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Iterable, Sequence

import calmap
import gpxpy
import matplotlib.pyplot as plt
import pandas as pd
from fitparse import FitFile
from tqdm import tqdm

SUPPORTED_SUFFIXES = {".gpx", ".fit"}


@dataclass
class ParsedActivity:
    """Distance and calendar date for one activity file."""

    source: Path
    day: date
    distance_km: float


class ActivityParseError(Exception):
    """Raised when an activity file cannot be parsed safely."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build yearly GitHub-style heatmaps from GPX/FIT activity files.",
    )
    parser.add_argument("path", help="Path to a GPX/FIT file or a directory containing them")
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Optional directory where generated PNG heatmaps are written",
    )
    return parser.parse_args()


def to_calendar_date(timestamp: datetime) -> date:
    """Convert timestamps to a day key in UTC.

    Timezone-aware timestamps are converted to UTC first. Naive timestamps are treated as UTC.
    """

    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    return timestamp.astimezone(UTC).date()


def semicircles_to_degrees(value: float) -> float:
    return value * (180.0 / (2**31))


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_km = 6371.0088
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)

    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * radius_km * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def path_candidates(path: Path) -> list[Path]:
    if path.is_file():
        return [path] if path.suffix.lower() in SUPPORTED_SUFFIXES else []
    candidates: list[Path] = []
    for candidate in path.rglob("*"):
        if candidate.is_file() and candidate.suffix.lower() in SUPPORTED_SUFFIXES:
            candidates.append(candidate)
    return sorted(candidates)


def calculate_distance_from_points(points: Sequence[tuple[float, float]]) -> float:
    if len(points) < 2:
        return 0.0
    distance_km = 0.0
    for (lat1, lon1), (lat2, lon2) in zip(points, points[1:]):
        distance_km += haversine_km(lat1, lon1, lat2, lon2)
    return distance_km


def parse_gpx(path: Path) -> ParsedActivity:
    try:
        with path.open("r", encoding="utf-8") as handle:
            gpx = gpxpy.parse(handle)
    except Exception as exc:  # noqa: BLE001
        raise ActivityParseError(f"failed to parse GPX: {exc}") from exc

    try:
        gpx.smooth(vertical=False, horizontal=True, remove_extremes=True)
    except Exception:
        # Continue with raw points if smoothing cannot be applied.
        pass

    start_time = gpx.get_time_bounds().start_time or gpx.time
    if start_time is None:
        raise ActivityParseError("no timestamp found in GPX track")

    distance_m = gpx.length_2d() or 0.0
    distance_km = distance_m / 1000.0
    if distance_km <= 0:
        points: list[tuple[float, float]] = []
        for track in gpx.tracks:
            for segment in track.segments:
                for point in segment.points:
                    points.append((point.latitude, point.longitude))
        distance_km = calculate_distance_from_points(points)

    if distance_km <= 0:
        raise ActivityParseError("could not calculate positive distance from GPX track points")

    return ParsedActivity(source=path, day=to_calendar_date(start_time), distance_km=distance_km)


def parse_fit(path: Path) -> ParsedActivity:
    try:
        fit = FitFile(str(path))
    except Exception as exc:  # noqa: BLE001
        raise ActivityParseError(f"failed to open FIT: {exc}") from exc

    recorded_distance_m: list[float] = []
    coordinates: list[tuple[float, float]] = []
    first_timestamp: datetime | None = None

    try:
        for message in fit.get_messages():
            values = message.get_values()

            raw_timestamp = values.get("timestamp") or values.get("start_time")
            if first_timestamp is None and isinstance(raw_timestamp, datetime):
                first_timestamp = raw_timestamp

            for key in ("distance", "total_distance"):
                value = values.get(key)
                if isinstance(value, (int, float)) and value > 0:
                    recorded_distance_m.append(float(value))

            lat_raw = values.get("position_lat")
            lon_raw = values.get("position_long")
            if isinstance(lat_raw, (int, float)) and isinstance(lon_raw, (int, float)):
                coordinates.append((semicircles_to_degrees(float(lat_raw)), semicircles_to_degrees(float(lon_raw))))
    except Exception as exc:  # noqa: BLE001
        raise ActivityParseError(f"failed to read FIT messages: {exc}") from exc

    if first_timestamp is None:
        raise ActivityParseError("no timestamp found in FIT data")

    if recorded_distance_m:
        distance_km = max(recorded_distance_m) / 1000.0
    else:
        distance_km = calculate_distance_from_points(coordinates)

    if distance_km <= 0:
        raise ActivityParseError("could not calculate positive distance from FIT data")

    return ParsedActivity(source=path, day=to_calendar_date(first_timestamp), distance_km=distance_km)


def parse_activity(path: Path) -> ParsedActivity:
    suffix = path.suffix.lower()
    if suffix == ".gpx":
        return parse_gpx(path)
    if suffix == ".fit":
        return parse_fit(path)
    raise ActivityParseError(f"unsupported extension: {path.suffix}")


def aggregate_daily_distances(activities: Iterable[ParsedActivity]) -> dict[date, float]:
    totals: dict[date, float] = defaultdict(float)
    for activity in activities:
        totals[activity.day] += activity.distance_km
    return dict(totals)


def render_yearly_heatmaps(daily_totals: dict[date, float], output_dir: Path) -> list[Path]:
    if not daily_totals:
        return []

    index = pd.to_datetime(list(daily_totals.keys()))
    values = list(daily_totals.values())
    series = pd.Series(values, index=index).sort_index()

    max_distance = max(values)
    created_files: list[Path] = []

    for year in sorted({d.year for d in daily_totals}):
        fig, ax = plt.subplots(figsize=(16, 3))
        calmap.yearplot(
            series,
            year=year,
            how=None,
            ax=ax,
            cmap="YlGn",
            fillcolor="#ebedf0",
            linewidth=0.8,
            linecolor="white",
            vmin=0,
            vmax=max_distance,
        )
        if ax.collections:
            color_mesh = ax.collections[-1]
            fig.colorbar(color_mesh, ax=ax, orientation="horizontal", pad=0.08, label="Distance (km)")
        ax.set_title(f"Activity Heatmap {year}")

        output_path = output_dir / f"activity-heatmap-{year}.png"
        fig.savefig(output_path, dpi=150, bbox_inches="tight")
        plt.close(fig)
        created_files.append(output_path)

    return created_files


def main() -> int:
    args = parse_args()
    target = Path(args.path).expanduser().resolve()

    if not target.exists():
        print(f"Error: path does not exist: {target}")
        return 2

    if not target.is_file() and not target.is_dir():
        print(f"Error: path must be a file or directory: {target}")
        return 2

    output_dir_override: Path | None = None
    if args.output_dir is not None:
        output_dir_override = Path(args.output_dir).expanduser().resolve()
        try:
            output_dir_override.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            print(f"Error: could not create output directory {output_dir_override}: {exc}")
            return 2
        if not output_dir_override.is_dir():
            print(f"Error: output directory path is not a directory: {output_dir_override}")
            return 2

    candidates = path_candidates(target)
    if not candidates:
        print(f"No supported files found at: {target}")
        print("Supported extensions: .gpx, .fit")
        return 1

    parsed: list[ParsedActivity] = []
    skipped: list[tuple[Path, str]] = []

    for candidate in tqdm(candidates, desc="Processing activities", unit="file"):
        try:
            parsed.append(parse_activity(candidate))
        except ActivityParseError as exc:
            skipped.append((candidate, str(exc)))

    if skipped:
        print("Skipped files:")
        for path, reason in skipped:
            print(f"  - {path}: {reason}")

    daily_totals = aggregate_daily_distances(parsed)
    output_dir = output_dir_override or (target if target.is_dir() else target.parent)
    output_files = render_yearly_heatmaps(daily_totals, output_dir)

    total_km = sum(activity.distance_km for activity in parsed)
    print("Summary:")
    print(f"  Files discovered: {len(candidates)}")
    print(f"  Files processed: {len(parsed)}")
    print(f"  Files skipped: {len(skipped)}")
    print(f"  Total distance: {total_km:.2f} km")
    if output_files:
        print("  Heatmaps:")
        for output_file in output_files:
            print(f"    - {output_file}")
    else:
        print("  Heatmaps: none (no valid activities)")

    return 0 if parsed else 1


if __name__ == "__main__":
    raise SystemExit(main())
