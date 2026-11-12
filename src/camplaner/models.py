from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Place:
    name: str
    latitude: float
    longitude: float
    kind: str
    sources: set[str] = field(default_factory=set)
    address: str = ""
    phone: str = ""
    website: str = ""
    opening_raw: list[str] = field(default_factory=list)
    opening_periods: list[tuple[str, str]] = field(default_factory=list)
    last_update: str = ""
    route_km: float = 0.0
    offset_km: float = 0.0

    @property
    def category(self) -> str:
        normalized = self.kind.casefold().replace("_", " ")
        return "campsites" if "camp" in normalized else "other"

