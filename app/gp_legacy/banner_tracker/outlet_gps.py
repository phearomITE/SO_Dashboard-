"""20 metre base-outlet lookup. Multiple base candidates never multiply a report."""
from __future__ import annotations

from collections import defaultdict
from hashlib import sha256
from itertools import product
import gzip
import json
from math import asin, cos, floor, isfinite, radians, sin, sqrt
import os
from pathlib import Path
import tempfile

from openpyxl import load_workbook

EARTH_RADIUS_M = 6371008.8
RADIUS_M = 20.0


def coordinates(latitude, longitude):
    try:
        lat, lon = float(latitude), float(longitude)
    except (ValueError, TypeError):
        return None
    if not (isfinite(lat) and isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    if lat == 0 and lon == 0:
        return None  # common missing-location placeholder
    return lat, lon


def distance_m(a, b):
    lat1, lon1, lat2, lon2 = map(radians, (*a, *b))
    h = sin((lat2-lat1)/2)**2 + cos(lat1)*cos(lat2)*sin((lon2-lon1)/2)**2
    return 2 * EARTH_RADIUS_M * asin(sqrt(min(1.0, max(0.0, h))))


def _cell(point):
    lat, lon = map(radians, point)
    return tuple(floor(v / RADIUS_M) for v in (
        EARTH_RADIUS_M*cos(lat)*cos(lon),
        EARTH_RADIUS_M*cos(lat)*sin(lon), EARTH_RADIUS_M*sin(lat),
    ))


class OutletGPSIndex:
    def __init__(self, rows):
        self.buckets = defaultdict(list)
        self.valid_rows = 0
        self.invalid_rows = 0
        for code, latitude, longitude in rows:
            point = coordinates(latitude, longitude)
            if point is None:
                self.invalid_rows += 1
                continue
            self.buckets[_cell(point)].append((str(code or ''), point))
            self.valid_rows += 1
        if not self.valid_rows:
            raise ValueError('outlet.xlsx contains no valid base GPS coordinates.')

    def match(self, latitude, longitude):
        point = coordinates(latitude, longitude)
        if point is None:
            return {'status': 'MISSING_OR_INVALID_GPS', 'matches': 0, 'code': '', 'distance_m': None}
        cell = _cell(point)
        matches = []
        for delta in product((-1, 0, 1), repeat=3):
            bucket = tuple(a+b for a, b in zip(cell, delta))
            for code, base_point in self.buckets.get(bucket, ()):
                distance = distance_m(point, base_point)
                if distance <= RADIUS_M + 1e-8:
                    matches.append((distance, code))
        if not matches:
            return {'status': 'NO_BASE_WITHIN_20M', 'matches': 0, 'code': '', 'distance_m': None}
        distance, code = min(matches)
        return {'status': 'MATCHED', 'matches': len(matches), 'code': code, 'distance_m': distance}

    @classmethod
    def from_workbook(cls, path: Path):
        path = Path(path)
        if not path.is_file():
            raise FileNotFoundError(f'Base outlet file required for # Outlets: {path}')
        # Cache only compact GPS fields. Content hash invalidates even same-size edits.
        digest = sha256()
        with path.open('rb') as source:
            for chunk in iter(lambda: source.read(1024*1024), b''):
                digest.update(chunk)
        fingerprint = digest.hexdigest()
        cache = path.with_suffix('.gps-cache.json.gz')
        try:
            with gzip.open(cache, 'rt', encoding='utf-8') as source:
                payload = json.load(source)
            if payload['version'] == 1 and payload['sha256'] == fingerprint:
                return cls(payload['rows'])
        except (OSError, ValueError, KeyError, TypeError, EOFError):
            pass
        print('Indexing outlet.xlsx GPS (first run or changed base file)...', flush=True)
        workbook = load_workbook(path, read_only=True, data_only=True)
        rows = []
        found = False
        try:
            for sheet in workbook:
                iterator = sheet.iter_rows(values_only=True)
                header = next(iterator, ())
                names = {str(v or '').strip().casefold(): i for i, v in enumerate(header)}
                if not {'latitude', 'longitude', 'outlet code'}.issubset(names):
                    continue
                found = True
                for row in iterator:
                    def value(label):
                        index = names[label]
                        return row[index] if index < len(row) else None
                    point = coordinates(value('latitude'), value('longitude'))
                    rows.append([str(value('outlet code') or ''), *(point or (None, None))])
        finally:
            workbook.close()
        if not found:
            raise ValueError('outlet.xlsx needs Outlet Code, Latitude and Longitude headers in row 1.')
        result = cls(rows)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, suffix='.tmp', delete=False) as target:
                temporary = target.name
            with gzip.open(temporary, 'wt', encoding='utf-8') as target:
                json.dump({'version': 1, 'sha256': fingerprint, 'rows': rows}, target)
            os.replace(temporary, cache)
            temporary = None
        except OSError:
            pass  # read-only template directory: correct results without disk cache
        finally:
            if temporary:
                Path(temporary).unlink(missing_ok=True)
        print(f'Base GPS indexed: {result.valid_rows} valid, {result.invalid_rows} invalid.', flush=True)
        return result
