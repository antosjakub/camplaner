from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from difflib import SequenceMatcher
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .geo import Point, expanded_bbox, haversine_km, simplify_for_query, split_route
from .models import Place

OVERPASS_URLS = (
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass-api.de/api/interpreter",
)
DATATOURISME_URL = "https://api.datatourisme.fr/v1/catalog"
DT_CAMP_TYPES = "Camping,FarmCamping"
DT_OTHER_TYPES = (
    "Hotel,HotelRestaurant,Hostel,BedAndBreakfast,GuestHouse,StopOverOrGroupLodge,"
    "GroupLodging,HolidayCentre,HolidayResort"
)


def _datatourisme_types(category: str) -> str:
    if category == "campsites":
        return DT_CAMP_TYPES
    if category == "other":
        return DT_OTHER_TYPES
    return f"{DT_CAMP_TYPES},{DT_OTHER_TYPES}"


def _request_json(request: Request, timeout: int = 60) -> Any:
    with urlopen(request, timeout=timeout) as response:
        return json.load(response)


def fetch_overpass(route: list[Point], radius_km: float, category: str = "all") -> list[Place]:
    camp_types = "camp_site"
    other_types = "hotel|hostel|guest_house|motel|chalet|apartment"
    tourism = camp_types if category == "campsites" else other_types if category == "other" else f"{camp_types}|{other_types}"
    query_points = simplify_for_query(route)
    chunk_size = 45
    chunks = [query_points[start:start + chunk_size] for start in range(0, len(query_points), chunk_size - 1)]
    if len(chunks) > 1 and len(chunks[-1]) == 1:
        chunks[-2].extend(chunks.pop())

    elements: dict[tuple[str, int], dict[str, Any]] = {}
    for chunk in chunks:
        north, west, south, east = expanded_bbox(chunk, radius_km)
        query = (
            f'[out:json][timeout:20];nwr["tourism"~"^({tourism})$"]'
            f"({south:.6f},{west:.6f},{north:.6f},{east:.6f});out center tags;"
        )
        last_error: Exception | None = None
        payload = None
        for endpoint in OVERPASS_URLS:
            request = Request(
                endpoint,
                data=urlencode({"data": query}).encode(),
                headers={"User-Agent": "camplaner/0.1 (personal route planning)"},
            )
            try:
                payload = _request_json(request, timeout=25)
                break
            except (OSError, ValueError) as exc:
                last_error = exc
        if payload is None:
            assert last_error is not None
            raise last_error
        for element in payload.get("elements", []):
            element_id = element.get("id")
            element_type = element.get("type", "")
            if isinstance(element_id, int):
                elements[(element_type, element_id)] = element

    places: list[Place] = []
    for element in elements.values():
        tags = element.get("tags", {})
        center = element.get("center", element)
        if "lat" not in center or "lon" not in center:
            continue
        name = tags.get("name") or tags.get("operator") or "Unnamed accommodation"
        address = ", ".join(
            value for value in (
                " ".join(filter(None, (tags.get("addr:housenumber"), tags.get("addr:street")))),
                tags.get("addr:postcode", ""), tags.get("addr:city", "")
            ) if value
        )
        opening = tags.get("opening_hours", "")
        places.append(Place(
            name=name,
            latitude=float(center["lat"]), longitude=float(center["lon"]),
            kind=tags.get("tourism", "accommodation").replace("_", " "),
            sources={"OpenStreetMap"}, address=address,
            phone=tags.get("contact:phone") or tags.get("phone", ""),
            website=tags.get("contact:website") or tags.get("website", ""),
            opening_raw=[opening] if opening else [],
            last_update=element.get("timestamp", ""),
        ))
    return places


def _first_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, dict):
        for key in ("en", "fr", "text", "label", "value", "@value"):
            if key in value:
                result = _first_text(value[key])
                if result:
                    return result
        for nested in value.values():
            result = _first_text(nested)
            if result:
                return result
    if isinstance(value, list):
        for nested in value:
            result = _first_text(nested)
            if result:
                return result
    return ""


def _walk(value: Any):
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from _walk(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _walk(nested)


def _find_first(value: Any, keys: tuple[str, ...]) -> str:
    for node in _walk(value):
        for key in keys:
            if key in node:
                result = _first_text(node[key])
                if result:
                    return result
    return ""


def _dt_place(item: dict[str, Any]) -> Place | None:
    located = item.get("isLocatedAt") or {}
    geographic_data = [located, item.get("geoPoint") or {}]
    latitude = _find_first(geographic_data, ("latitude", "lat"))
    longitude = _find_first(geographic_data, ("longitude", "lon", "lng"))
    if not latitude or not longitude:
        return None
    address = ", ".join(filter(None, (
        _find_first(located, ("streetAddress",)),
        _find_first(located, ("postalCode",)),
        _find_first(located, ("addressLocality", "hasAddressCity")),
    )))
    contact = item.get("hasContact") or {}
    website = _find_first(contact, ("homepage", "website"))
    phone = _find_first(contact, ("telephone", "phone"))
    raw: list[str] = []
    periods: list[tuple[str, str]] = []
    for node in _walk(item.get("openingHoursSpecification", [])):
        start = _first_text(node.get("validFrom") or node.get("startDate"))[:10]
        end = _first_text(node.get("validThrough") or node.get("endDate"))[:10]
        if start or end:
            periods.append((start, end))
        details = _first_text(node.get("additionalInformation") or node.get("openingDetails"))
        if details and details not in raw:
            raw.append(details)
    text_hours = _first_text(item.get("textOpeningHoursSpecification"))
    if text_hours:
        raw.append(text_hours)
    return Place(
        name=_first_text(item.get("label")) or "Unnamed accommodation",
        latitude=float(latitude), longitude=float(longitude),
        kind=_first_text(item.get("type")).replace("_", " ") or "accommodation",
        sources={"DATAtourisme"}, address=address, phone=phone, website=website,
        opening_raw=raw, opening_periods=periods,
        last_update=_first_text(item.get("lastUpdate") or item.get("lastUpdateDatatourisme"))[:10],
    )


def _fetch_datatourisme_rectangle(
    bounds: tuple[float, float, float, float], api_key: str, category: str,
) -> list[Place]:
    top, left, bottom, right = bounds
    params = {
        "filters": f"type[in]={_datatourisme_types(category)}",
        "geo_bounding": f"{top},{left},{bottom},{right}",
        "page_size": "100", "lang": "fr,en",
        "fields": (
            "uuid,label,type,isLocatedAt,hasContact,openingHoursSpecification,"
            "textOpeningHoursSpecification,lastUpdate,lastUpdateDatatourisme"
        ),
    }
    url: str | None = f"{DATATOURISME_URL}?{urlencode(params)}"
    places: list[Place] = []
    page_count = 0
    while url and page_count < 50:
        request = Request(url, headers={"X-API-Key": api_key, "User-Agent": "camplaner/0.1"})
        payload = _request_json(request)
        for item in payload.get("objects", []):
            if not isinstance(item, dict):
                continue
            place = _dt_place(item)
            if place:
                places.append(place)
        next_url = payload.get("meta", {}).get("next")
        if next_url and next_url.startswith("/"):
            next_url = "https://api.datatourisme.fr" + next_url
        elif next_url and next_url.startswith("http://api.datatourisme.fr/"):
            next_url = "https://" + next_url.removeprefix("http://")
        url = next_url
        page_count += 1
    return places


def fetch_datatourisme(
    route: list[Point], radius_km: float, api_key: str, category: str = "all",
) -> list[Place]:
    bounds = [expanded_bbox(chunk, radius_km) for chunk in split_route(route)]
    places: list[Place] = []
    # A single-day route normally creates fewer than ten sections. DATAtourisme
    # permits 20-30 concurrent requests, so eight workers keep the run to one
    # network round while remaining comfortably below that limit.
    with ThreadPoolExecutor(max_workers=min(8, len(bounds))) as executor:
        futures = [
            executor.submit(_fetch_datatourisme_rectangle, box, api_key, category)
            for box in bounds
        ]
        for future in futures:
            places.extend(future.result())
    return places


def merge_places(places: list[Place]) -> list[Place]:
    merged: list[Place] = []
    for place in places:
        normalized = re.sub(r"\W+", "", place.name.casefold())
        match = None
        for candidate in merged:
            candidate_name = re.sub(r"\W+", "", candidate.name.casefold())
            same_phone = place.phone and candidate.phone and re.sub(r"\D", "", place.phone) == re.sub(r"\D", "", candidate.phone)
            nearby = haversine_km((place.latitude, place.longitude), (candidate.latitude, candidate.longitude)) < 0.2
            similar = SequenceMatcher(None, normalized, candidate_name).ratio() >= 0.62
            if same_phone or (nearby and similar):
                match = candidate
                break
        if match is None:
            merged.append(place)
            continue
        match.sources.update(place.sources)
        for field in ("address", "phone", "website", "last_update"):
            if not getattr(match, field) and getattr(place, field):
                setattr(match, field, getattr(place, field))
        match.opening_raw.extend(value for value in place.opening_raw if value not in match.opening_raw)
        match.opening_periods.extend(value for value in place.opening_periods if value not in match.opening_periods)
        if place.category == "campsites" and match.category != "campsites":
            match.kind = place.kind
        elif match.kind == "accommodation" and place.kind != "accommodation":
            match.kind = place.kind
    return merged


MONTHS = {name: number for number, name in enumerate(
    ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), 1
)}


def opening_status(place: Place, travel_date: date) -> tuple[str, str]:
    for start_raw, end_raw in place.opening_periods:
        try:
            start = date.fromisoformat(start_raw) if start_raw else date.min
            end = date.fromisoformat(end_raw) if end_raw else date.max
        except ValueError:
            continue
        if start <= travel_date <= end:
            return "OPEN", f"Published period {start_raw or '…'} to {end_raw or '…'} includes this date."
    if place.opening_periods:
        return "CLOSED", "The requested date is outside all published opening periods."
    for value in place.opening_raw:
        clean = value.strip()
        if clean == "24/7":
            return "OPEN", "Listed as 24/7."
        if clean.casefold() in {"off", "closed"}:
            return "CLOSED", f"Listed as {clean}."
        match = re.search(r"\b(" + "|".join(MONTHS) + r")\b\s*-\s*\b(" + "|".join(MONTHS) + r")\b", clean)
        if match:
            start, end = MONTHS[match.group(1)], MONTHS[match.group(2)]
            month = travel_date.month
            included = start <= month <= end if start <= end else month >= start or month <= end
            return ("OPEN" if included else "CLOSED", f"Interpreted seasonal hours: {clean}")
    evidence = "; ".join(place.opening_raw)
    return "UNKNOWN", evidence or "No reliable opening period was published; verify before travelling."

