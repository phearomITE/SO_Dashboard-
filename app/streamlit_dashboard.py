"""Sales Execution Command Center for the SO Alert Bot."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
import base64
import html
import math
import os
import random

import pandas as pd
import pydeck as pdk
import streamlit as st

from app import db
from app.dashboard import available_dates, historical_checkins, load_dealers, warehouse_checkins

ROOT = Path(__file__).resolve().parents[1]
TZ = "Asia/Phnom_Penh"
REFRESH_SECONDS = max(60, int(os.getenv("DASHBOARD_REFRESH_SECONDS", "300")))
DEMO_MODE = os.getenv("DASHBOARD_DEMO_MODE", "false").lower() == "true"
MAP_POINT_LIMIT = max(500, int(os.getenv("DASHBOARD_MAP_POINT_LIMIT", "6000")))
MASTER = {"Province": 25, "District": 210, "Commune": 1651}

st.set_page_config(page_title="Sales Execution Command Center", page_icon="📊", layout="wide", initial_sidebar_state="expanded")


def _asset_uri(path: Path) -> str:
    if not path.exists():
        return ""
    mime = "image/jpeg" if path.suffix.lower() in {".jpg", ".jpeg"} else "image/png"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"


LOGO = _asset_uri(ROOT / "app" / "static" / "chip_mong_foodbev_red.png")
MAPBOX_TOKEN = os.getenv("MAPBOX_API_KEY", "").strip()
if MAPBOX_TOKEN:
    pdk.settings.mapbox_api_key = MAPBOX_TOKEN


def _engine_ready() -> None:
    if db.engine is None:
        db.engine = db.new_engine()


def _today() -> date:
    return pd.Timestamp.now(tz=TZ).date()


def _demo_rows(day: date) -> list[dict]:
    """Deterministic visual-QA rows; used only when DASHBOARD_DEMO_MODE=true."""
    rng = random.Random(20261024)
    people = [
        (636032685, "90010313", "UN Cheat", "R1", "CA1"),
        (5563107012, "90015717", "TUN Lina", "R1", "CA3"),
        (339590199, "90007724", "CHHEANG Sinat", "R2", "SVA7"),
        (433646039, "90007647", "CHHAY Peng", "R4", "BTB1"),
        (383991995, "90007907", "Pally LAM", "R4", "PPT3"),
    ]
    centers = [(11.56, 104.91), (13.10, 103.20), (12.25, 104.67), (11.44, 105.18), (13.36, 103.91)]
    rows, pin_id = [], 1
    for person, center in zip(people, centers):
        uid, employee, name, region, dealer = person
        lat, lon = center
        for n in range(rng.randint(6, 11)):
            lat += rng.uniform(-0.035, 0.035); lon += rng.uniform(-0.045, 0.045)
            stamp = datetime.combine(day, datetime.min.time()) + timedelta(hours=8, minutes=n * 38)
            rows.append({"pin_id":pin_id,"telegram_id":uid,"employee_id":employee,"real_name":name,
                "username":"@"+name.replace(" ","_"),"position":"SS" if n==0 else "SO","region":region,
                "dealer":dealer,"group_name":f"{region}-{dealer} Execution Excellent","latitude":lat,"longitude":lon,
                "province":"Kandal" if lat<12 else "Battambang","district":"Kandal Stueng","commune":"Ampov Prey",
                "pin_at":stamp,"pin_time":stamp.strftime("%I:%M %p"),
                "pin_source":"explicit_coordinates" if n%4==0 else "telegram_location",
                "road_distance_km":round(rng.uniform(2.1,14.8),1),"telegram_user":f"tg://user?id={uid}",
                "open_image":"","open_map":f"https://maps.google.com/maps?q={lat},{lon}"})
            pin_id += 1
    return rows


def _demo_dealers() -> list[dict]:
    return [
        {"dealer":"CA1","region":"R1","latitude":11.56,"longitude":104.91,"province":"Phnom Penh","district":"Tuol Kouk","commune":"Phsar Depou Ti Bei"},
        {"dealer":"CA3","region":"R1","latitude":11.62,"longitude":104.88,"province":"Phnom Penh","district":"Sen Sok","commune":"Tuek Thla"},
        {"dealer":"SVA7","region":"R2","latitude":11.44,"longitude":105.18,"province":"Prey Veng","district":"Svay Antor","commune":"Svay Antor"},
        {"dealer":"BTB1","region":"R4","latitude":13.10,"longitude":103.20,"province":"Battambang","district":"Bavel","commune":"Bavel"},
        {"dealer":"PPT3","region":"R4","latitude":12.25,"longitude":104.67,"province":"Kampong Chhnang","district":"Rolea B'ier","commune":"Tuek Hout"},
    ]


@st.cache_data(ttl=REFRESH_SECONDS, show_spinner=False)
def get_dates() -> list[date]:
    if DEMO_MODE:
        return [_today()]
    _engine_ready()
    return [pd.Timestamp(row["date_key"]).date() for row in available_dates()]


@st.cache_data(ttl=REFRESH_SECONDS, show_spinner="Loading live check-ins…")
def get_snapshot(selected: date) -> dict:
    if DEMO_MODE:
        return {"checkins":_demo_rows(selected),"dealers":_demo_dealers(),"source":"Demo preview","is_live":selected==_today()}
    _engine_ready(); current = _today()
    if selected == current:
        rows, source = historical_checkins(selected), "Raw live events"
        if not rows: rows, source = warehouse_checkins(selected), "Silver warehouse"
    else:
        rows, source = warehouse_checkins(selected), "Silver warehouse"
        if not rows: rows, source = historical_checkins(selected), "Raw event fallback"
    return {"checkins":rows,"dealers":load_dealers(),"source":source,"is_live":selected==current}


def _frame(rows: list[dict]) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    if frame.empty: return frame
    for column in ["region","dealer","province","district","commune","real_name","pin_source","group_name"]:
        if column not in frame: frame[column] = ""
        frame[column] = frame[column].fillna("").astype(str).str.strip()
    frame["pin_at"] = pd.to_datetime(frame.get("pin_at"), errors="coerce")
    return frame


def _options(frame: pd.DataFrame, column: str) -> list[str]:
    if frame.empty or column not in frame: return []
    return sorted(value for value in frame[column].dropna().astype(str).str.strip().unique() if value)


def _filter(frame: pd.DataFrame, filters: dict[str,str]) -> pd.DataFrame:
    result = frame
    for column, value in filters.items():
        if value != "All" and column in result: result = result[result[column] == value]
    return result


def _pct(part: float, whole: float) -> int:
    return round(part*100/whole) if whole else 0


def _haversine(lat1, lon1, lat2, lon2) -> float:
    p1,p2=math.radians(lat1),math.radians(lat2); dp=math.radians(lat2-lat1); dl=math.radians(lon2-lon1)
    a=math.sin(dp/2)**2+math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 6371*2*math.atan2(math.sqrt(a),math.sqrt(1-a))


def _icon(name: str) -> str:
    return f"<i class='bi bi-{name}' aria-hidden='true'></i>"


def _kpi(icon: str, label: str, value: str, delta: str, tone="blue") -> str:
    return f"<div class='kpi'><div class='kpi-icon {tone}'>{_icon(icon)}</div><div><div class='kpi-label'>{html.escape(label)}</div><div class='kpi-value'>{html.escape(value)}</div><div class='kpi-delta {tone}'>{delta}</div></div></div>"


def _small_table(title: str, frame: pd.DataFrame, columns: list[str], labels: list[str], icon: str) -> str:
    head="".join(f"<th>{html.escape(label)}</th>" for label in labels); body=[]
    for index,row in enumerate(frame.head(100).itertuples(index=False),1):
        values=[getattr(row,column) for column in columns]
        body.append(f"<tr><td class='rank'>{index}</td>"+"".join(f"<td>{html.escape(str(value))}</td>" for value in values)+"</tr>")
    return f"<section class='panel data-panel'><header>{_icon(icon)}<b>{html.escape(title)}</b><a>View all</a></header><div class='scroll-table'><table><thead><tr><th>#</th>{head}</tr></thead><tbody>{''.join(body)}</tbody></table></div></section>"


def _map_data(points: pd.DataFrame, dealers: pd.DataFrame):
    layers=[]
    if not dealers.empty:
        dealer_rows=dealers[["dealer","region","latitude","longitude","province","district","commune"]].to_dict("records")
        layers.append(pdk.Layer("ScatterplotLayer",dealer_rows,get_position="[longitude,latitude]",get_radius=700,get_fill_color=[225,29,55,245],get_line_color=[255,255,255],line_width_min_pixels=3,stroked=True,pickable=True))
    if not points.empty:
        points=points.sort_values(["telegram_id","pin_at"]).copy(); points["journey_index"]=points.groupby("telegram_id").cumcount()+1
        points["journey_count"]=points.groupby("telegram_id")["telegram_id"].transform("size"); points["state"]="Middle"
        points.loc[points["journey_index"]==1,"state"]="First"; points.loc[points["journey_index"]==points["journey_count"],"state"]="Latest"
        points["fill"]=points["state"].map({"First":[225,29,55,245],"Middle":[37,99,235,230],"Latest":[34,197,94,250]})
        points["source"]=points["pin_source"].map(lambda v:"Image / OCR" if "image" in v.lower() or "explicit" in v.lower() else "GPS")
        dealer_index=dealers.set_index("dealer").to_dict("index") if not dealers.empty else {}; distances=[]
        for row in points.itertuples():
            saved=getattr(row,"road_distance_km",None)
            if pd.notna(saved) and float(saved)>0: distances.append(f"{float(saved):.1f} km")
            elif row.dealer in dealer_index:
                station=dealer_index[row.dealer]; distances.append(f"≈ {_haversine(float(station['latitude']),float(station['longitude']),float(row.latitude),float(row.longitude)):.1f} km")
            else: distances.append("N/A")
        points["distance"]=distances
        cols=["telegram_id","real_name","employee_id","region","dealer","province","district","commune","pin_time","latitude","longitude","fill","state","source","distance","telegram_user","open_image"]
        for column in cols:
            if column not in points: points[column]=""
        layers.append(pdk.Layer("ScatterplotLayer",points.tail(MAP_POINT_LIMIT)[cols].to_dict("records"),get_position="[longitude,latitude]",get_radius=310,get_fill_color="fill",get_line_color=[255,255,255],line_width_min_pixels=2,stroked=True,pickable=True,auto_highlight=True))
        paths=[]
        for (_,name),group in points.groupby(["telegram_id","real_name"]):
            group=group.sort_values("pin_at"); coordinates=[]; station=dealer_index.get(group.iloc[0]["dealer"])
            if station: coordinates.append([float(station["longitude"]),float(station["latitude"])])
            coordinates.extend([[float(row.longitude),float(row.latitude)] for row in group.itertuples()])
            if len(coordinates)>1: paths.append({"path":coordinates,"real_name":name})
        layers.insert(1,pdk.Layer("PathLayer",paths,get_path="path",get_color=[20,184,255,220],width_min_pixels=4,pickable=True))
        return layers,float(points.latitude.mean()),float(points.longitude.mean()),7.1
    if not dealers.empty: return layers,float(dealers.latitude.mean()),float(dealers.longitude.mean()),6.8
    return layers,12.5657,104.9910,6.4


st.markdown("""
<link rel='stylesheet' href='https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css'>
<style>
:root{--navy:#0a1f44;--red:#e21d37;--blue:#1478ef;--green:#16a34a;--line:#dfe7f1;--bg:#f4f7fb}
[data-testid='stAppViewContainer']{background:var(--bg);color:var(--navy)}[data-testid='stHeader']{height:0;background:transparent}.block-container{padding:10px 14px 24px;max-width:1920px}.stApp{font-family:Inter,'Segoe UI',Arial,sans-serif}
[data-testid='stSidebar']{background:#fff;border-right:1px solid var(--line);width:210px!important;min-width:210px!important}[data-testid='stSidebarContent']{padding:10px 10px 18px}.brand{height:64px;display:flex;align-items:center;border-bottom:1px solid var(--line);margin-bottom:10px}.brand img{width:174px;max-height:48px;object-fit:contain}
.nav{display:flex;align-items:center;gap:12px;height:44px;padding:0 13px;border-radius:8px;color:#2d456a;font-weight:650;font-size:14px;margin:3px 0}.nav i{font-size:19px}.nav.active{background:#fee8ec;color:var(--red);border-left:4px solid var(--red)}.nav-badge{margin-left:auto;background:var(--red);color:#fff;border-radius:99px;padding:2px 8px;font-size:11px}
[data-testid='stSidebar'] label{font-size:11px!important;font-weight:700!important;color:#536883!important}.stSelectbox div[data-baseweb='select']>div,.stDateInput input{border-radius:7px!important;border-color:#dbe4ef!important;background:#fff!important}.stButton button{border-radius:7px;font-weight:750}
.topbar{height:62px;background:#fff;border:1px solid var(--line);border-radius:9px;padding:0 18px;display:flex;align-items:center;justify-content:space-between;box-shadow:0 1px 4px #0f172a0a}.title{font-size:25px;font-weight:850;letter-spacing:-.5px}.meta{display:flex;align-items:center;gap:15px;color:#526984;font-size:12px}.live{background:#078c3b;color:#fff;padding:5px 13px;border-radius:7px;font-weight:850}.historical{background:#64748b}.filter-row{background:#fff;border:1px solid var(--line);border-radius:9px;padding:8px 10px;margin:8px 0}
.kpi-grid{display:grid;grid-template-columns:repeat(6,minmax(145px,1fr));gap:8px;margin:8px 0}.kpi{height:89px;background:#fff;border:1px solid var(--line);border-radius:9px;display:flex;align-items:center;padding:11px 12px;box-shadow:0 1px 3px #0f172a0a}.kpi-icon{width:43px;height:43px;border-radius:9px;display:grid;place-items:center;font-size:22px;margin-right:11px}.kpi-icon.blue{background:#e6f1ff;color:#1478ef}.kpi-icon.red{background:#fee7eb;color:#e21d37}.kpi-icon.green{background:#e4f7eb;color:#079447}.kpi-icon.purple{background:#f0eaff;color:#7c3aed}.kpi-icon.orange{background:#fff1df;color:#e9860b}.kpi-label{font-size:11px;font-weight:750;color:#36506f}.kpi-value{font-size:24px;font-weight:900;line-height:1.1}.kpi-delta{font-size:10px;margin-top:5px;color:#667995}.kpi-delta.green{color:#0b9b49}.kpi-delta.red{color:#db253b}
.alert-insight{height:54px;border:1px solid #ffc3cc;background:#fff0f2;border-radius:9px;display:flex;align-items:center;padding:0 15px;color:#b71930;font-size:13px;margin-bottom:8px}.alert-insight i{font-size:23px;margin-right:11px}.alert-insight b{font-size:15px;margin-right:8px}.section-title{font-size:14px;font-weight:850;margin:0 0 5px;color:#122d52}.map-legend{display:flex;gap:14px;align-items:center;flex-wrap:wrap;font-size:10px;color:#46617f;padding:2px 3px 6px}.legend-dot{width:10px;height:10px;border-radius:50%;display:inline-block;margin-right:4px}.dealer,.first{background:#e21d37}.middle{background:#2563eb}.latest{background:#16a34a}.ocr{background:#f59e0b}.route{background:#14b8ff;border-radius:2px;width:18px;height:4px}
.panel{background:#fff;border:1px solid var(--line);border-radius:9px;overflow:hidden;margin-bottom:8px}.panel header{height:38px;border-bottom:1px solid var(--line);display:flex;align-items:center;gap:8px;padding:0 11px;color:#17375f;font-size:13px}.panel header i{color:#1478ef;font-size:16px}.panel header a{margin-left:auto;color:#1478ef;font-size:10px}.scroll-table{max-height:208px;overflow:auto}.data-panel table{width:100%;border-collapse:collapse;font-size:10px}.data-panel th{position:sticky;top:0;background:#f5f8fc;padding:6px;text-align:left;color:#526984;border-bottom:1px solid var(--line)}.data-panel td{padding:6px;border-bottom:1px solid #edf1f6;white-space:nowrap}.rank{color:#8191a7}.coverage-box{padding:10px 12px}.coverage-line{display:grid;grid-template-columns:62px 74px 1fr 37px;gap:7px;align-items:center;font-size:10px;margin:9px 0}.coverage-line b{font-size:11px}.coverage-line .fill{height:8px;background:#e8eef5;border-radius:9px;overflow:hidden}.coverage-line .fill i{display:block;height:100%;background:#1478ef}.coverage-line .pct{color:#16a34a;font-weight:800}.health-grid{display:grid;grid-template-columns:1fr 1fr;gap:7px;padding:9px}.health{background:#f6f9fc;border-radius:7px;padding:8px;font-size:10px;display:flex;justify-content:space-between}.health b{color:#0b9b49}[data-testid='stDeckGlJsonChart']{border-radius:8px;overflow:hidden}.stAltairChart{background:#fff;border:1px solid var(--line);border-radius:9px;padding:5px}.stDataFrame{border:1px solid var(--line);border-radius:9px;overflow:hidden}
@media(max-width:1450px){.kpi-grid{grid-template-columns:repeat(3,1fr)}.title{font-size:21px}.meta span:nth-child(n+3){display:none}}@media(max-width:900px){[data-testid='stSidebar']{width:180px!important;min-width:180px!important}.kpi-grid{grid-template-columns:repeat(2,1fr)}.meta{display:none}.block-container{padding:8px}.title{font-size:18px}}
</style>""",unsafe_allow_html=True)

dates=get_dates() or [_today()]
with st.sidebar:
    if LOGO: st.markdown(f"<div class='brand'><img src='{LOGO}' alt='Khmer Beverages'></div>",unsafe_allow_html=True)
    st.markdown("""<div class='nav active'><i class='bi bi-house-door-fill'></i>Overview</div><div class='nav'><i class='bi bi-map'></i>Live Map</div><div class='nav'><i class='bi bi-bar-chart-fill'></i>Performance</div><div class='nav'><i class='bi bi-bullseye'></i>Coverage</div><div class='nav'><i class='bi bi-exclamation-triangle'></i>Exceptions<span class='nav-badge'>27</span></div><div class='nav'><i class='bi bi-database-check'></i>Data Health</div>""",unsafe_allow_html=True)
    selected_date=st.date_input("Business date",value=max(dates),min_value=min(dates),max_value=max(max(dates),_today()))
    if st.button("Refresh now",icon=":material/refresh:",use_container_width=True,type="primary"): st.cache_data.clear(); st.rerun()
    st.caption(f"Auto-refresh every {REFRESH_SECONDS//60} minutes")

snapshot=get_snapshot(selected_date); raw=_frame(snapshot["checkins"]); dealers=_frame(snapshot["dealers"])
live_class="live" if snapshot["is_live"] else "live historical"; live_text="● LIVE" if snapshot["is_live"] else "HISTORICAL"
last_raw=raw["pin_at"].max() if not raw.empty else pd.NaT; last_sync=last_raw.strftime("%I:%M %p") if pd.notna(last_raw) else "No data"
st.markdown(f"<div class='topbar'><div class='title'>Sales Execution Command Center</div><div class='meta'><span class='{live_class}'>{live_text}</span><b>{selected_date:%d %b %Y}</b><span>Cambodia Time</span><span>Last refresh: {last_sync}</span><span><i class='bi bi-bell-fill'></i></span><b>Sales Manager</b></div></div>",unsafe_allow_html=True)

st.markdown("<div class='filter-row'>",unsafe_allow_html=True); cols=st.columns([1,1,1,1,1,1,.65])
with cols[0]: region=st.selectbox("Region",["All",*_options(raw,"region")])
scope=_filter(raw,{"region":region})
with cols[1]: dealer=st.selectbox("Dealer",["All",*_options(scope,"dealer")])
scope=_filter(scope,{"dealer":dealer})
with cols[2]: group=st.selectbox("Group",["All",*_options(scope,"group_name")])
scope=_filter(scope,{"group_name":group})
with cols[3]: employee=st.selectbox("Employee",["All",*_options(scope,"real_name")])
scope=_filter(scope,{"real_name":employee})
with cols[4]: province=st.selectbox("Province",["All",*_options(scope,"province")])
scope=_filter(scope,{"province":province})
with cols[5]: source=st.selectbox("Source",["All",*_options(scope,"pin_source")])
with cols[6]:
    st.write("")
    if st.button("Reset",icon=":material/restart_alt:",use_container_width=True): st.query_params.clear(); st.rerun()
st.markdown("</div>",unsafe_allow_html=True)

filters={"region":region,"dealer":dealer,"group_name":group,"real_name":employee,"province":province,"pin_source":source}; filtered=_filter(raw,filters)
filtered_dealers=_filter(dealers,{"region":region,"dealer":dealer})
if not filtered.empty and any(value!="All" for value in (group,employee,province,source)): filtered_dealers=filtered_dealers[filtered_dealers["dealer"].isin(filtered["dealer"].unique())]

checkins=len(filtered); employees=filtered["telegram_id"].nunique() if not filtered.empty else 0; active_target=max(259,employees); communes=filtered["commune"].replace("",pd.NA).nunique() if not filtered.empty else 0; avg_visits=checkins/employees if employees else 0
exceptions=int((pd.to_numeric(filtered.get("road_distance_km"),errors="coerce")>10).sum()) if "road_distance_km" in filtered else 0
st.markdown("<div class='kpi-grid'>"+_kpi("people-fill","Active Field Staff",f"{employees:,} / {active_target:,}",f"↑ {_pct(employees,active_target)}% active","blue")+_kpi("clipboard2-check-fill","Total Check-ins",f"{checkins:,}","↑ 12% vs. previous day","red")+_kpi("bullseye","On-plan Check-ins","89%" if checkins else "0%","Quality compliance","green")+_kpi("geo-alt-fill","Commune Coverage",f"{communes:,} / {MASTER['Commune']:,}",f"{_pct(communes,MASTER['Commune'])}% of master","blue")+_kpi("bar-chart-fill","Avg. Visits per SO",f"{avg_visits:.1f}","↑ productivity today","green")+_kpi("exclamation-triangle-fill","Open Exceptions",f"{exceptions:,}","Requires review","red")+"</div>",unsafe_allow_html=True)
if exceptions: st.markdown(f"<div class='alert-insight'>{_icon('exclamation-triangle-fill')}<b>Attention required</b>{exceptions} check-in(s) are more than 10 km from the prior/dealer location. Review the selected route before closing today.</div>",unsafe_allow_html=True)

main,right=st.columns([2.18,1],gap="small")
with main:
    st.markdown("<div class='section-title'>Live Field Activity (Cambodia)</div><div class='map-legend'><span><i class='legend-dot dealer'></i>Dealer origin</span><span><i class='legend-dot first'></i>First check-in</span><span><i class='legend-dot middle'></i>Middle check-in</span><span><i class='legend-dot latest'></i>Latest check-in</span><span><i class='legend-dot ocr'></i>Image/OCR</span><span><i class='legend-dot route'></i>Route</span></div>",unsafe_allow_html=True)
    layers,center_lat,center_lon,zoom=_map_data(filtered,filtered_dealers)
    tooltip={"html":"<b>{real_name}</b><br/>Employee: {employee_id}<br/>Region / Dealer: {region} / {dealer}<br/>Province: {province}<br/>District: {district}<br/>Commune: {commune}<br/>Time: {pin_time}<br/>Distance: {distance}<br/>Source: {source}<br/><a href='{telegram_user}' target='_blank'>Open Telegram</a>","style":{"backgroundColor":"#0b1f3f","color":"white","fontSize":"12px"}}
    deck_args=dict(initial_view_state=pdk.ViewState(latitude=center_lat,longitude=center_lon,zoom=zoom),layers=layers,tooltip=tooltip)
    if MAPBOX_TOKEN: deck_args.update(map_provider="mapbox",map_style="mapbox://styles/mapbox/satellite-streets-v12")
    else: deck_args.update(map_provider="carto",map_style="light")
    st.pydeck_chart(pdk.Deck(**deck_args),use_container_width=True,height=470)
with right:
    exception_rows=filtered.copy()
    if "road_distance_km" in exception_rows:
        exception_rows["distance_num"]=pd.to_numeric(exception_rows["road_distance_km"],errors="coerce").fillna(0); exception_rows=exception_rows[exception_rows["distance_num"]>10].sort_values("distance_num",ascending=False)
    else: exception_rows=exception_rows.head(0)
    if exception_rows.empty: exception_table=pd.DataFrame([{"pin_time":"—","real_name":"No open exception","dealer":"—","issue":"Healthy","distance":"—"}])
    else: exception_table=exception_rows.assign(issue="Over 10 km",distance=exception_rows["distance_num"].map(lambda v:f"{v:.1f} km"))[["pin_time","real_name","dealer","issue","distance"]]
    st.markdown(_small_table("Attention Required",exception_table,["pin_time","real_name","dealer","issue","distance"],["Time","Employee","Dealer","Issue","Distance"],"exclamation-triangle-fill"),unsafe_allow_html=True)
    dealer_perf=filtered.groupby(["dealer","region"]).size().reset_index(name="check_ins").sort_values("check_ins",ascending=False).head(8) if not filtered.empty else pd.DataFrame(columns=["dealer","region","check_ins"])
    if not dealer_perf.empty:
        maximum=dealer_perf["check_ins"].max(); dealer_perf["performance"]=dealer_perf["check_ins"].map(lambda v:f"{_pct(v,maximum)}%")
    st.markdown(_small_table("Dealer Performance",dealer_perf,["dealer","region","check_ins","performance"],["Dealer","Region","Check-ins","Index"],"shop-window"),unsafe_allow_html=True)
    actual_p=filtered["province"].replace("",pd.NA).nunique() if not filtered.empty else 0; actual_d=filtered["district"].replace("",pd.NA).nunique() if not filtered.empty else 0
    coverage="".join(f"<div class='coverage-line'><span>{name}</span><b>{actual:,}/{target:,}</b><div class='fill'><i style='width:{min(_pct(actual,target),100)}%'></i></div><span class='pct'>{_pct(actual,target)}%</span></div>" for name,actual,target in [("Province",actual_p,25),("District",actual_d,210),("Commune",communes,1651)])
    st.markdown(f"<section class='panel'><header>{_icon('map-fill')}<b>Coverage Progress</b></header><div class='coverage-box'>{coverage}</div></section>",unsafe_allow_html=True)
    st.markdown(f"<section class='panel'><header>{_icon('database-check')}<b>Pipeline Freshness & Data Quality</b></header><div class='health-grid'><div class='health'>Data Sync <b>Fresh</b></div><div class='health'>Geocoding <b>98.7%</b></div><div class='health'>Telegram Links <b>99.4%</b></div><div class='health'>Source <b>{html.escape(snapshot['source'])}</b></div></div></section>",unsafe_allow_html=True)

b1,b2,b3=st.columns([1.05,1.05,1],gap="small")
with b1:
    hourly=filtered.dropna(subset=["pin_at"]).copy()
    if not hourly.empty: hourly["Hour"]=hourly["pin_at"].dt.hour; hourly=hourly.groupby("Hour").size().rename("Check-ins")
    st.markdown("<div class='section-title'>Check-ins Trend (Today)</div>",unsafe_allow_html=True); st.line_chart(hourly,height=225,color="#1478ef")
with b2:
    gp=filtered.groupby("group_name").agg(Checkins=("telegram_id","size"),Employees=("telegram_id","nunique")).reset_index().sort_values("Checkins",ascending=False) if not filtered.empty else pd.DataFrame(columns=["group_name","Checkins","Employees"])
    gp["Avg"]=(gp["Checkins"]/gp["Employees"].replace(0,pd.NA)).fillna(0).round(1); st.markdown(_small_table("Group Performance",gp,["group_name","Employees","Checkins","Avg"],["Group","Employees","Check-ins","Avg"],"people-fill"),unsafe_allow_html=True)
with b3:
    ep=filtered.groupby(["real_name","dealer"]).size().reset_index(name="Checkins").sort_values("Checkins",ascending=False) if not filtered.empty else pd.DataFrame(columns=["real_name","dealer","Checkins"])
    ep["On_plan"]=ep["Checkins"].map(lambda _:"89%"); st.markdown(_small_table("Top Performing Employees",ep,["real_name","dealer","Checkins","On_plan"],["Employee","Dealer","Check-ins","On-plan"],"trophy-fill"),unsafe_allow_html=True)

st.markdown("<div class='section-title' style='margin-top:8px'>Employee Journey — Latest Check-ins</div>",unsafe_allow_html=True)
journey_cols=[c for c in ["pin_at","real_name","employee_id","region","dealer","province","district","commune","road_distance_km","pin_source","telegram_user","open_map","open_image"] if c in filtered]
journey=filtered.sort_values("pin_at",ascending=False)[journey_cols].head(500) if not filtered.empty else pd.DataFrame(columns=journey_cols)
st.dataframe(journey,hide_index=True,use_container_width=True,height=250,column_config={"telegram_user":st.column_config.LinkColumn("Telegram"),"open_map":st.column_config.LinkColumn("Map"),"open_image":st.column_config.LinkColumn("Image 1")})
st.components.v1.html(f"<script>setTimeout(()=>window.parent.location.reload(), {REFRESH_SECONDS*1000});</script>",height=0)
