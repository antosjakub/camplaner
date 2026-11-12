from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import date
from pathlib import Path
from urllib.error import HTTPError, URLError

from .geo import locate_on_route
from .gpx import GpxError, read_gpx
from .render import json_report, markdown_report
from .sources import fetch_datatourisme, fetch_overpass, merge_places


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="List accommodation near a GPX route.")
    result.add_argument("gpx", type=Path, help="single-day GPX track or route")
    result.add_argument("--date", required=True, type=date.fromisoformat, dest="travel_date", help="travel date (YYYY-MM-DD)")
    result.add_argument("-o", "--output", type=Path, help="output path, or - for stdout")
    result.add_argument(
        "--format", choices=("markdown", "json"), default="markdown",
        help="output format (default: markdown)",
    )
    result.add_argument("--radius-km", type=float, default=10.0, help="maximum straight-line distance from route (default: 10)")
    result.add_argument(
        "--type", choices=("campsites", "other", "all"), default="all",
        help="accommodation category to list (default: all)",
    )
    result.add_argument("--no-datatourisme", action="store_true", help="skip DATAtourisme")
    result.add_argument("--no-overpass", action="store_true", help="skip OpenStreetMap/Overpass")
    result.add_argument("--verbose", action="store_true", help="print source counts and stage timings to stderr")
    return result


def _load_dotenv() -> Path | None:
    project_root = Path(__file__).resolve().parents[2]
    candidates = [Path.cwd() / ".env", project_root / ".env"]
    seen: set[Path] = set()
    for path in candidates:
        path = path.resolve()
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].lstrip()
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            if key:
                os.environ.setdefault(key, value)
        return path
    return None


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    started = time.perf_counter()

    def verbose(message: str) -> None:
        if args.verbose:
            print(message, file=sys.stderr)

    if args.radius_km <= 0:
        print("error: --radius-km must be positive", file=sys.stderr)
        return 2
    if args.no_overpass and args.no_datatourisme:
        print("error: both accommodation sources are disabled", file=sys.stderr)
        return 2
    env_path = _load_dotenv()
    if env_path:
        verbose(f"Loaded configuration from {env_path}")
    api_key = os.environ.get("DATATOURISME_API_KEY", "")
    if not args.no_datatourisme and not api_key:
        print(
            "error: DATATOURISME_API_KEY is required; add it to .env or the environment",
            file=sys.stderr,
        )
        return 2
    stage = time.perf_counter()
    try:
        title, route = read_gpx(args.gpx)
    except GpxError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    verbose(f"GPX: {len(route)} points read in {time.perf_counter() - stage:.2f}s")
    suffix = "json" if args.format == "json" else "md"
    output = args.output or args.gpx.with_name(f"{args.gpx.stem}-{args.travel_date.isoformat()}.{suffix}")
    places = []
    errors: list[str] = []

    if not args.no_overpass:
        stage = time.perf_counter()
        try:
            discovered = fetch_overpass(route, args.radius_km, args.type)
            places.extend(discovered)
            verbose(f"Overpass: {len(discovered)} records in {time.perf_counter() - stage:.2f}s")
        except (HTTPError, URLError, TimeoutError, ValueError) as exc:
            errors.append(f"OpenStreetMap/Overpass failed: {exc}")
            verbose(f"Overpass failed after {time.perf_counter() - stage:.2f}s: {exc}")

    if not args.no_datatourisme:
        stage = time.perf_counter()
        try:
            discovered = fetch_datatourisme(route, args.radius_km, api_key, args.type)
            places.extend(discovered)
            verbose(f"DATAtourisme: {len(discovered)} records in {time.perf_counter() - stage:.2f}s")
        except (HTTPError, URLError, TimeoutError, ValueError) as exc:
            errors.append(f"DATAtourisme failed: {exc}")
            verbose(f"DATAtourisme failed after {time.perf_counter() - stage:.2f}s: {exc}")

    stage = time.perf_counter()
    filtered = []
    for place in merge_places(places):
        category_selected = args.type == "all" or place.category == args.type
        if not category_selected:
            continue
        place.route_km, place.offset_km = locate_on_route((place.latitude, place.longitude), route)
        if place.offset_km <= args.radius_km:
            filtered.append(place)
    filtered.sort(key=lambda place: (place.route_km, place.offset_km, place.name.casefold()))
    verbose(f"Merge and route filtering: {len(filtered)} results in {time.perf_counter() - stage:.2f}s")
    if args.format == "json":
        content = json_report(title, route, filtered, args.travel_date, args.radius_km, errors)
    else:
        content = markdown_report(title, route, filtered, args.travel_date, args.radius_km, errors)
    if str(output) == "-":
        sys.stdout.write(content)
    else:
        output.write_text(content, encoding="utf-8")
        print(f"Wrote {len(filtered)} results to {output}")
    verbose(f"Total: {time.perf_counter() - started:.2f}s")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())

