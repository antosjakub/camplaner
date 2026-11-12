# Repository guidance

This repository contains a dependency-free Python CLI that discovers accommodation
near a single-day GPX route using DATAtourisme and optionally OpenStreetMap Overpass.
It also contains a private Telegram bot that accepts GPX documents and returns the
CLI's Markdown report.

## Running the tool

The DATAtourisme key belongs in the ignored `.env` file as
`DATATOURISME_API_KEY=...`. Never print, commit, or copy this value into logs.
The CLI loads `.env` automatically without an external package.

Prefer machine-readable output when using the result in another agent step:

```bash
python camplan.py route.gpx --date YYYY-MM-DD --type campsites --no-overpass --format json --output -
```

Valid accommodation types are `campsites`, `other`, and `all`. Use `--no-overpass`
when only the official DATAtourisme source is required or public Overpass servers
are slow. A file output is safer than stdout when the result is large.

Exit codes are part of the interface:

- `0`: complete result without source warnings
- `1`: partial result; inspect the JSON `warnings` field or Markdown warnings section
- `2`: invalid arguments, input, or configuration

## Development

- Support Python 3.11 or newer and prefer the standard library.
- Preserve stable JSON field names and CLI flags unless a breaking change is intended.
- Treat missing opening information as unknown, never as closed.
- Keep API keys and personal GPX files out of version control.
- Run the complete test suite after changes:

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

Run the Telegram bot from this directory with `python telegram_bot.py`. Its token
and chat allowlist belong only in `.env`. Keep the bot private by preserving the
`TELEGRAM_ALLOWED_CHAT_IDS` authorization check and temporary-file cleanup.

