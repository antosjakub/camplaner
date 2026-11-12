# camplaner

`camplaner` reads a single-day GPX route, discovers nearby campsites and lodging
from OpenStreetMap and DATAtourisme, and writes a route-ordered Markdown report.

## Setup

Python 3.11 or newer is required.

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
```

Put the DATAtourisme key in the ignored `.env` file. The script loads it
automatically:

```bash
DATATOURISME_API_KEY=your-key-here
```

## Use

```bash
python camplan.py route.gpx --date 2026-11-13
```

This writes `route-2026-11-13.md` beside the GPX file. Results are ordered by
position along the route and include opening evidence, contact details, and a
Google Maps link for navigation and reviews.

Useful options:

```bash
python camplan.py route.gpx --date 2026-11-13 --radius-km 7 -o tomorrow.md
python camplan.py route.gpx --date 2026-11-13 --type campsites
python camplan.py route.gpx --date 2026-11-13 --type other
python camplan.py route.gpx --date 2026-11-13 --type all
python camplan.py route.gpx --date 2026-11-13 --no-datatourisme
python camplan.py route.gpx --date 2026-11-13 --no-overpass
python camplan.py route.gpx --date 2026-11-13 --format json -o results.json
python camplan.py route.gpx --date 2026-11-13 --format json -o -
python camplan.py route.gpx --date 2026-11-13 --verbose
```

`--format` selects Markdown or structured JSON. Pass `--output -` to write the
selected format to stdout. Exit code `0` means a complete result, `1` means a
partial result with source warnings, and `2` means invalid input or configuration.

The route distance is a straight-line projection onto the GPX geometry, not a
routed cycling detour. Opening information can be incomplete or stale; verify
the final choice by phone, official website, or recent Google Maps reviews.

Long routes are divided into approximately 20 km sections. The script calculates
a padded latitude/longitude rectangle for each section, queries DATAtourisme
concurrently, combines overlapping results, and applies the exact route-distance
filter locally. Overpass uses similar small rectangles as a supplemental
OpenStreetMap source. It is optional; use `--no-overpass` for a faster
DATAtourisme-only report.

`--type` controls the output category. Campsites include regular and farm
campsites. Other accommodation includes hotels, motels, hostels, guest houses,
B&Bs, chalets, apartments, group lodging, holiday centres, and resorts. The
default is `all`.

