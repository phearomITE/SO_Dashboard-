import { sessionFetch } from "./dashboardSession.js";
import ActualPlan from "./ActualPlan.jsx";
import ReasonSummaryPage from './ReasonSummaryPage.jsx';
import DashboardErrorBoundary from './DashboardErrorBoundary.jsx';
import GPReport from './GPReport.jsx';
import React, { useEffect, useMemo, useRef, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  MapPin,
  ChartBar,
  Stack,
  CalendarBlank,
  Storefront,
  Gear,
  SquaresFour,
  Bell,
  UserCircle,
  UsersThree,
  WarningCircle,
  CheckCircle,
  Path,
  Plus,
  PencilSimple,
  FloppyDisk,
  UploadSimple,
  TrendUp,
  UserMinus,
  MagnifyingGlass,
} from "@phosphor-icons/react";
import {
  ResponsiveContainer,
  BarChart,
  Bar,
  CartesianGrid,
  XAxis,
  YAxis,
  Tooltip,
  LineChart,
  Line,
  Legend,
} from "recharts";

async function adminApi(path, options = {}) {
  const response = await sessionFetch(path, options);
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}));
    throw new Error(detail.detail || `Request failed (${response.status})`);
  }
  return response.status === 204 ? null : response.json();
}


const dealerGroups = {
  R1: ["CA1", "CA2", "CA3", "CA4", "CA5", "CA6", "CA7", "CA8", "CA9", "PTM6"],
  R2: [
    "BTI6",
    "CPH2",
    "KDL1",
    "KHT3",
    "KRV5",
    "KSV5",
    "KTB1",
    "PKB4",
    "PMR5",
    "SVA7",
    "SVR4",
    "TAK2",
  ],
  R3: [
    "CUK7",
    "KKG3",
    "KPS7",
    "KPT2",
    "KSM1",
    "KSP3",
    "KTR4",
    "PNP6",
    "SRA5",
    "TPG2",
  ],
  R4: ["BTB1", "BVL6", "KCG4", "KTL6", "MRS5", "PLN4", "PPT3", "PUS5", "VVG1"],
  R5: ["BMC2", "CHK6", "KRG7", "KTH2", "KTM8", "PPR8", "PUK7", "SRP1"],
  R6: ["BTY8", "KCM1", "MMT3", "STT4", "TKM2"],
  R7: ["BKE7", "KRT1", "KSA5", "MDK2", "SNL6", "STR3", "RTK4"],
  R8: ["CKS1", "AVG4", "PVH3", "SMR5"],
};
const dealers = Object.entries(dealerGroups).flatMap(([region, list]) =>
  list.map((code) => ({ region, code })),
);
const nav = [
  ["Live Map Check In", MapPin],
  ["Performance", ChartBar],
  ["Master Sale", Stack],
  ["Plan Sale", CalendarBlank],
  ["AL", UserMinus],
  ["Reason Summary", WarningCircle],
  ["Report GP", CalendarBlank],
  ["Setting", Gear],
];

function cambodiaMapPosition(latitude, longitude) {
  const lat = Number(latitude);
  const lon = Number(longitude);
  if (!Number.isFinite(lat) || !Number.isFinite(lon) ||
      lat < 9.8 || lat > 14.75 || lon < 102.3 || lon > 107.7) return null;
  return {
    left: `${((lon - 102.3) / (107.7 - 102.3) * 100).toFixed(2)}%`,
    top: `${((14.75 - lat) / (14.75 - 9.8) * 100).toFixed(2)}%`,
  };
}

function Header({ page, apiHealth, onOpenAlert }) {
  const [now, setNow] = useState(() => new Date());
  const [firstCheckAlerts, setFirstCheckAlerts] = useState(null);
  const [notificationOpen, setNotificationOpen] = useState(false);
  const notificationRef = useRef(null);
  useEffect(() => {
    const timer = setInterval(() => setNow(new Date()), 15000);
    return () => clearInterval(timer);
  }, []);
  useEffect(() => {
    let active = true;
    const refresh = () => adminApi("/api/v1/admin/first-check-alerts")
      .then(result => { if (active) setFirstCheckAlerts(result); })
      .catch(() => { if (active) setFirstCheckAlerts({ error: true, count: 0, items: [] }); });
    refresh();
    const timer = setInterval(refresh, 30000);
    return () => { active = false; clearInterval(timer); };
  }, []);
  useEffect(() => {
    if (!notificationOpen) return undefined;
    const closeOnOutsideClick = event => {
      if (!notificationRef.current?.contains(event.target)) setNotificationOpen(false);
    };
    document.addEventListener("pointerdown", closeOnOutsideClick);
    return () => document.removeEventListener("pointerdown", closeOnOutsideClick);
  }, [notificationOpen]);
  const timeLabel = new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Phnom_Penh", weekday: "short", day: "2-digit", month: "short",
    year: "numeric", hour: "2-digit", minute: "2-digit", hour12: false,
  }).format(now);
  return (
    <header className="header">
      <div className="sa-breadcrumb" aria-label="Breadcrumb">
        <span className="sa-crumb-root">Workspace</span>
        <span className="sa-crumb-sep" aria-hidden="true">›</span>
        <strong className="sa-crumb-current">{page}</strong>
      </div>
      <div className="header-tools">
        <span className="time">
          <b>●</b>Cambodia Time (ICT) · {timeLabel}
        </span>
        <input className="search" placeholder="Search SO, dealer, location…" />
        <div className="notification-wrap" ref={notificationRef}>
          <button className="icon-btn notification-button" aria-label={`${firstCheckAlerts?.count || 0} first-check alerts`}
            aria-expanded={notificationOpen} onClick={() => setNotificationOpen(value => !value)}>
            <Bell size={18} />
            {firstCheckAlerts?.count > 0 && <span className="notification-count">{firstCheckAlerts.count > 99 ? "99+" : firstCheckAlerts.count}</span>}
          </button>
          {notificationOpen && <section className="notification-popover" role="dialog" aria-label="First check-in alerts">
            <div className="notification-heading"><div><b>First check-in</b><small>Due by 9:00 AM ICT · {firstCheckAlerts?.date || "Today"}</small></div>
              <span className={firstCheckAlerts?.count ? "notification-total is-alert" : "notification-total"}>{firstCheckAlerts?.count || 0}</span></div>
            {firstCheckAlerts?.error ? <p className="notification-empty">Alerts could not be loaded. Try again shortly.</p> :
              firstCheckAlerts?.deadline_passed === false ? <p className="notification-empty">Monitoring starts at 9:00 AM ICT.</p> :
              firstCheckAlerts?.items?.length ? <div className="notification-list">{firstCheckAlerts.items.map(item => <button type="button" className="notification-item notification-action" key={`${item.employee_id}-${item.telegram_id || ""}`} onClick={() => { onOpenAlert(item); setNotificationOpen(false); }} aria-label={`Show ${item.real_name || item.employee_id} on live map`}>
                <div className="notification-item-top"><b>{item.real_name || "Sales user"}</b><span className={item.alert_type === "late" ? "notification-kind is-late" : "notification-kind"}>{item.alert_type === "late" ? "Late" : "No check-in"}</span></div>
                <small>{item.employee_id} · {item.region} / {item.dealer} · {item.telegram_group}</small>
                <p>{item.alert_type === "late" ? `First check-in at ${item.first_check_time}` : "No first check-in by 9:00 AM"}<span className="notification-open-map">View on map ↗</span></p>
              </button>)}</div> : <p className="notification-empty">All active sales have checked in by 9:00 AM.</p>}
          </section>}
        </div>
        <span className={`sa-conn-pill ${apiHealth !== "ready" ? "is-warn" : ""}`}>
          <i aria-hidden="true" />
          {apiHealth === "ready" ? "Bot online" : apiHealth === "checking" ? "Checking…" : "Unavailable"}
        </span>
      </div>
    </header>
  );
}
function Sidebar({ page, setPage, apiHealth }) {
  const databaseLabel = apiHealth === "ready" ? "PostgreSQL connected" :
    apiHealth === "checking" ? "Checking database" : "Database/API error";
  const workspaceItems = nav.filter(([name]) => name !== "Setting");
  const managementItems = nav.filter(([name]) => name === "Setting");
  return (
    <aside className="sidebar">
      <div className="sa-brand-logo">
        <img src="/assets/cmfb-logo.png" alt="Chip Mong FoodBev" />
      </div>
      <div className="sa-workspace-card">
        <span className="sa-workspace-icon"><SquaresFour size={18} weight="duotone" /></span>
        <div>
          <b>SO Alert Bot</b>
          <small>Operations workspace</small>
        </div>
      </div>
      <nav className="nav" aria-label="Workspace navigation">
        <h2 className="sa-nav-group">WORKSPACE</h2>
        {workspaceItems.map(([name, Icon]) => (
          <motion.button
            whileHover={{ x: 2 }}
            whileTap={{ scale: 0.99 }}
            key={name}
            className={page === name ? "active" : ""}
            aria-label={name} title={name} aria-current={page === name ? "page" : undefined}
            onClick={() => setPage(name)}
          >
            <Icon size={18} />
            <span>{name}</span>
            {page === name && <i className="sa-nav-dot" aria-hidden="true" />}
          </motion.button>
        ))}
        <h2 className="sa-nav-group sa-nav-group-spaced">MANAGEMENT</h2>
        {managementItems.map(([name, Icon]) => (
          <motion.button
            whileHover={{ x: 2 }}
            whileTap={{ scale: 0.99 }}
            key={name}
            className={page === name ? "active" : ""}
            aria-label={name} title={name} aria-current={page === name ? "page" : undefined}
            onClick={() => setPage(name)}
          >
            <Icon size={18} />
            <span>{name}</span>
            {page === name && <i className="sa-nav-dot" aria-hidden="true" />}
          </motion.button>
        ))}
      </nav>
      <div className="sa-side-footer">
        <span className="sa-side-avatar">SO</span>
        <div>
          <b>Operations Center</b>
          <small>{databaseLabel}</small>
        </div>
      </div>
    </aside>
  );
}
function Metric({ name, value, delta }) {
  return (
    <motion.div className="metric" whileHover={{ y: -1 }}>
      <div className="top">
        <small>{name}</small>
      </div>
      <strong>{value}</strong>

    </motion.div>
  );
}
function RegionDealerFilters({ region, setRegion, dealer, setDealer }) {
  const list =
    region === "All Regions"
      ? dealers
      : dealers.filter((d) => d.region === region);
  return (
    <>
      <label className="field">
        <span>Region</span>
        <select
          value={region}
          onChange={(e) => {
            setRegion(e.target.value);
            setDealer("All Dealers");
          }}
        >
          <option>All Regions</option>
          {Object.keys(dealerGroups).map((r) => (
            <option key={r}>{r}</option>
          ))}
        </select>
      </label>
      <label className="field">
        <span>Dealer</span>
        <select value={dealer} onChange={(e) => setDealer(e.target.value)}>
          <option>All Dealers</option>
          {Object.entries(dealerGroups).map(([r, codes]) => (
            <optgroup label={r} key={r}>
              {codes
                .filter((c) => list.some((x) => x.code === c))
                .map((c) => (
                  <option key={c}>{c}</option>
                ))}
            </optgroup>
          ))}
        </select>
      </label>
    </>
  );
}
function LiveMap({ target }) {
  const params = new URLSearchParams({embedded:"1"});
  if (target?.employee_id) params.set("employee_id", String(target.employee_id));
  if (target?.telegram_id) params.set("telegram_id", String(target.telegram_id));
  if (target?.region) params.set("region", String(target.region));
  if (target?.dealer) params.set("dealer", String(target.dealer));
  if (target?.alert_type) params.set("alert", String(target.alert_type));
  return (
    <div className="live-map-shell">
      <iframe
        title="Live check-in map"
        src={`/static/dashboard.html?${params}`}
        loading="eager"
      />
    </div>
  );
}
function Performance() {
  const today = new Intl.DateTimeFormat("en-CA", {timeZone:"Asia/Phnom_Penh",year:"numeric",month:"2-digit",day:"2-digit"}).format(new Date());
  const [region, setRegion] = useState("All Regions"), [dealer, setDealer] = useState("All Dealers"), [tab, setTab] = useState("Region");
  const [end, setEnd] = useState(today), [start, setStart] = useState(()=>{const d=new Date(`${today}T00:00:00Z`);d.setUTCDate(d.getUTCDate()-6);return d.toISOString().slice(0,10)});
  const [report,setReport]=useState(null),[error,setError]=useState(""),[loading,setLoading]=useState(true);
  useEffect(()=>{
    let active=true;
    const q=new URLSearchParams({start,end});
    if(region!=="All Regions")q.set("region",region);
    if(dealer!=="All Dealers")q.set("dealer",dealer);
    setLoading(true);setError("");setReport(null);
    adminApi(`/api/v1/admin/performance?${q}`)
      .then(data=>{if(active)setReport(data)})
      .catch(e=>{if(active){setReport(null);setError(e.message)}})
      .finally(()=>{if(active)setLoading(false)});
    return ()=>{active=false};
  },[start,end,region,dealer]);
  const summary=(report?.by_region||[]).filter(x=>region==="All Regions"||x.region===region);
  const dealerRows=(report?.by_dealer||[]).filter(x=>(region==="All Regions"||x.region===region)&&(dealer==="All Dealers"||x.dealer===dealer));
  const individualRows=(report?.individuals||[]).filter(x=>(region==="All Regions"||x.region===region)&&(dealer==="All Dealers"||x.dealer===dealer));
  function exportRows(){const items=tab==="Region"?summary:tab==="Dealer"?dealerRows:individualRows;const csv=[Object.keys(items[0]||{}).join(","),...items.map(x=>Object.values(x).map(v=>`"${String(v??"").replaceAll('"','""')}"`).join(","))].join("\n");const a=document.createElement("a");a.href=URL.createObjectURL(new Blob(["\ufeff"+csv],{type:"text/csv;charset=utf-8"}));a.download=`SO_Performance_${tab}_${start}_${end}.csv`;a.click();URL.revokeObjectURL(a.href)}
  return (
    <>
      <div className="panel controls">
        <label className="field">
          <span>From</span><input type="date" value={start} max={end} onChange={e=>setStart(e.target.value)} />
        </label>
        <label className="field"><span>To</span><input type="date" value={end} min={start} max={today} onChange={e=>setEnd(e.target.value)} /></label>
        <RegionDealerFilters {...{ region, setRegion, dealer, setDealer }} />
        <button className="secondary" onClick={exportRows} disabled={loading || !report}>Export CSV</button>
      </div>
      {error&&<p className="api-error" role="alert">{error}</p>}
      <div className="metrics">
        <Metric name="Total Check-ins" value={loading ? "Loading…" : report ? Number(report.totals?.checkins ?? 0).toLocaleString() : "—"} />
        <Metric name="Districts Visited" value={loading ? "Loading…" : report ? Number(report.totals?.districts ?? 0).toLocaleString() : "—"} />
        <Metric name="Communes Visited" value={loading ? "Loading…" : report ? Number(report.totals?.communes ?? 0).toLocaleString() : "—"} />
        <Metric name="Over 10 km / AL" value={loading ? "Loading…" : report ? `${Number(report.totals?.over_10km ?? 0)} / ${Number(report.al_estimate ?? 0)}` : "—"} />
      </div>
      <div className="grid2">
        <div className="panel">
          <div className="panel-head">
            <div>
              <h2>Total Check-in Performance</h2>
              
            </div>
          </div>
          <div className="chart-wrap">
            {loading ? <div className="empty" role="status">Loading performance data…</div> : error ? <div className="empty">Performance data is unavailable.</div> :
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={summary}>
                  <CartesianGrid strokeDasharray="4 4" vertical={false} />
                  <XAxis dataKey="region" />
                  <YAxis />
                  <Tooltip />
                  <Bar dataKey="checkins" fill="#0353a4" radius={[8, 8, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>}
          </div>
        </div>
        <div className="panel">
          <div className="panel-head">
            <div>
              <h2>Coverage & Exceptions</h2>
              
            </div>
          </div>
          <div className="chart-wrap">
            {loading ? <div className="empty" role="status">Loading performance data…</div> : error ? <div className="empty">Performance data is unavailable.</div> :
              <ResponsiveContainer width="100%" height="100%">
                <LineChart
                  data={summary.map((r) => ({r:r.region,district:r.districts,commune:r.communes,over:r.over_10km,active:r.active_sale}))}
                >
                  <CartesianGrid strokeDasharray="4 4" />
                  <XAxis dataKey="r" />
                  <YAxis />
                  <Tooltip />
                  <Legend />
                  <Line dataKey="district" stroke="#0353a4" strokeWidth={3} />
                  <Line dataKey="commune" stroke="#16796c" strokeWidth={3} />
                  <Line dataKey="over" stroke="#d96b43" strokeWidth={3} />
                  <Line dataKey="active" stroke="#6366f1" strokeWidth={3} />
                </LineChart>
              </ResponsiveContainer>}
          </div>
        </div>
      </div>
      <div className="panel" style={{ marginTop: 16 }}>
        <div className="panel-head">
          <div>
            <h2>Performance Drill-down</h2>
            
          </div>
          <div className="tabs">
            {["Region", "Dealer", "Individual Sale"].map((t) => (
              <button
                key={t}
                className={tab === t ? "active" : ""}
                onClick={() => setTab(t)}
              >
                {t}
              </button>
            ))}
          </div>
        </div>
        <div className="table-wrap" style={{ maxHeight: 420 }}>
          {tab === "Region" ? (
            <table className="data-table">
              <thead>
                <tr>
                  <th>Region</th>
                  <th>Dealers</th>
                  <th>Total Check-in</th>
                  <th>District Visit</th>
                  <th>Commune Visit</th>
                  <th>Over 10 km</th>
                  <th>AL</th>
                  <th>Completion</th>
                </tr>
              </thead>
              <tbody>
                {summary.map((r) => (
                  <tr key={r.region}>
                    <td>
                      <span className="region-tag">{r.region}</span>
                    </td>
                    <td>{dealerGroups[r.region]?.length||0}</td>
                    <td>
                      <b>{Number(r.checkins).toLocaleString()}</b>
                    </td>
                    <td>{r.districts}</td><td>{r.communes}</td><td>{r.over_10km}</td><td>{r.al_count}</td><td><span className="status">{r.roster_count ? `${Math.round(100*r.active_sale/r.roster_count)}%` : "—"}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : tab === "Dealer" ? (
            <table className="data-table">
              <thead>
                <tr>
                  <th>Region</th>
                  <th>Dealer</th>
                  <th>Total Check-in</th>
                  <th>District Visit</th>
                  <th>Commune Visit</th>
                  <th>Over 10 km</th>
                  <th>AL</th>
                </tr>
              </thead>
              <tbody>
                {dealerRows.map((r) => (
                    <tr key={`${r.region}-${r.dealer}`}>
                      <td>{r.region}</td>
                      <td>
                        <span className="dealer-tag">{r.dealer}</span>
                      </td>
                      <td><b>{r.checkins}</b></td><td>{r.districts}</td><td>{r.communes}</td><td>{r.over_10km}</td><td>{r.al_count}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
          ) : (
            <table className="data-table">
              <thead>
                <tr>
                  <th>Employee ID</th>
                  <th>Sale Name</th>
                  <th>Region</th>
                  <th>Dealer</th>
                  <th>Check-ins</th>
                  <th>District</th>
                  <th>Commune</th>
                  <th>Over 10 km</th>
                  <th>Attendance</th>
                </tr>
              </thead>
              <tbody>
                {individualRows.map((x) => (
                  <tr key={`${x.employee_id}-${x.region}-${x.dealer}`}>
                    <td>{x.employee_id}</td>
                    <td>
                      <b>{x.real_name}</b>
                    </td>
                    <td>{x.region}</td>
                    <td>
                      <span className="dealer-tag">{x.dealer}</span>
                    </td>
                    <td>{x.checkins}</td><td>{x.districts}</td><td>{x.communes}</td><td>{x.over_10km}</td><td>{x.attendance}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </>
  );
}

function Master() {
  const [data, setData] = useState([]),
    [region, setRegion] = useState("All Regions"),
    [dealer, setDealer] = useState("All Dealers"),
    [statusMasterFilter, setStatusMasterFilter] = useState("All Status Master"),
    [edit, setEdit] = useState(null), [search, setSearch] = useState(""), [error, setError] = useState("");
  const [reportFrom, setReportFrom] = useState(""), [reportTo, setReportTo] = useState("");
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [feedback, setFeedback] = useState("");
  async function uploadMaster(event) {
    const file = event.target.files?.[0];
    if (!file) return;
    setError(""); setFeedback(""); setUploading(true);
    try {
      const payload = new FormData(); payload.append("file", file);
      const result = await adminApi("/api/v1/admin/roster/upload", {method:"POST",body:payload});
      const {items} = await adminApi("/api/v1/admin/roster");
      setData(items.map(x=>({id:x.employee_id,name:x.real_name,telegram:x.telegram_id||"",username:x.username,region:x.region,dealer:x.dealer,role:x.position,active:x.active,source:x.source,statusMaster:x.status_master||"New Join",telegramGroup:x.telegram_group||"",reportedRegion:x.reported_region||"",reportedDealer:x.reported_dealer||"",lastReportAt:x.last_report_at||""})));
      setFeedback(`Master Sale updated: ${result.rows} employees imported from ${result.filename}.`);
    } catch(e) {setError(e.message)}
    finally {setUploading(false);event.target.value=""}
  }
  async function deleteFromMaster(user) {
    if (!window.confirm(`Delete ${user.name} (${user.id}) from Master Sale?\nThis disables their account and hides them from the master. Historical check-ins will be preserved.`)) return;
    setError(""); setFeedback("");
    try {
      await adminApi(`/api/v1/admin/roster/${encodeURIComponent(user.id)}`, {method:"DELETE"});
      setData(rows=>rows.filter(x=>x.id!==user.id));
      setFeedback(`${user.name} removed from Master Sale; historical reports preserved.`);
    } catch(e) {setError(e.message)}
  }
  useEffect(() => {
    let active = true;
    adminApi("/api/v1/admin/roster")
      .then(({items})=>{if(active)setData(items.map(x=>({id:x.employee_id,name:x.real_name,telegram:x.telegram_id||"",username:x.username,region:x.region,dealer:x.dealer,role:x.position,active:x.active,source:x.source,statusMaster:x.status_master|| (x.source==="excel"?"In Master":"New Join"),telegramGroup:x.telegram_group||"",reportedRegion:x.reported_region||"",reportedDealer:x.reported_dealer||"",lastReportAt:x.last_report_at||""})))} )
      .catch(e=>{if(active)setError(e.message)})
      .finally(()=>{if(active)setLoading(false)});
    return ()=>{active=false};
  }, []);
  async function useReportedGroup(user) {
    if (!user.reportedRegion || !user.reportedDealer) return;
    setError("");
    try {
      const payload={employee_id:user.id,telegram_id:user.telegram?Number(user.telegram):null,real_name:user.name,username:user.username||"",region:user.reportedRegion,dealer:user.reportedDealer,position:user.role,active:user.active};
      const result=await adminApi(`/api/v1/admin/roster/${encodeURIComponent(user.id)}`,{method:"PATCH",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
      setData(rows=>rows.map(x=>x.id===user.id?{...x,region:result.region,dealer:result.dealer}:x));
    } catch(e) { setError(e.message); }
  }
  const filtered = data
    .filter(
      (u) =>
        (region === "All Regions" || u.region === region) &&
        (dealer === "All Dealers" || u.dealer === dealer) &&
        (statusMasterFilter === "All Status Master" || u.statusMaster === statusMasterFilter) &&
        (!reportFrom || (u.lastReportAt && String(u.lastReportAt).slice(0, 10) >= reportFrom)) &&
        (!reportTo || (u.lastReportAt && String(u.lastReportAt).slice(0, 10) <= reportTo)) &&
        (!search || `${u.id} ${u.name} ${u.telegram} ${u.telegramGroup}`.toLowerCase().includes(search.toLowerCase())),
    )
    .sort(
      (a, b) =>
        a.region.localeCompare(b.region) || a.dealer.localeCompare(b.dealer),
    );
  const activeSales=data.filter(u=>u.active&&["SO","SS"].includes(String(u.role||"").toUpperCase()));
  const newJoinCount=activeSales.filter(u=>u.statusMaster==="New Join").length;
  const masterCount=activeSales.filter(u=>u.statusMaster==="In Master").length;
  return (
    <>
      <div className="section-title">
        <div>
          <h2>Sale Employee Master</h2>
          
        </div>
        <div className="master-actions">
        <label className="primary upload-button" aria-label="Upload Master Sale Excel">
          <UploadSimple /> {uploading ? "Uploading…" : "Upload Master Sale"}
          <input type="file" accept=".xlsx" disabled={uploading} onChange={uploadMaster} />
        </label>
        <button
          className="primary"
          onClick={() =>
            setEdit({
              id: "",
              name: "",
              telegram: "",
              region: "R1",
              dealer: "CA1",
              role: "SO",
              active: true,
            })
          }
        >
          <Plus /> Add Sale User
        </button>
        </div>
      </div>
      
      {feedback && <p className="api-success" role="status">{feedback}</p>}
      {error && <p className="api-error" role="alert">{error}</p>}
      <div className="metrics master-metrics">
        <Metric name="Total Sale" value={loading?"Loading…":activeSales.length.toLocaleString()} />
        <Metric name="Total Sale New Join" value={loading?"Loading…":newJoinCount.toLocaleString()} />
        <Metric name="Total Sale Master" value={loading?"Loading…":masterCount.toLocaleString()} />
      </div>
      <div className="panel controls master-controls">
        <RegionDealerFilters {...{ region, setRegion, dealer, setDealer }} />
        <label className="field grow">
          <span>Search Employee</span>
          <input value={search} onChange={e=>setSearch(e.target.value)} placeholder="Employee ID, name, Telegram ID…" />
        </label>
        <label className="field">
          <span>Status Master</span>
          <select value={statusMasterFilter} onChange={e=>setStatusMasterFilter(e.target.value)}>
            <option>All Status Master</option>
            <option>In Master</option>
            <option>New Join</option>
          </select>
        </label>
        <label className="field">
          <span>Last report from</span>
          <input type="date" value={reportFrom} max={reportTo || undefined} onChange={e=>setReportFrom(e.target.value)} />
        </label>
        <label className="field">
          <span>Last report to</span>
          <input type="date" value={reportTo} min={reportFrom || undefined} onChange={e=>setReportTo(e.target.value)} />
        </label>
      </div>
      <div className="panel table-wrap">
        <table className="data-table">
          <thead>
            <tr>
              <th>Employee ID</th>
              <th>Real Name</th>
              <th>Region</th>
              <th>Dealer</th>
              <th>Telegram Group</th>
              <th>Telegram ID</th>
              <th>Position</th>
              <th>Status</th>
              <th>Status Master</th>
              <th>Last Report</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {loading ? <tr><td className="empty" colSpan={11}>Loading roster…</td></tr> : filtered.map((u) => (
              <tr key={u.id}>
                <td>
                  <b>{u.id}</b>
                </td>
                <td>{u.name}</td>
                <td>
                  <span className="region-tag">{u.region}</span>
                </td>
                <td>
                  <span className="dealer-tag">{u.dealer}</span>
                </td>
                <td>
                  <span className="dealer-tag">{u.telegramGroup || "No report yet"}</span>
                  {u.reportedRegion && u.reportedDealer &&
                   (u.region !== u.reportedRegion || u.dealer !== u.reportedDealer) &&
                    <button className="secondary compact" title="Assign this user to the latest valid report group" onClick={()=>useReportedGroup(u)}>Use reported group</button>}
                </td>
                <td>{u.telegram}</td>
                <td>{u.role}</td>
                <td>
                  <span className={"status " + (!u.active ? "danger" : "")}>
                    {u.active ? "Active" : "Disabled"}
                  </span>
                </td>
                <td><span className={"master-status " + (u.statusMaster==="New Join"?"master-status-new":"master-status-in")}>{u.statusMaster}</span></td>
                <td>{u.lastReportAt ? String(u.lastReportAt).slice(0, 10) : "—"}</td>
                <td>
                  <button
                    className="icon-btn"
                    onClick={() => setEdit({ ...u })}
                  >
                    <PencilSimple />
                  </button>
                  <button className="secondary compact" onClick={async()=>{
                    try { const response=await adminApi(`/api/v1/admin/roster/${encodeURIComponent(u.id)}`,{method:"PATCH",headers:{"Content-Type":"application/json"},body:JSON.stringify({employee_id:u.id,telegram_id:u.telegram?Number(u.telegram):null,real_name:u.name,username:u.username||"",region:u.region,dealer:u.dealer,position:u.role,active:!u.active})});
                      setData(rows=>rows.map(x=>x.id===u.id?{...x,active:response.active}:x));
                    } catch(e){setError(e.message)}
                  }}>{u.active?"Disable":"Enable"}</button>
                  <button className="secondary compact master-delete" title={`Remove ${u.name} from Master Sale`} onClick={()=>deleteFromMaster(u)}><UserMinus /> Delete</button>
                </td>
              </tr>
            ))}
            {!loading && !error && filtered.length === 0 && <tr><td className="empty" colSpan={11}>No employees match the current filters.</td></tr>}
          </tbody>
        </table>
      </div>
      <AnimatePresence>
        {edit && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            style={{
              position: "fixed",
              inset: 0,
              background: "rgba(17, 45, 35, 0.35)",
              backdropFilter: "blur(3px)",
              zIndex: 50,
              display: "grid",
              placeItems: "center",
            }}
          >
            <motion.div
              initial={{ y: 20, scale: 0.98 }}
              animate={{ y: 0, scale: 1 }}
              className="panel"
              style={{ width: 620 }}
            >
              <div className="panel-head">
                <div>
                  <h2>{edit.id ? "Edit Sale User" : "Add Sale User"}</h2>
                  
                </div>
                <button className="icon-btn" onClick={() => setEdit(null)}>
                  ×
                </button>
              </div>
              <div className="form-grid">
                {[
                  ["Employee ID", "id"],
                  ["Real Name", "name"],
                  ["Telegram ID", "telegram"],
                ].map(([l, k]) => (
                <label key={k} className="field">
                    <span>{l}</span>
                    <input
                      value={edit[k]}
                      onChange={(e) =>
                        setEdit({ ...edit, [k]: e.target.value })
                      }
                    />
                  </label>
                ))}
                <label className="field"><span>Status</span><select value={edit.active===false?"Disabled":"Active"} onChange={e=>setEdit({...edit,active:e.target.value==="Active"})}><option>Active</option><option>Disabled</option></select></label>
                <label className="field">
                  <span>Region</span>
                  <select
                    value={edit.region}
                    onChange={(e) =>
                      setEdit({
                        ...edit,
                        region: e.target.value,
                        dealer: dealerGroups[e.target.value][0],
                      })
                    }
                  >
                    {Object.keys(dealerGroups).map((r) => (
                      <option key={r}>{r}</option>
                    ))}
                  </select>
                </label>
                <label className="field">
                  <span>Dealer</span>
                  <select
                    value={edit.dealer}
                    onChange={(e) =>
                      setEdit({ ...edit, dealer: e.target.value })
                    }
                  >
                    {dealerGroups[edit.region].map((d) => (
                      <option key={d}>{d}</option>
                    ))}
                  </select>
                </label>
                <label className="field">
                  <span>Position</span>
                  <select
                    value={edit.role}
                    onChange={(e) => setEdit({ ...edit, role: e.target.value })}
                  >
                    <option>SO</option>
                    <option>SS</option>
                    <option>SA</option>
                    <option>SL</option>
                  </select>
                </label>
              </div>
              <div
                style={{
                  display: "flex",
                  justifyContent: "flex-end",
                  gap: 10,
                  padding: "0 20px 20px",
                }}
              >
                <button className="secondary" onClick={() => setEdit(null)}>
                  Cancel
                </button>
                <button
                  className="primary"
                  onClick={async () => {
                    setError("");
                    const payload={employee_id:edit.id.trim(),telegram_id:edit.telegram?Number(edit.telegram):null,real_name:edit.name.trim(),username:edit.username||"",region:edit.region,dealer:edit.dealer,position:edit.role,active:edit.active!==false};
                    try {
                      const response=await adminApi(edit.id?`/api/v1/admin/roster/${encodeURIComponent(edit.id)}`:"/api/v1/admin/roster",{method:edit.id?"PATCH":"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
                      const saved={id:response.employee_id,name:response.real_name,telegram:response.telegram_id||"",username:response.username,region:response.region,dealer:response.dealer,role:response.position,active:response.active,source:response.source||edit.source||(edit.id?"excel":"dashboard"),statusMaster:response.status_master||edit.statusMaster||(response.source==="excel"?"In Master":"New Join"),telegramGroup:edit.telegramGroup||"",reportedRegion:edit.reportedRegion||"",reportedDealer:edit.reportedDealer||"",lastReportAt:edit.lastReportAt||""};
                      setData(rows=>rows.some(x=>x.id===saved.id)?rows.map(x=>x.id===saved.id?saved:x):[saved,...rows]);setEdit(null);
                    } catch(e){setError(e.message)}
                  }}
                >
                  <FloppyDisk /> Save Changes
                </button>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </>
  );
}

function Plan() {
  const [view,setView]=useState('workbook');
  return <div className="plan-page"><div className="plan-view-tabs" role="tablist" aria-label="Plan SO pages"><button role="tab" aria-selected={view==='workbook'} className={view==='workbook'?'selected':''} onClick={()=>setView('workbook')}>Plan workbook</button><button role="tab" aria-selected={view==='actual'} className={view==='actual'?'selected':''} onClick={()=>setView('actual')}>Actual Plan · All employees</button></div>{view==='actual'?<ActualPlan api={adminApi} RegionDealerFilters={RegionDealerFilters} Metric={Metric}/>:<PlanWorkbook/>}</div>
}
function PlanWorkbook() {
  const [plan, setPlan] = useState({ latest: {}, items: [], dates: [], columns: [] });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [planDate,setPlanDate]=useState("All dates");
  const [planRegion,setPlanRegion]=useState("All Regions");
  const [planDealer,setPlanDealer]=useState("All Dealers");
  const [planSearch,setPlanSearch]=useState("");
  const [page,setPage]=useState(0);
  const pageSize=100;
  const refresh = () => adminApi("/api/v1/admin/plans").then(setPlan).catch(e=>setError(e.message));
  useEffect(() => { refresh(); }, []);
  async function upload(event) {
    const file = event.target.files?.[0]; event.target.value = "";
    if (!file) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const form = new FormData(); form.append("file", file);
      const result = await adminApi("/api/v1/admin/plans/upload", { method: "POST", body: form });
      setPlan(await adminApi("/api/v1/admin/plans"));
      setPlanDate("All dates");setPlanRegion("All Regions");setPlanDealer("All Dealers");setPlanSearch("");setPage(0);
      setNotice(`Plan uploaded: ${Number(result.latest?.commune_rows||0).toLocaleString()} commune rows and ${Number(result.rows).toLocaleString()} scheduled date cells.`);
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  }
  const itemRows = plan.items || [];
  const dateOptions=plan.dates||[];
  const corePlanColumns=[
    {key:"region",label:"Region",type:"data"},
    {key:"dealer",label:"Dealer",type:"data"},
    {key:"employee id",label:"Employee ID",type:"data"},
    {key:"sale name",label:"Sale Name",type:"data"},
    {key:"district",label:"District",type:"data"},
    {key:"com.code",label:"Com.Code",type:"data"},
    {key:"commune",label:"Commune Name",type:"data"},
    {key:"dis. from dealer",label:"Dis. From Dealer",type:"data"},
  ];
  const columns=corePlanColumns;
  const getValue=(row,key)=>row.values?.[key]??"";
  const isScheduledMark=mark=>!(["","0","no","off","-","al"].includes(String(mark??"").trim().toLowerCase()));
  const regionOptions=[...new Set(itemRows.map(x=>getValue(x,"region")).filter(Boolean))].sort();
  const dealerOptions=[...new Set(itemRows.filter(x=>planRegion==="All Regions"||getValue(x,"region")===planRegion).map(x=>getValue(x,"dealer")).filter(Boolean))].sort();
  const normalizedSearch=planSearch.trim().toLowerCase();
  const visibleRows=itemRows.filter(x=>(planRegion==="All Regions"||getValue(x,"region")===planRegion)
    &&(planDealer==="All Dealers"||getValue(x,"dealer")===planDealer)
    &&(planDate==="All dates"||isScheduledMark(x.schedule?.[planDate]))
    &&(!normalizedSearch||corePlanColumns.map(column=>getValue(x,column.key)).join(" ").toLowerCase().includes(normalizedSearch)));
  const pageCount=Math.max(1,Math.ceil(visibleRows.length/pageSize));
  const currentPage=Math.min(page,pageCount-1);
  const pageRows=visibleRows.slice(currentPage*pageSize,(currentPage+1)*pageSize);
  const employeeCount = new Set(itemRows.map(x=>getValue(x,"employee id")).filter(Boolean)).size;
  const dealerCount = new Set(itemRows.map(x=>`${getValue(x,"region")}-${getValue(x,"dealer")}`).filter(x=>!x.endsWith("-")&&!x.startsWith("-"))).size;
  const districtCount = new Set(itemRows.map(x=>`${getValue(x,"region")}-${getValue(x,"dealer")}-${getValue(x,"district")}`).filter(x=>!x.endsWith("-")&&!x.includes("--"))).size;
  const communeCount = new Set(itemRows.map(x=>getValue(x,"com.code")||`${getValue(x,"region")}-${getValue(x,"dealer")}-${getValue(x,"commune")}`).filter(x=>x&&!String(x).endsWith("-"))).size;
  const scheduledCellCount=itemRows.reduce((sum,row)=>sum+dateOptions.reduce((count,day)=>{
    return count+(isScheduledMark(row.schedule?.[day])?1:0);
  },0),0);
  const showingRows=visibleRows.length?`${(currentPage*pageSize+1).toLocaleString()}–${Math.min((currentPage+1)*pageSize,visibleRows.length).toLocaleString()}`:"0";
  return (
    <>
      <div className="section-title">
        <div>
          <h2>Current Sale Plan</h2>
          
        </div>
        <label className="primary upload-button"><UploadSimple /> {busy ? "Uploading and validating…" : "Upload Excel Plan"}<input type="file" accept=".xlsx" disabled={busy} onChange={upload} /></label>
      </div>
      {error && <p className="api-error" role="alert">{error}</p>}{notice && <p className="api-success">{notice}</p>}
      <div className="metrics plan-metrics">
        <Metric name="Sales IDs in Plan" value={employeeCount.toLocaleString()} />
        <Metric name="Excel Commune Rows" value={itemRows.length.toLocaleString()} />
        <Metric name="Unique Commune Codes" value={communeCount.toLocaleString()} />
        <Metric name="Scheduled Date Cells" value={scheduledCellCount.toLocaleString()} />
        <Metric name="District Groups" value={districtCount.toLocaleString()} />
        <Metric name="Dealer Coverage" value={`${dealerCount} / 65`} />
      </div>

      <div className="panel plan-filter-tools">
        <label className="field"><span>Scheduled visit date</span><select value={planDate} onChange={e=>{setPlanDate(e.target.value);setPage(0)}}><option>All dates</option>{dateOptions.map(day=><option key={day} value={day}>{day}</option>)}</select></label>
        <label className="field"><span>Region</span><select value={planRegion} onChange={e=>{setPlanRegion(e.target.value);setPlanDealer("All Dealers");setPage(0)}}><option>All Regions</option>{regionOptions.map(value=><option key={value}>{value}</option>)}</select></label>
        <label className="field"><span>Dealer</span><select value={planDealer} onChange={e=>{setPlanDealer(e.target.value);setPage(0)}}><option>All Dealers</option>{dealerOptions.map(value=><option key={value}>{value}</option>)}</select></label>
        <label className="field plan-search"><span>Find a plan row</span><input value={planSearch} onChange={e=>{setPlanSearch(e.target.value);setPage(0)}} placeholder="Employee ID, name, district, commune…"/></label>
      </div>
      <div className="panel table-wrap plan-table-wrap" style={{ marginTop: 16 }}>
        <div className="panel-head plan-table-head"><div><h2>Plan communes</h2></div></div>
        <table className="data-table plan-wide-table">
          <thead><tr>{columns.map(column=><th key={column.key} title={column.label}>{column.label}</th>)}</tr></thead>
          <tbody>
            {pageRows.map((r,i)=><tr key={`${r.source_sheet}-${r.source_row}-${i}`}>
              {columns.map(column=>{
                const value=getValue(r,column.key);
                const distanceText=String(value??"").trim();
                const display=column.key==="dis. from dealer"&&distanceText&&!/(?:km|kilomet(?:er|re)s?)\s*$/i.test(distanceText)?`${distanceText} km`:value;
                return <td key={column.key}>{display}</td>;
              })}
            </tr>)}
            {visibleRows.length===0&&<tr><td className="empty" colSpan={columns.length||1}>No plan rows match these filters.</td></tr>}
          </tbody>
        </table>
        {visibleRows.length>0&&<div className="plan-pagination"><span>Page {currentPage+1} of {pageCount}</span><div><button className="secondary compact" disabled={currentPage===0} onClick={()=>setPage(currentPage-1)}>Previous</button><button className="secondary compact" disabled={currentPage+1>=pageCount} onClick={()=>setPage(currentPage+1)}>Next</button></div></div>}
        {itemRows.length===0&&<div className="empty">No active plan rows found. Upload a validated .xlsx plan to begin.</div>}
      </div>
    </>
  );
}
function Locations() {
  const [region, setRegion] = useState("All Regions"),
    [dealer, setDealer] = useState("All Dealers"),
    [selected, setSelected] = useState("CA2"), [locations,setLocations]=useState([]),
    [latitude,setLatitude]=useState(""),[longitude,setLongitude]=useState(""),[locationError,setLocationError]=useState("");
  useEffect(()=>{adminApi("/api/v1/admin/dealers").then(({items})=>setLocations(items.map(x=>({...dealers.find(d=>d.code===x.dealer),...x,code:x.dealer})).filter(x=>x.code))).catch(e=>setLocationError(e.message))},[]);
  const current=locations.find(d=>d.code===selected);
  useEffect(()=>{if(current){setLatitude(String(current.latitude??""));setLongitude(String(current.longitude??""))}},[selected,locations]);
  const visible = locations.filter(
    (d) =>
      (region === "All Regions" || d.region === region) &&
      (dealer === "All Dealers" || d.code === dealer),
  );
  return (
    <>
      <div className="panel controls">
        <RegionDealerFilters {...{ region, setRegion, dealer, setDealer }} />
        <span className="badge">65 Dealer Stations</span>
      </div>
      <div className="map-grid">
        <div className="panel map">
          <img src="/assets/cambodia-map.png" />
          {visible.map((d) => {
            const position = cambodiaMapPosition(d.latitude, d.longitude);
            if (!position) return null;
            return <button key={d.code} onClick={() => setSelected(d.code)}
              className={"marker " + (selected === d.code ? "selected" : "")}
              style={position} title={`${d.region}-${d.code} · ${d.origin_source || "location"}`}>
              {d.code}
            </button>;
          })}
          {visible.every((d) => !cambodiaMapPosition(d.latitude, d.longitude)) &&
            <div className="empty" style={{ position: "absolute", inset: "40% 0 auto" }}>
              {locationError || "No dealer coordinates are available for these filters."}
            </div>}
        </div>
        <div className="panel">
          <div className="panel-head">
            <div>
              <h2>{selected} Dealer Station</h2>
              
            </div>
            <button className="icon-btn">
              <PencilSimple />
            </button>
          </div>
          <div className="form-grid" style={{ gridTemplateColumns: "1fr" }}>
            <label className="field">
              <span>Dealer Name</span>
              <input value={selected + " Dealer Station"} readOnly />
            </label>
            <label className="field">
              <span>Latitude</span>
              <input type="number" step="0.000001" min="9.8" max="14.75" value={latitude} onChange={e=>setLatitude(e.target.value)} />
            </label>
            <label className="field">
              <span>Longitude</span>
              <input type="number" step="0.000001" min="102.3" max="107.7" value={longitude} onChange={e=>setLongitude(e.target.value)} />
            </label>
            {locationError&&<p className="api-error">{locationError}</p>}
            <button className="primary" disabled={!current} onClick={async()=>{setLocationError("");try{await adminApi(`/api/v1/admin/dealers/${current.region}-${selected}/origin`,{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify({latitude:Number(latitude),longitude:Number(longitude)})});setLocations(rows=>rows.map(x=>x.code===selected?{...x,latitude:Number(latitude),longitude:Number(longitude),origin_source:"dashboard_override"}:x))}catch(e){setLocationError(e.message)}}}>
              <FloppyDisk /> Save Location
            </button>
          </div>
        </div>
      </div>
    </>
  );
}
function SettingsPage() {
  const [s, setS] = useState(null);
  const [settingsLoading,setSettingsLoading]=useState(true);
  const [saving,setSaving]=useState(false);
  const [error,setError]=useState("");
  const [tunnel,setTunnel]=useState({active:false,public_url:"",message:"Checking ngrok…"});
  const refreshTunnel=()=>adminApi("/api/v1/admin/tunnel").then(setTunnel).catch(e=>setTunnel({active:false,public_url:"",message:e.message}));
  useEffect(()=>{
    let active=true;
    adminApi("/api/v1/admin/settings")
      .then(x=>{if(active)setS({...x.questions,automation_enabled:x.automation.enabled,alert_interval_minutes:x.alert_interval_minutes})})
      .catch(e=>{if(active)setError(e.message)})
      .finally(()=>{if(active)setSettingsLoading(false)});
    refreshTunnel();
    return ()=>{active=false};
  },[]);
  async function change(key,value){if(!s||saving)return;setError("");const before=s[key];setS(x=>({...x,[key]:value}));setSaving(true);try{const result=await adminApi("/api/v1/admin/settings",{method:"PATCH",headers:{"Content-Type":"application/json"},body:JSON.stringify({[key]:value})});setS({...result.questions,automation_enabled:result.automation.enabled,alert_interval_minutes:result.alert_interval_minutes})}catch(e){setS(x=>({...x,[key]:before}));setError(e.message)}finally{setSaving(false)}}
  return (
    <div className="panel" style={{ padding: "4px 24px 24px" }}>
      <div className="panel-head" style={{ paddingLeft: 0, paddingRight: 0 }}>
        <div>
          <h2>Alert Rules & Automation</h2>
          
        </div>
      </div>
      {error&&<p className="api-error" role="alert">{error}</p>}
      {settingsLoading ? <div className="empty" role="status">Loading persisted settings…</div> : !s ? <div className="empty">Settings could not be loaded. Correct the API/database error before changing bot automation.</div> : <>
      <div className="settings-row"><div><b>Alert check interval</b></div><select disabled={saving} value={s.alert_interval_minutes} onChange={e=>change("alert_interval_minutes",Number(e.target.value))}>{[5,10,15,20,25,30,35,40,45,50,55,60].map(v=><option key={v} value={v}>{v} minutes</option>)}</select></div>
      {[
        [
          "wrong_plan",
          "Wrong Plan Check-in",
          "Ask when actual commune differs from the uploaded plan.",
        ],
        [
          "over_10km",
          "Road Distance Over 10 km",
          "Alert dealer-to-first and check-in-to-check-in exceptions.",
        ],
        [
          "missing_report_followup",
          "Missing Report",
          "Monitor completed reporting windows and notify controllers.",
        ],
        [
          "reason_acknowledgement",
          "Acknowledge user reasons",
          "Send a confirmation after a user submits a follow-up reason.",
        ],
      ].map(([k, n, d]) => (
        <div key={k} className="settings-row">
          <div>
            <b>{n}</b>
            
          </div>
          <button
            disabled={saving}
            className={"toggle " + (s[k] ? "on" : "")}
            onClick={() => change(k,!s[k])}
          >
            <span />
          </button>
        </div>
      ))}
      <div className="settings-row"><div><b>Automatic bot alerts</b></div><button disabled={saving} className={"toggle "+(s.automation_enabled?"on":"")} onClick={()=>change("automation_enabled",!s.automation_enabled)}><span/></button></div>
      <div className="settings-row"><div><b>Public ngrok dashboard link</b></div><div style={{display:"flex",gap:10,alignItems:"center"}}>{tunnel.active&&<a className="secondary compact" href={tunnel.public_url} target="_blank" rel="noreferrer">Open public dashboard</a>}<button className="secondary compact" onClick={refreshTunnel}>Refresh link</button></div></div>
      </>}
    </div>
  );
}

function ReasonSummary() {
 return <ReasonSummaryPage api={adminApi} RegionDealerFilters={RegionDealerFilters} Metric={Metric}/>;
}

function ALPage() {
  const [region, setRegion] = useState("All Regions");
  const [dealer, setDealer] = useState("All Dealers");
  const initialDay = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Phnom_Penh",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date());
  const [date, setDate] = useState(initialDay);
  const [attendanceSearch, setAttendanceSearch] = useState("");
  const [groupFilter, setGroupFilter] = useState("All Telegram groups");
  const [status, setStatus] = useState("All SO");
  const [rows, setRows] = useState([]);
  const [scheduledCount, setScheduledCount] = useState(0);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [success, setSuccess] = useState("");

  // Set annual leave modal state
  const [modalRow, setModalRow] = useState(null);
  const [modalType, setModalType] = useState("AL Date Range");
  const [modalStart, setModalStart] = useState(initialDay);
  const [modalEnd, setModalEnd] = useState(initialDay);
  const [savingModal, setSavingModal] = useState(false);
  const [modalError, setModalError] = useState("");

  // Saved range viewer modal state
  const [rangesOpen, setRangesOpen] = useState(false);
  const [rangeFrom, setRangeFrom] = useState(initialDay);
  const [rangeTo, setRangeTo] = useState(initialDay);
  const [rangeReportRows, setRangeReportRows] = useState([]);
  const [rangeReportLoading, setRangeReportLoading] = useState(false);

  async function loadRows() {
    const q = new URLSearchParams({ day: date });
    if (region !== "All Regions") q.set("region", region);
    if (dealer !== "All Dealers") q.set("dealer", dealer);
    setLoading(true);
    setError("");
    try {
      const payload = await adminApi(`/api/v1/admin/al?${q}`);
      const items = Array.isArray(payload?.items) ? payload.items : [];
      setRows(
        items.map((x) => ({
          id: x.employee_id,
          telegramId: x.telegram_id,
          name: x.real_name,
          region: x.region,
          dealer: x.dealer,
          role: x.position,
          attendance: x.attendance,
          leaveStatus: x.leave_status || "",
          leaveFrom: x.leave_from || "",
          leaveTo: x.leave_to || "",
          hasCheckin: x.has_checkin,
          fullDayConflict: x.full_day_conflict,
          telegramGroup: x.telegram_group,
          source: x.source,
        })),
      );
      setScheduledCount(Number(payload?.scheduled_count ?? 0));
    } catch (e) {
      setRows([]);
      setScheduledCount(0);
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadRows();
  }, [date, region, dealer]);

  const groupOptions = useMemo(
    () =>
      [...new Set(rows.map((r) => r.telegramGroup).filter((g) => g && g !== "—"))].sort((a, b) =>
        a.localeCompare(b),
      ),
    [rows],
  );

  const attendanceFiltered = useMemo(() => {
    const q = attendanceSearch.trim().toLowerCase();
    return rows
      .filter((r) => {
        if (region !== "All Regions" && r.region !== region) return false;
        if (dealer !== "All Dealers" && r.dealer !== dealer) return false;
        if (groupFilter !== "All Telegram groups" && r.telegramGroup !== groupFilter) return false;
        if (status === "On annual leave" && !r.leaveStatus) return false;
        if (status === "Not on leave" && Boolean(r.leaveStatus)) return false;
        if (
          status !== "All SO" &&
          status !== "On annual leave" &&
          status !== "Not on leave" &&
          r.attendance !== status &&
          r.leaveStatus !== status
        ) {
          return false;
        }
        if (q) {
          const hay = [r.id, r.telegramId, r.name, r.telegramGroup, r.region, r.dealer]
            .join(" ")
            .toLowerCase();
          if (!hay.includes(q)) return false;
        }
        return true;
      })
      .sort(
        (a, b) =>
          (a.name || "").localeCompare(b.name || "") ||
          a.region.localeCompare(b.region) ||
          a.dealer.localeCompare(b.dealer),
      );
  }, [rows, region, dealer, groupFilter, status, attendanceSearch]);

  const alCount = attendanceFiltered.filter((r) => Boolean(r.leaveStatus)).length;

  function openLeaveModal(row) {
    setModalRow(row);
    setModalError("");
    if (row.leaveFrom && row.leaveTo && row.leaveTo !== row.leaveFrom) {
      setModalType("AL Date Range");
      setModalStart(row.leaveFrom);
      setModalEnd(row.leaveTo);
    } else if (row.leaveStatus === "AL Morning" || row.leaveStatus === "AL Afternoon" || row.leaveStatus === "AL Full Day") {
      setModalType("AL Date Range");
      setModalStart(row.leaveFrom || date);
      setModalEnd(row.leaveTo || date);
    } else {
      setModalType("AL Date Range");
      setModalStart(date);
      setModalEnd(date);
    }
  }

  async function saveLeaveModal(event) {
    event?.preventDefault();
    if (!modalRow || savingModal) return;
    setModalError("");
    setSuccess("");
    const isRangeMode = modalType === "AL Date Range" || modalType === "Clear annual leave";
    const startVal = modalStart;
    const endVal = isRangeMode ? modalEnd : modalStart;
    if (!startVal || !endVal || endVal < startVal) {
      setModalError("End date must be on or after Start date.");
      return;
    }
    const days = (Date.parse(`${endVal}T00:00:00Z`) - Date.parse(`${startVal}T00:00:00Z`)) / 86400000 + 1;
    if (!Number.isFinite(days) || days < 1 || days > 90) {
      setModalError("Choose a date range of 1 to 90 days.");
      return;
    }
    setSavingModal(true);
    try {
      if (modalType === "Clear annual leave") {
        if (startVal === endVal) {
          await adminApi(`/api/v1/admin/al/${encodeURIComponent(modalRow.id)}`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ day: startVal, attendance: "Clear dashboard AL" }),
          });
        } else {
          await adminApi("/api/v1/admin/al/range", {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              start: startVal,
              end: endVal,
              employee_ids: [modalRow.id],
              attendance: "Clear dashboard AL",
            }),
          });
        }
        setSuccess(`Annual leave cleared for ${modalRow.name} (${startVal}${endVal !== startVal ? ` to ${endVal}` : ""}).`);
      } else if (modalType === "AL Date Range") {
        const result = await adminApi("/api/v1/admin/al/range", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            start: startVal,
            end: endVal,
            employee_ids: [modalRow.id],
            attendance: "AL Full Day",
          }),
        });
        setSuccess(`AL Date Range saved for ${modalRow.name} (${result.from_date} to ${result.to_date}, ${result.days} day(s)).`);
      } else {
        await adminApi(`/api/v1/admin/al/${encodeURIComponent(modalRow.id)}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ day: startVal, attendance: modalType }),
        });
        setSuccess(`${modalType} saved for ${modalRow.name} on ${startVal}.`);
      }
      setModalRow(null);
      await loadRows();
    } catch (e) {
      setModalError(e.message);
    } finally {
      setSavingModal(false);
    }
  }

  async function loadRangeReport() {
    setError("");
    const days = (Date.parse(`${rangeTo}T00:00:00Z`) - Date.parse(`${rangeFrom}T00:00:00Z`)) / 86400000 + 1;
    if (!Number.isFinite(days) || days < 1 || days > 90) {
      setError("Choose an ordered date range of 1 to 90 days.");
      return;
    }
    const q = new URLSearchParams({ start: rangeFrom, end: rangeTo });
    if (region !== "All Regions") q.set("region", region);
    if (dealer !== "All Dealers") q.set("dealer", dealer);
    setRangeReportLoading(true);
    try {
      const result = await adminApi(`/api/v1/admin/al/range?${q}`);
      setRangeReportRows(result.items || []);
    } catch (e) {
      setError(e.message);
    } finally {
      setRangeReportLoading(false);
    }
  }

  const initialsOf = (name) =>
    String(name || "?")
      .trim()
      .split(/\s+/)
      .map((x) => x[0] || "")
      .join("")
      .slice(0, 2)
      .toUpperCase() || "?";

  return (
    <div className="al-page">
      <div className="section-title al-page-title">
        <div>
          <h2>Annual Leave</h2>
        </div>
        <div className="sa-header-actions">
          <button
            type="button"
            className="secondary"
            onClick={() => {
              setRangesOpen(true);
              loadRangeReport();
            }}
          >
            Scheduled ranges
          </button>
          <button type="button" className="secondary al-print-button" onClick={() => window.print()}>
            Export / Print
          </button>
        </div>
      </div>

      {error && (
        <p className="api-error" role="alert">
          {error}
        </p>
      )}
      {success && (
        <p className="al-success" role="status">
          {success}
        </p>
      )}

      <div className="sa-al-kpis">
        <div className="sa-al-kpi">
          <strong>{loading ? "…" : error ? "—" : alCount}</strong>
          <span>On annual leave now</span>
        </div>
        <div className="sa-al-kpi">
          <strong>{loading ? "…" : error ? "—" : scheduledCount}</strong>
          <span>Scheduled leave</span>
        </div>
        <div className="sa-al-kpi">
          <strong>{loading ? "…" : error ? "—" : attendanceFiltered.length}</strong>
          <span>SO in roster</span>
        </div>
      </div>

      <section className="panel sa-card-unified">
        <div className="sa-filter-bar">
          <label className="field sa-filter-search">
            <span>Search SO</span>
            <input
              value={attendanceSearch}
              onChange={(e) => setAttendanceSearch(e.target.value)}
              placeholder="Search SO, Employee ID or Telegram ID…"
            />
          </label>
          <label className="field">
            <span>Date</span>
            <input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
          </label>
          <RegionDealerFilters {...{ region, setRegion, dealer, setDealer }} />
          <label className="field">
            <span>Group</span>
            <select value={groupFilter} onChange={(e) => setGroupFilter(e.target.value)}>
              <option value="All Telegram groups">All Telegram groups</option>
              {groupOptions.map((g) => (
                <option key={g} value={g}>
                  {g}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Filter</span>
            <select value={status} onChange={(e) => setStatus(e.target.value)}>
              <option value="All SO">All SO</option>
              <option value="On annual leave">On annual leave</option>
              <option value="Not on leave">Not on leave</option>
              <option value="Present">Present</option>
              <option value="Not Reported">Not Reported</option>
              <option value="Scheduled">Scheduled</option>
              <option value="AL Full Day">AL Full Day</option>
              <option value="AL Morning">AL Morning</option>
              <option value="AL Afternoon">AL Afternoon</option>
            </select>
          </label>
          <span className="sa-member-count">{attendanceFiltered.length} members</span>
        </div>

        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>SO NAME</th>
                <th>EMPLOYEE ID</th>
                <th>TELEGRAM ID</th>
                <th>GROUP</th>
                <th>ATTENDANCE / LEAVE</th>
                <th>CHECK-IN</th>
                <th>ACTION</th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr>
                  <td className="empty" colSpan={7}>
                    Loading SO roster…
                  </td>
                </tr>
              ) : (
                attendanceFiltered.map((r) => (
                  <tr key={r.id}>
                    <td>
                      <div className="sa-name-cell">
                        <div className="sa-avatar">
                          <span>{initialsOf(r.name)}</span>
                          {r.telegramId && (
                            <img
                              src={`/api/v1/dashboard/telegram-avatar/${encodeURIComponent(r.telegramId)}`}
                              alt=""
                              loading="lazy"
                              onError={(e) => {
                                e.currentTarget.style.display = "none";
                              }}
                            />
                          )}
                        </div>
                        <div className="sa-name-copy">
                          <b>{r.name}</b>
                          <small>
                            {r.role || "SO"} · {r.dealer || "—"}
                          </small>
                        </div>
                      </div>
                    </td>
                    <td className="sa-mono">{r.id || "—"}</td>
                    <td className="sa-mono">{r.telegramId || "—"}</td>
                    <td>{r.telegramGroup || `${r.region}-${r.dealer}`}</td>
                    <td>
                      <span
                        className={
                          "status " +
                          (r.leaveStatus ? "danger" : "") +
                          (r.fullDayConflict ? " status-conflict" : "")
                        }
                      >
                        {r.fullDayConflict
                          ? "AL Full Day · Check-in conflict"
                          : r.leaveStatus && r.leaveFrom && r.leaveTo && r.leaveTo !== r.leaveFrom
                            ? `${r.leaveStatus} (${r.leaveFrom} → ${r.leaveTo})`
                            : r.attendance}
                      </span>
                    </td>
                    <td>{r.hasCheckin ? "Yes" : "No"}</td>
                    <td>
                      <button
                        type="button"
                        className="secondary sa-set-leave-btn"
                        onClick={() => openLeaveModal(r)}
                      >
                        {r.leaveStatus ? "Edit leave" : "Set leave"}
                      </button>
                    </td>
                  </tr>
                ))
              )}
              {!loading && !error && attendanceFiltered.length === 0 && (
                <tr>
                  <td className="empty" colSpan={7}>
                    No matching SO members.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      {modalRow && (
        <div
          className="sa-modal-backdrop"
          role="dialog"
          aria-modal="true"
          aria-labelledby="sa-al-modal-title"
          onClick={(e) => {
            if (e.target === e.currentTarget && !savingModal) setModalRow(null);
          }}
        >
          <form className="sa-modal" onSubmit={saveLeaveModal}>
            <div className="sa-modal-head">
              <div>
                <div className="sa-modal-eyebrow">
                  ( {modalRow.name} ) ( {modalRow.telegramGroup || `${modalRow.region}-${modalRow.dealer}`} )
                </div>
                <h3 id="sa-al-modal-title">Set annual leave</h3>
              </div>
              <button
                type="button"
                className="sa-modal-close"
                aria-label="Close"
                disabled={savingModal}
                onClick={() => setModalRow(null)}
              >
                ×
              </button>
            </div>
            <div className="sa-modal-body">
              {modalError && (
                <p className="api-error" role="alert">
                  {modalError}
                </p>
              )}
              <label className="field">
                <span>Leave type</span>
                <select value={modalType} onChange={(e) => setModalType(e.target.value)}>
                  <option value="AL Date Range">AL Date Range</option>
                  <option value="AL Full Day">AL Full Day</option>
                  <option value="AL Morning">AL Morning</option>
                  <option value="AL Afternoon">AL Afternoon</option>
                  <option value="Clear annual leave">Clear annual leave</option>
                </select>
              </label>

              {modalType === "AL Date Range" || modalType === "Clear annual leave" ? (
                <>
                  <label className="field">
                    <span>Start date</span>
                    <input
                      type="date"
                      value={modalStart}
                      onChange={(e) => {
                        const v = e.target.value;
                        setModalStart(v);
                        if (modalEnd < v) setModalEnd(v);
                      }}
                      required
                    />
                  </label>
                  <label className="field">
                    <span>
                      End date <small className="sa-muted-inline">(inclusive)</small>
                    </span>
                    <input
                      type="date"
                      value={modalEnd}
                      min={modalStart || undefined}
                      onChange={(e) => setModalEnd(e.target.value)}
                      required
                    />
                  </label>
                </>
              ) : (
                <label className="field">
                  <span>Date</span>
                  <input
                    type="date"
                    value={modalStart}
                    onChange={(e) => setModalStart(e.target.value)}
                    required
                  />
                </label>
              )}

              <p className="sa-modal-hint">
                {modalType === "Clear annual leave"
                  ? "Removes dashboard-scheduled annual leave overrides across the selected dates."
                  : "The bot will skip scheduled missing-report alerts from the start date through the end date."}
              </p>
            </div>
            <div className="sa-modal-footer">
              <button
                type="button"
                className="sa-btn-ghost"
                disabled={savingModal}
                onClick={() => setModalRow(null)}
              >
                Cancel
              </button>
              <button type="submit" className="sa-btn-emerald" disabled={savingModal}>
                {savingModal ? "Saving…" : "✓ Save changes"}
              </button>
            </div>
          </form>
        </div>
      )}

      {rangesOpen && (
        <div
          className="sa-modal-backdrop"
          role="dialog"
          aria-modal="true"
          aria-labelledby="sa-ranges-title"
          onClick={(e) => {
            if (e.target === e.currentTarget) setRangesOpen(false);
          }}
        >
          <div className="sa-modal sa-modal--wide">
            <div className="sa-modal-head">
              <div>
                <div className="sa-modal-eyebrow">
                  {rangeFrom} to {rangeTo} · {rangeReportRows.length} entries
                </div>
                <h3 id="sa-ranges-title">Scheduled leave ranges</h3>
              </div>
              <button
                type="button"
                className="sa-modal-close"
                aria-label="Close"
                onClick={() => setRangesOpen(false)}
              >
                ×
              </button>
            </div>
            <div className="sa-modal-toolbar">
              <label className="field">
                <span>From date</span>
                <input
                  type="date"
                  value={rangeFrom}
                  max={rangeTo || undefined}
                  onChange={(e) => setRangeFrom(e.target.value)}
                />
              </label>
              <label className="field">
                <span>To date</span>
                <input
                  type="date"
                  value={rangeTo}
                  min={rangeFrom || undefined}
                  onChange={(e) => setRangeTo(e.target.value)}
                />
              </label>
              <button
                type="button"
                className="secondary"
                disabled={rangeReportLoading}
                onClick={loadRangeReport}
              >
                {rangeReportLoading ? "Loading…" : "Load ranges"}
              </button>
            </div>
            <div className="sa-modal-table-wrap">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>EMPLOYEE ID</th>
                    <th>SO NAME</th>
                    <th>GROUP</th>
                    <th>REGION</th>
                    <th>DEALER</th>
                    <th>AL TYPE</th>
                    <th>FROM DATE</th>
                    <th>TO DATE</th>
                    <th>DAYS</th>
                    <th>SOURCE</th>
                  </tr>
                </thead>
                <tbody>
                  {rangeReportLoading ? (
                    <tr>
                      <td className="empty" colSpan={10}>
                        Loading saved leave…
                      </td>
                    </tr>
                  ) : rangeReportRows.length ? (
                    rangeReportRows.map((r, index) => (
                      <tr key={`${r.employee_id}-${r.from_date}-${r.to_date}-${r.source}-${index}`}>
                        <td>{r.employee_id}</td>
                        <td>{r.real_name}</td>
                        <td>{r.telegram_group}</td>
                        <td>{r.region}</td>
                        <td>{r.dealer}</td>
                        <td>
                          <span className="status danger">{r.leave_type}</span>
                        </td>
                        <td>{r.from_date}</td>
                        <td>{r.to_date}</td>
                        <td>{r.days}</td>
                        <td>{r.source}</td>
                      </tr>
                    ))
                  ) : (
                    <tr>
                      <td className="empty" colSpan={10}>
                        No saved AL records found for this date range.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default function App() {
  const [page, setPage] = useState("Live Map Check In");
  const [mapTarget, setMapTarget] = useState(null);
  const [apiHealth, setApiHealth] = useState("checking");
  useEffect(() => {
    let active = true;
    const checkHealth = async () => {
      try {
        const response = await fetch("/health", { cache: "no-store" });
        const result = response.ok ? await response.json() : null;
        if (active) setApiHealth(result?.status === "ready" && result?.database ? "ready" : "unavailable");
      } catch {
        if (active) setApiHealth("unavailable");
      }
    };
    checkHealth();
    const timer = setInterval(checkHealth, 30000);
    return () => { active = false; clearInterval(timer); };
  }, []);
  const content =
    page === "Live Map Check In" ? (
      <LiveMap target={mapTarget} />
    ) : page === "Performance" ? (
      <Performance />
    ) : page === "Master Sale" ? (
      <Master />
    ) : page === "Plan Sale" ? (
      <Plan />
    ) : page === "AL" ? (
      <ALPage />
    ) : page === "Report GP" ? (
      <GPReport api={adminApi} />
    ) : page === "Reason Summary" ? (
      <ReasonSummary />
    ) : (
      <SettingsPage />
    );
  return (
    <div className="app">
      <Sidebar {...{ page, setPage, apiHealth }} />
      <main className="main">
        <Header page={page} apiHealth={apiHealth} onOpenAlert={item=>{setMapTarget({...item, nonce:Date.now()});setPage("Live Map Check In");}} />
        <div className="content">
          
          <AnimatePresence mode="wait">
            <motion.div
              className={`workspace-page page-${page.toLowerCase().replaceAll(' ', '-')}`}
              key={`${page}:${page === "Live Map Check In" ? mapTarget?.nonce || 0 : 0}`}
              initial={{ opacity: 0, y: 7 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -5 }}
              transition={{ duration: 0.2 }}
            >
              <DashboardErrorBoundary resetKey={page}>{content}</DashboardErrorBoundary>
            </motion.div>
          </AnimatePresence>
        </div>
      </main>
    </div>
  );
}


