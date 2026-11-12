from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree

from .geo import Point


class GpxError(ValueError):
    pass


def read_gpx(path: Path) -> tuple[str, list[Point]]:
    try:
        root = ElementTree.parse(path).getroot()
    except (OSError, ElementTree.ParseError) as exc:
        raise GpxError(f"Could not read GPX file: {exc}") from exc

    name = path.stem
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] == "name" and element.text:
            name = element.text.strip()
            break

    points: list[Point] = []
    for wanted in ("trkpt", "rtept"):
        found: list[Point] = []
        for element in root.iter():
            if element.tag.rsplit("}", 1)[-1] != wanted:
                continue
            try:
                found.append((float(element.attrib["lat"]), float(element.attrib["lon"])))
            except (KeyError, ValueError):
                continue
        if found:
            points = found
            break
    if len(points) < 2:
        raise GpxError("The GPX must contain at least two track or route points.")
    return name, points

