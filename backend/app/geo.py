"""Geography: the warning area, point-in-polygon, distances."""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from shapely.geometry import Point, shape
from shapely.prepared import prep

EARTH_RADIUS_KM = 6371.0088


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def load_feature(path: Path | str) -> dict[str, Any]:
    """Read GeoJSON (a Feature, a FeatureCollection's first feature, or a bare geometry) as a Feature."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("type") == "FeatureCollection":
        data = data["features"][0]
    if data.get("type") != "Feature":
        data = {"type": "Feature", "properties": {}, "geometry": data}
    return data


class WarningArea:
    """A warning polygon. Coordinates are GeoJSON order (lng, lat); methods take (lat, lng)."""

    def __init__(self, feature: dict[str, Any]) -> None:
        self.feature = feature
        self.geometry = shape(feature["geometry"])
        self._prepared = prep(self.geometry)
        centroid = self.geometry.centroid
        self.center = (centroid.y, centroid.x)
        # The farthest corner from the center: the scale for the "near the center" risk factor.
        self.reach_km = max(
            haversine_km(lat, lng, *self.center) for lng, lat in self.geometry.exterior.coords
        )

    @classmethod
    def load(cls, path: Path | str) -> WarningArea:
        return cls(load_feature(path))

    @property
    def title(self) -> str:
        return self.feature.get("properties", {}).get("title", "Warning")

    def contains(self, lat: float, lng: float) -> bool:
        return self._prepared.contains(Point(lng, lat))

    def distance_to_center_km(self, lat: float, lng: float) -> float:
        return haversine_km(lat, lng, *self.center)


def find_affected(households: list, area: WarningArea) -> list:
    """Every household inside the warning area (anything with .lat and .lng)."""
    return [h for h in households if area.contains(h.lat, h.lng)]
