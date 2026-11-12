from __future__ import annotations

from math import asin, cos, degrees, radians, sin, sqrt

Point = tuple[float, float]  # latitude, longitude
EARTH_RADIUS_KM = 6371.0088


def haversine_km(a: Point, b: Point) -> float:
    lat1, lon1 = map(radians, a)
    lat2, lon2 = map(radians, b)
    dlat, dlon = lat2 - lat1, lon2 - lon1
    value = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_KM * asin(sqrt(value))


def cumulative_distances(points: list[Point]) -> list[float]:
    result = [0.0]
    for previous, current in zip(points, points[1:]):
        result.append(result[-1] + haversine_km(previous, current))
    return result


def _local_xy(point: Point, origin: Point) -> tuple[float, float]:
    lat, lon = point
    lat0, lon0 = origin
    x = radians(lon - lon0) * EARTH_RADIUS_KM * cos(radians((lat + lat0) / 2))
    y = radians(lat - lat0) * EARTH_RADIUS_KM
    return x, y


def locate_on_route(point: Point, route: list[Point]) -> tuple[float, float]:
    """Return (kilometres along route, straight-line distance from route)."""
    if len(route) < 2:
        return 0.0, haversine_km(point, route[0])
    cumulative = cumulative_distances(route)
    best_offset = float("inf")
    best_progress = 0.0
    for index, (start, end) in enumerate(zip(route, route[1:])):
        ax, ay = _local_xy(start, point)
        bx, by = _local_xy(end, point)
        dx, dy = bx - ax, by - ay
        denominator = dx * dx + dy * dy
        fraction = 0.0 if denominator == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / denominator))
        px, py = ax + fraction * dx, ay + fraction * dy
        offset = sqrt(px * px + py * py)
        if offset < best_offset:
            best_offset = offset
            best_progress = cumulative[index] + fraction * (cumulative[index + 1] - cumulative[index])
    return best_progress, best_offset


def expanded_bbox(points: list[Point], radius_km: float) -> tuple[float, float, float, float]:
    min_lat = min(point[0] for point in points)
    max_lat = max(point[0] for point in points)
    min_lon = min(point[1] for point in points)
    max_lon = max(point[1] for point in points)
    middle_lat = (min_lat + max_lat) / 2
    lat_pad = degrees(radius_km / EARTH_RADIUS_KM)
    lon_pad = degrees(radius_km / (EARTH_RADIUS_KM * max(0.1, cos(radians(middle_lat)))))
    return max_lat + lat_pad, min_lon - lon_pad, min_lat - lat_pad, max_lon + lon_pad


def simplify_for_query(points: list[Point], max_points: int = 220) -> list[Point]:
    if len(points) <= max_points:
        return points
    step = (len(points) - 1) / (max_points - 1)
    selected = [points[round(index * step)] for index in range(max_points)]
    selected[-1] = points[-1]
    return selected


def split_route(points: list[Point], segment_km: float = 20.0) -> list[list[Point]]:
    """Split a route into distance-based sections sharing their boundary point."""
    if len(points) < 2:
        return [points]
    chunks: list[list[Point]] = []
    current = [points[0]]
    distance = 0.0
    for previous, point in zip(points, points[1:]):
        distance += haversine_km(previous, point)
        current.append(point)
        if distance >= segment_km:
            chunks.append(current)
            current = [point]
            distance = 0.0
    if len(current) > 1:
        chunks.append(current)
    elif not chunks:
        chunks.append(current)
    return chunks

