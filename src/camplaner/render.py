from __future__ import annotations

from datetime import date
from pathlib import Path
from urllib.parse import quote, urlencode

from .geo import cumulative_distances
from .models import Place
from .sources import opening_status


def _escape(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ").strip()


def maps_url(place: Place) -> str:
    query = f"{place.name} {place.latitude:.6f},{place.longitude:.6f}"
    return "https://www.google.com/maps/search/?" + urlencode({"api": 1, "query": query})


def render_report(
    title: str, route: list[tuple[float, float]], places: list[Place], travel_date: date,
    radius_km: float, errors: list[str], output: Path,
) -> None:
    lines = [
        f"# Accommodation along {title}", "",
        f"- **Date:** {travel_date.isoformat()}",
        f"- **Route length:** {cumulative_distances(route)[-1]:.1f} km",
        f"- **Maximum straight-line distance from route:** {radius_km:g} km",
        f"- **Results:** {len(places)}", "",
        "> Distances from the route are straight-line estimates, not riding detours. “Open” only means that a published period includes the requested date. Call or check recent Google Maps reviews before relying on a result.", "",
    ]
    if errors:
        lines.extend(["## Source warnings", ""] + [f"- {_escape(error)}" for error in errors] + [""])
    lines.extend(["## Results", ""])
    if not places:
        lines.extend(["No matching accommodation was found.", ""])
    headings = (("campsites", "Campsites"), ("other", "Other accommodation"))
    result_number = 0
    for category, heading in headings:
        grouped = [place for place in places if place.category == category]
        if not grouped:
            continue
        lines.extend([f"### {heading}", ""])
        for place in grouped:
            result_number += 1
            status, evidence = opening_status(place, travel_date)
            lines.extend([
                f"#### {result_number}. {_escape(place.name)} — {status}", "",
                f"- **Route position:** {place.route_km:.1f} km",
                f"- **Distance from route:** {place.offset_km:.1f} km",
                f"- **Type:** {_escape(place.kind)}",
                f"- **Opening evidence:** {_escape(evidence)}",
                f"- **Sources:** {', '.join(sorted(place.sources))}",
            ])
            if place.address:
                lines.append(f"- **Address:** {_escape(place.address)}")
            if place.last_update:
                lines.append(f"- **Source updated:** {_escape(place.last_update)}")
            links = [f"[Google Maps and reviews]({maps_url(place)})"]
            if place.website:
                links.append(f"[Official website]({place.website})")
            if place.phone:
                phone_link = quote(place.phone, safe="+")
                links.append(f"[Call {place.phone}](tel:{phone_link})")
            links.append(f"[Coordinates](geo:{place.latitude:.6f},{place.longitude:.6f})")
            lines.extend([f"- **Links:** {' · '.join(links)}", ""])
    output.write_text("\n".join(lines), encoding="utf-8")

