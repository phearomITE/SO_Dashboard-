from __future__ import annotations

import json
import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class Commune:
    code: str
    name: str
    district: str = ""
    province: str = ""


def _ring_contains(point_x: float, point_y: float, ring: list[list[float]]) -> bool:
    inside = False
    if len(ring) < 3:
        return False
    previous = ring[-1]
    for current in ring:
        x1, y1 = float(previous[0]), float(previous[1])
        x2, y2 = float(current[0]), float(current[1])
        if (y1 > point_y) != (y2 > point_y):
            cross_x = (x2 - x1) * (point_y - y1) / (y2 - y1) + x1
            if point_x < cross_x:
                inside = not inside
        previous = current
    return inside


def _polygon_contains(point_x: float, point_y: float, polygon) -> bool:
    if not polygon or not _ring_contains(point_x, point_y, polygon[0]):
        return False
    return not any(_ring_contains(point_x, point_y, hole) for hole in polygon[1:])


def _all_points(geometry: dict) -> Iterable[list[float]]:
    coordinates = geometry.get("coordinates") or []
    polygons = coordinates if geometry.get("type") == "MultiPolygon" else [coordinates]
    for polygon in polygons:
        for ring in polygon:
            yield from ring


class CommuneResolver:
    """Fast offline lookup against Cambodia commune polygons.

    ``Map_KML.kml`` is the authoritative project source.  GeoJSON remains
    supported for older installations and small unit-test fixtures.
    """

    def __init__(self, geojson_path: Path, *, grid_size: float = 0.25):
        self.path = Path(geojson_path)
        self.grid_size = float(grid_size)
        self.features: list[tuple[Commune, dict, tuple[float, float, float, float]]] = []
        self.grid: dict[tuple[int, int], list[int]] = {}
        if not self.path.is_file():
            return
        source_features = (
            self._load_kml_features()
            if self.path.suffix.casefold() == ".kml"
            else self._load_geojson_features()
        )
        for commune, geometry in source_features:
            points = list(_all_points(geometry))
            if not points:
                continue
            xs = [float(point[0]) for point in points]
            ys = [float(point[1]) for point in points]
            bbox = (min(xs), min(ys), max(xs), max(ys))
            index = len(self.features)
            self.features.append((commune, geometry, bbox))
            min_x, min_y, max_x, max_y = bbox
            for grid_x in range(math.floor(min_x / self.grid_size), math.floor(max_x / self.grid_size) + 1):
                for grid_y in range(math.floor(min_y / self.grid_size), math.floor(max_y / self.grid_size) + 1):
                    self.grid.setdefault((grid_x, grid_y), []).append(index)

    def _load_geojson_features(self) -> list[tuple[Commune, dict]]:
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        result: list[tuple[Commune, dict]] = []
        for feature in payload.get("features", []):
            geometry = feature.get("geometry") or {}
            if geometry.get("type") not in {"Polygon", "MultiPolygon"}:
                continue
            properties = feature.get("properties") or {}
            result.append((Commune(
                code=str(properties.get("ADM3_PCODE") or properties.get("commune_code") or "").strip(),
                name=str(properties.get("ADM3_EN") or properties.get("commune_name") or "").strip(),
                district=str(properties.get("ADM2_EN") or "").strip(),
                province=str(properties.get("ADM1_EN") or "").strip(),
            ), geometry))
        return result

    @staticmethod
    def _clean_code(value: object) -> str:
        text = str(value or "").strip()
        return text[:-2] if text.endswith(".0") else text

    @staticmethod
    def _coordinate_ring(text: str | None) -> list[list[float]]:
        ring: list[list[float]] = []
        for item in str(text or "").split():
            parts = item.split(",")
            if len(parts) < 2:
                continue
            try:
                ring.append([float(parts[0]), float(parts[1])])
            except ValueError:
                continue
        return ring

    @staticmethod
    def _local_district_name(value: str) -> str:
        match = re.search(r"\[([^\]]+)\]", value or "")
        return match.group(1).strip() if match else ""

    def _load_kml_features(self) -> list[tuple[Commune, dict]]:
        result: list[tuple[Commune, dict]] = []
        for _event, placemark in ET.iterparse(self.path, events=("end",)):
            if not placemark.tag.endswith("}Placemark") and placemark.tag != "Placemark":
                continue
            properties = {
                str(item.attrib.get("name") or "").strip(): str(item.text or "").strip()
                for item in placemark.findall(".//{*}SimpleData")
            }
            polygons: list[list[list[list[float]]]] = []
            for polygon in placemark.findall(".//{*}Polygon"):
                outer = polygon.find("./{*}outerBoundaryIs/{*}LinearRing/{*}coordinates")
                outer_ring = self._coordinate_ring(outer.text if outer is not None else "")
                if len(outer_ring) < 3:
                    continue
                rings = [outer_ring]
                for inner in polygon.findall("./{*}innerBoundaryIs/{*}LinearRing/{*}coordinates"):
                    inner_ring = self._coordinate_ring(inner.text)
                    if len(inner_ring) >= 3:
                        rings.append(inner_ring)
                polygons.append(rings)
            if polygons:
                geometry = {
                    "type": "Polygon" if len(polygons) == 1 else "MultiPolygon",
                    "coordinates": polygons[0] if len(polygons) == 1 else polygons,
                }
                district_code = self._clean_code(properties.get("DIS_CODE"))
                province_code = self._clean_code(properties.get("PRO_CODE"))
                result.append((Commune(
                    code=self._clean_code(properties.get("COM_CODE")),
                    name=str(properties.get("HRName") or "").strip(),
                    district=(
                        self._local_district_name(properties.get("0001- DEAL", ""))
                        or district_code
                    ),
                    province=province_code,
                ), geometry))
            placemark.clear()
        return result

    @property
    def available(self) -> bool:
        return bool(self.features)

    @lru_cache(maxsize=100_000)
    def _resolve_cached(self, latitude: float, longitude: float) -> Commune | None:
        x = float(longitude)
        y = float(latitude)
        cell = (math.floor(x / self.grid_size), math.floor(y / self.grid_size))
        for index in self.grid.get(cell, []):
            commune, geometry, bbox = self.features[index]
            min_x, min_y, max_x, max_y = bbox
            if not (min_x <= x <= max_x and min_y <= y <= max_y):
                continue
            coordinates = geometry.get("coordinates") or []
            polygons = coordinates if geometry.get("type") == "MultiPolygon" else [coordinates]
            if any(_polygon_contains(x, y, polygon) for polygon in polygons):
                return commune
        return None

    def resolve(self, latitude: object, longitude: object) -> Commune | None:
        try:
            latitude_value = round(float(latitude), 6)
            longitude_value = round(float(longitude), 6)
        except (TypeError, ValueError):
            return None
        if not (-90 <= latitude_value <= 90 and -180 <= longitude_value <= 180):
            return None
        return self._resolve_cached(latitude_value, longitude_value)


@lru_cache(maxsize=8)
def default_commune_resolver(project_root: Path) -> CommuneResolver:
    """Reuse one parsed Cambodia boundary index across all exports in a run."""
    reference = Path(project_root).resolve() / "reference"
    kml_path = reference / "Map_KML.kml"
    return CommuneResolver(
        kml_path if kml_path.is_file() else reference / "CambodiaCommuneBoundaries.geojson"
    )


def commune_identity(commune: Commune | None) -> str:
    if commune is None:
        return ""
    return commune.code or "|".join(
        part.casefold() for part in (commune.province, commune.district, commune.name)
    )


def district_identity(commune: Commune | None) -> str:
    if commune is None or not commune.district:
        return ""
    return "|".join(
        part.casefold() for part in (commune.province, commune.district) if part
    )
