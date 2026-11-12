from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path
from urllib.error import HTTPError, URLError

from .geo import locate_on_route
from .gpx import GpxError, read_gpx
from .render import render_report
from .sources import fetch_datatourisme, fetch_overpass, merge_places


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Write a Markdown list of accommodation near a GPX route.")
    result.add_argument("gpx", type=Path, help="single-day GPX track or route")
    result.add_argument("--date", required=True, type=date.fromisoformat, dest="travel_date", help="travel date (YYYY-MM-DD)")
    result.add_argument("-o", "--output", type=Path, help="output Markdown path")
    result.add_argument("--radius-km", type=float, default=10.0, help="maximum straight-line distance from route (default: 10)")
    result.add_argument(
        "--type", choices=("campsites", "other", "all"), default="all",
        help="accommodation category to list (default: all)",
    )
    result.add_argument("--no-datatourisme", action="store_true", help="skip DATAtourisme")
    result.add_argument("--no-overpass", action="store_true", help="skip OpenStreetMap/Overpass")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.radius_km <= 0:
        print("error: --radius-km must be positive", file=sys.stderr)
        return 2
    try:
        title, route = read_gpx(args.gpx)
    except GpxError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    output = args.output or args.gpx.with_name(f"{args.gpx.stem}-{args.travel_date.isoformat()}.md")
    places = []
    errors: list[str] = []

    if not args.no_overpass:
        try:
            places.extend(fetch_overpass(route, args.radius_km))
        except (HTTPError, URLError, TimeoutError, ValueError) as exc:
            errors.append(f"OpenStreetMap/Overpass failed: {exc}")

    if not args.no_datatourisme:
        api_key = os.environ.get("DATATOURISME_API_KEY", "")
        if not api_key:
            errors.append("DATAtourisme skipped because DATATOURISME_API_KEY is not set.")
        else:
            try:
                places.extend(fetch_datatourisme(route, args.radius_km, api_key))
            except (HTTPError, URLError, TimeoutError, ValueError) as exc:
                errors.append(f"DATAtourisme failed: {exc}")

    filtered = []
    for place in merge_places(places):
        place.route_km, place.offset_km = locate_on_route((place.latitude, place.longitude), route)
        category_selected = args.type == "all" or place.category == args.type
        if place.offset_km <= args.radius_km and category_selected:
            filtered.append(place)
    filtered.sort(key=lambda place: (place.route_km, place.offset_km, place.name.casefold()))
    render_report(title, route, filtered, args.travel_date, args.radius_km, errors, output)
    print(f"Wrote {len(filtered)} results to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

