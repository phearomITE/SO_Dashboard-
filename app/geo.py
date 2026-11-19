import json
import asyncio
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import parse_qs,urlparse
import httpx
from shapely.geometry import Point,Polygon,shape
from shapely.validation import make_valid
from app.config import DATA
from app.db import Plan,Roster,state
from sqlalchemy import select
from app.config import REVERSE_GEOCODING_ENABLED,REVERSE_GEOCODING_URL,REVERSE_GEOCODING_USER_AGENT,ROUTING_ENABLED,ROUTING_URL,ROUTING_USER_AGENT,ROUTING_TIMEOUT_SECONDS

_cache=None

# The project operates only in Cambodia.  A small margin is intentional so
# valid pins close to the national border are not rejected by rounding.
CAMBODIA_BOUNDS=(9.80,14.75,102.30,107.70)

def is_cambodia_coordinate(lat,lon):
    try:
        lat=float(lat);lon=float(lon)
    except (TypeError,ValueError):return False
    south,north,west,east=CAMBODIA_BOUNDS
    return south<=lat<=north and west<=lon<=east

def coordinates_from_text(text):
    """Read coordinates from Google Maps q/ll URLs or a plain lat,lon pair."""
    raw=str(text or '')
    for url in re.findall(r'https?://\S+',raw):
        try:
            query=parse_qs(urlparse(url.rstrip('.,)>')).query)
            pair=(query.get('q') or query.get('ll') or [''])[0]
            m=re.fullmatch(r'\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*',pair)
            if m:return valid_coordinates(float(m.group(1)),float(m.group(2)))
        except (ValueError,IndexError):pass
    # A plain pair must contain decimal precision on both values.  Requiring
    # this prevents report dates such as "09,10,2026" from becoming a fake
    # pin at 9,10 (Nigeria).
    m=re.search(r'(?<![\d.])(-?\d{1,2}\.\d{4,})\s*,\s*(-?\d{1,3}\.\d{4,})(?![\d.])',raw)
    return valid_coordinates(float(m.group(1)),float(m.group(2))) if m else None

def valid_coordinates(lat,lon):
    if -90 <= lat <= 90 and -180 <= lon <= 180:return lat,lon
    return None

def online_address(payload):
    address=payload.get('address') or {}
    def pick(*keys):return next((str(address[k]).strip() for k in keys if address.get(k)), '')
    return {
        'province':pick('state','province','city'),
        'district':pick('city_district','district','town','municipality','county'),
        'commune':pick('suburb','quarter','village','neighbourhood','hamlet'),
        'commune_code':'',
        'display_name':str(payload.get('display_name') or ''),
        'geo_source':'OpenStreetMap Nominatim',
        'geo_issue':'Online reverse geocode; no official commune boundary code',
    }

async def reverse_online(lat,lon,lock,last_request):
    if not is_cambodia_coordinate(lat,lon):return {'geo_issue':'Coordinate outside Cambodia','geo_source':'Rejected'}
    if not REVERSE_GEOCODING_ENABLED:return {'geo_issue':'Online reverse geocoding disabled'}
    async with lock:
        delay=max(0,1.05-(time.monotonic()-last_request[0]))
        if delay:await asyncio.sleep(delay)
        try:
            async with httpx.AsyncClient(timeout=15,headers={'User-Agent':REVERSE_GEOCODING_USER_AGENT}) as client:
                response=await client.get(REVERSE_GEOCODING_URL,params={'format':'jsonv2','lat':lat,'lon':lon,'zoom':18,'addressdetails':1,'accept-language':'km,en'})
                response.raise_for_status();payload=response.json()
            return online_address(payload)
        except (httpx.HTTPError,ValueError,TypeError) as exc:
            return {'geo_issue':f'Online reverse geocoding failed: {type(exc).__name__}'}
        finally:last_request[0]=time.monotonic()

async def road_distance_km(start_lat,start_lon,end_lat,end_lon,lock,last_request,include_geometry=False):
    """Return OSRM/OpenStreetMap driving distance, never a straight-line estimate."""
    if not is_cambodia_coordinate(start_lat,start_lon) or not is_cambodia_coordinate(end_lat,end_lon):
        return (None,'Coordinate outside Cambodia',[]) if include_geometry else (None,'Coordinate outside Cambodia')
    if not ROUTING_ENABLED:return (None,'Road routing disabled',[]) if include_geometry else (None,'Road routing disabled')
    async with lock:
        delay=max(0,1.0-(time.monotonic()-last_request[0]))
        if delay:await asyncio.sleep(delay)
        coordinates=f'{start_lon:.7f},{start_lat:.7f};{end_lon:.7f},{end_lat:.7f}'
        try:
            async with httpx.AsyncClient(timeout=ROUTING_TIMEOUT_SECONDS,headers={'User-Agent':ROUTING_USER_AGENT}) as client:
                response=await client.get(f'{ROUTING_URL}/route/v1/driving/{coordinates}',params={'overview':'full' if include_geometry else 'false','geometries':'geojson','alternatives':'false','steps':'false'})
                response.raise_for_status();payload=response.json()
            routes=payload.get('routes') or []
            if payload.get('code')!='Ok' or not routes:return (None,'No drivable road route found',[]) if include_geometry else (None,'No drivable road route found')
            meters=float(routes[0]['distance'])
            if meters<0:return (None,'Road routing returned an invalid distance',[]) if include_geometry else (None,'Road routing returned an invalid distance')
            result=(round(meters/1000,2),'')
            if not include_geometry:return result
            raw=(routes[0].get('geometry') or {}).get('coordinates') or []
            geometry=[[round(float(x[0]),7),round(float(x[1]),7)] for x in raw if isinstance(x,(list,tuple)) and len(x)>=2]
            return result+(geometry,)
        except (httpx.HTTPError,ValueError,TypeError,KeyError) as exc:
            result=(None,f'Road routing failed: {type(exc).__name__}')
            return result+([],) if include_geometry else result
        finally:last_request[0]=time.monotonic()

def load_boundaries():
    global _cache
    files=sorted((DATA/'boundaries').glob('*.kml'))+sorted((DATA/'boundaries').glob('*.geojson'))
    fingerprint=tuple((str(p),p.stat().st_mtime_ns) for p in files)
    if _cache and _cache[0]==fingerprint:return _cache[1]
    out=[]
    for p in files:
        if p.suffix=='.geojson':
            for f in json.loads(p.read_text(encoding='utf-8'))['features']:out.append((shape(f['geometry']),f.get('properties',{})))
        else:
            root=ET.parse(p).getroot();ns={'k':'http://www.opengis.net/kml/2.2'}
            for pm in root.findall('.//k:Placemark',ns):
                props={x.attrib['name']:(x.findtext('k:value',default='',namespaces=ns)) for x in pm.findall('.//k:Data',ns)}
                props.update({x.attrib['name']:x.text or '' for x in pm.findall('.//k:SimpleData',ns)})
                props.setdefault('commune',pm.findtext('k:name',default='',namespaces=ns))
                for poly in pm.findall('.//k:Polygon',ns):
                    def coords(node):return [tuple(map(float,v.split(',')[:2])) for v in (node.text or '').split()]
                    outer=poly.find('.//k:outerBoundaryIs//k:coordinates',ns)
                    if outer is None:continue
                    holes=[coords(x) for x in poly.findall('.//k:innerBoundaryIs//k:coordinates',ns)]
                    geom=Polygon(coords(outer),holes)
                    # Operational GIS exports can contain self-intersections.
                    # Repair them for point-in-polygon lookup instead of making
                    # the whole bot fail at startup/export time.
                    if not geom.is_valid:geom=make_valid(geom)
                    if not geom.is_empty:out.append((geom,props))
            root.clear()
    _cache=(fingerprint,out);return out

def locate(lat,lon):
    if not is_cambodia_coordinate(lat,lon):return {'geo_issue':'Coordinate outside Cambodia','geo_source':'Rejected'}
    found=[]
    for geom,p in load_boundaries():
        if geom.covers(Point(lon,lat)):found.append({str(k).lower():str(v) for k,v in p.items()})
    if len(found)!=1:return {'geo_issue':'No matching boundary' if not found else 'Multiple matching boundaries'}
    p=found[0]
    def val(*keys):return next((p[k] for k in keys if p.get(k)), '')
    province_code=val('pro_code','province_code')
    province_names={'01':'Banteay Meanchey','02':'Battambang','03':'Kampong Cham','04':'Kampong Chhnang','05':'Kampong Speu','06':'Kampong Thom','07':'Kampot','08':'Kandal','09':'Koh Kong','10':'Kratie','11':'Mondulkiri','12':'Phnom Penh','13':'Preah Vihear','14':'Prey Veng','15':'Pursat','16':'Ratanakiri','17':'Siem Reap','18':'Preah Sihanouk','19':'Stung Treng','20':'Svay Rieng','21':'Takeo','22':'Oddar Meanchey','23':'Kep','24':'Pailin','25':'Tboung Khmum'}
    province=val('province','adm1_en','name_1','prov_name','province_name') or province_names.get(province_code.zfill(2),'')
    district=val('district','adm2_en','name_2','dist_name','district_name')
    if not district:
        combined=val('0001- deal')
        match=re.search(r'\[([^\]]+)\]',combined)
        district=match.group(1).strip() if match else val('dis_code','district_code')
    commune=val('commune','adm3_en','name_3','com_name','commune_name','hrname')
    return {'province':province,'province_code':province_code,'district':district,'district_code':val('dis_code','district_code'),'commune':commune,'commune_code':val('commune_code','com_code','com.code','adm3_pcode'),'geo_issue':''}

def plan_locations(s,employee,day,name=''):
    """Return the active plan rows for one employee/date, with name fallback."""
    batch=state(s,'plan_dates',{}).get(day)
    if not batch:return []
    employee=str(employee or '').strip()
    employee_ids=[employee] if employee else []
    names=[name] if name else []
    # Resolve legacy/manual identities through Telegram ID. This handles an
    # SO who was added as manual:716... before List_SO supplied 90024146.
    match=re.fullmatch(r'manual:(\d+)',employee,re.I)
    if match:
        uid=int(match.group(1))
        roster_rows=list(s.scalars(select(Roster).where(Roster.uid==uid,Roster.active==1)))
        roster_rows.sort(key=lambda r:(r.source!='excel',str(r.employee or '').startswith('manual:')))
        for roster in roster_rows:
            if roster.employee and roster.employee not in employee_ids:employee_ids.append(roster.employee)
            if roster.name and roster.name not in names:names.append(roster.name)
    rows=[]
    for employee_id in employee_ids:
        rows=list(s.scalars(select(Plan).where(Plan.batch==batch,Plan.employee==employee_id,Plan.day==day)))
        if rows:break
    if not rows and names:
        normalized_names={' '.join(str(value).casefold().split()) for value in names if value}
        candidates=list(s.scalars(select(Plan).where(Plan.batch==batch,Plan.day==day)))
        rows=[r for r in candidates if ' '.join(str(r.data.get('sale_name','')).casefold().split()) in normalized_names]
    return [dict(r.data) for r in rows]

def boundary_center(commune_code='',commune=''):
    """Return a representative point inside a planned commune boundary."""
    code=str(commune_code or '').strip().lstrip('0');name=' '.join(str(commune or '').casefold().split())
    for geom,raw in load_boundaries():
        p={str(k).lower():str(v) for k,v in raw.items()}
        candidate=next((p.get(k,'') for k in ('commune_code','com_code','com.code','adm3_pcode') if p.get(k)), '')
        candidate_name=next((p.get(k,'') for k in ('commune','adm3_en','name_3','com_name','commune_name','hrname') if p.get(k)), '')
        if (code and candidate.strip().lstrip('0')==code) or (not code and name and ' '.join(candidate_name.casefold().split())==name):
            point=geom.representative_point()
            return point.y,point.x
    return None

def plan_check(s,employee,day,geo,name=''):
    batch=state(s,'plan_dates',{}).get(day)
    if not batch:return 'Not checked','No plan for this date'
    if geo.get('geo_issue') or not (geo.get('commune_code') or geo.get('commune')):return 'Not checked',geo.get('geo_issue','Boundary missing')
    rows=plan_locations(s,employee,day,name)
    if not rows:return 'Incorrect','Employee has no scheduled commune for this date'
    def norm(v):return ' '.join(str(v or '').strip().casefold().split())
    def numeric_code(v):
        value=str(v or '').strip()
        return value.lstrip('0') if value.isdigit() else ''
    for r in rows:
        actual_code=numeric_code(geo.get('commune_code'));planned_code=numeric_code(r.get('commune_code'))
        if actual_code and planned_code and actual_code==planned_code:return 'Correct',''
        if (not actual_code or not planned_code) and norm(geo.get('commune'))==norm(r.get('commune')):return 'Correct',''
    planned=', '.join(dict.fromkeys(f"{r.get('commune','')} ({r.get('commune_code','')})" for r in rows))
    actual=f"{geo.get('commune','')} ({geo.get('commune_code','')})"
    return 'Incorrect',f'Actual commune {actual} is outside planned commune(s): {planned}'
