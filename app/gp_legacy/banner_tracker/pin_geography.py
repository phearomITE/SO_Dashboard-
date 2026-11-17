"""Offline location-pin geography from the project's reference directory."""
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from openpyxl import load_workbook
from .commune import default_commune_resolver


def _code(value):
    text = str(value or '').strip()
    if text.endswith('.0'):
        text = text[:-2]
    return text.lstrip('0') or ('0' if text else '')


@dataclass(frozen=True)
class PinGeography:
    province: str = ''
    district: str = ''
    com_code: str = ''
    commune: str = ''

    def values(self):
        return [self.province, self.district, self.com_code, self.commune]


class ReferencePinGeography:
    def __init__(self, project_root):
        root = Path(project_root).resolve()
        self.boundaries = default_commune_resolver(root)
        if not self.boundaries.available:
            raise FileNotFoundError('No usable commune boundaries in reference/Map_KML.kml or reference/CambodiaCommuneBoundaries.geojson')
        path = root/'reference/Location_All.xlsx'
        if not path.is_file():
            raise FileNotFoundError(f'Location name reference missing: {path}')
        self.communes, self.provinces, self.districts = {}, {}, {}
        workbook = load_workbook(path, read_only=True, data_only=True)
        found = False
        try:
            for sheet in workbook:
                rows = sheet.iter_rows(values_only=True)
                headers = {str(v or '').strip().casefold():i for i,v in enumerate(next(rows,()))}
                if not {'com_code','pro_name','dis_name','com_name'} <= headers.keys():
                    continue
                found = True
                for row in rows:
                    def get(name):
                        i = headers.get(name)
                        return row[i] if i is not None and i<len(row) else None
                    code = str(get('com_code') or '').strip()
                    if not code:
                        continue
                    if code.endswith('.0'):code=code[:-2]
                    place = PinGeography(str(get('pro_name') or '').strip(),
                        str(get('dis_name') or '').strip(),code,str(get('com_name') or '').strip())
                    key = _code(code)
                    previous = self.communes.get(key)
                    if previous and previous.values()[0:2]+previous.values()[3:] != place.values()[0:2]+place.values()[3:]:
                        raise ValueError(f'Conflicting administrative names for commune {code} in {path.name}')
                    self.communes[key] = place
                    self.provinces[_code(get('pro_code'))] = place.province
                    self.districts[_code(get('dis_code'))] = place.district
        finally:
            workbook.close()
        if not found or not self.communes:
            raise ValueError('Location_All.xlsx needs COM_Code, PRO_Name, DIS_Name and COM_Name headers')

    def resolve(self, latitude, longitude):
        commune = self.boundaries.resolve(latitude,longitude)
        if commune is None:
            return PinGeography()
        known = self.communes.get(_code(commune.code))
        if known:
            return known
        # Keep a known boundary code/name even if the name workbook lacks it.
        province = self.provinces.get(_code(commune.province),'')
        district = self.districts.get(_code(commune.code[:-2]),'')
        return PinGeography(province,district,commune.code,commune.name)


@lru_cache(maxsize=8)
def default_pin_geography(project_root=None):
    root = Path(project_root) if project_root is not None else Path(__file__).resolve().parents[1]
    return ReferencePinGeography(root)
