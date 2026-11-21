import TelegramConnections from './TelegramConnections.jsx';
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
  DownloadSimple,
  List,
  X,
  Check,
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
  PieChart,
  Pie,
  Cell,
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
            <div className="notification-heading"><div><b>Late check-ins (after 9:00 AM)</b><small>First check-in 9:01 AM+ · {firstCheckAlerts?.date || "Today"}</small></div>
              <span className={firstCheckAlerts?.count ? "notification-total is-alert" : "notification-total"}>{firstCheckAlerts?.count || 0}</span></div>
            {firstCheckAlerts?.error ? <p className="notification-empty">Alerts could not be loaded. Try again shortly.</p> :
              firstCheckAlerts?.deadline_passed === false ? <p className="notification-empty">Late check-in monitoring starts after 9:00 AM ICT.</p> :
              firstCheckAlerts?.items?.length ? <div className="notification-list">{firstCheckAlerts.items.map(item => <button type="button" className="notification-item notification-action" key={`${item.employee_id}-${item.telegram_id || ""}`} onClick={() => { onOpenAlert(item); setNotificationOpen(false); }} aria-label={`Show ${item.real_name || item.employee_id} on live map`}>
                <div className="notification-item-top"><b>{item.real_name || "Sales user"}</b><span className="notification-kind is-late">Late ({item.first_check_time})</span></div>
                <small>{item.employee_id} · {item.region} / {item.dealer} · {item.telegram_group}</small>
                <p>First check-in at {item.first_check_time}{item.minutes_late ? ` (${item.minutes_late} min late)` : ""}<span className="notification-open-map">View on map ↗</span></p>
              </button>)}</div> : <p className="notification-empty">No late check-ins after 9:00 AM.</p>}
          </section>}
        </div>
        <span className={`sa-conn-pill ${apiHealth !== "ready" ? "is-warn" : ""}`}>
          <i aria-hidden="true" />
          {apiHealth === "ready" ? "Database connected" : apiHealth === "checking" ? "Checking…" : "Unavailable"}
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
  params.set("ui", "20261120-connections");
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
  const today = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Phnom_Penh", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());
  const [region, setRegion] = useState("All Regions");
  const [dealer, setDealer] = useState("All Dealers");
  const [granularity, setGranularity] = useState("Daily");
  const [tab, setTab] = useState("SO Summary");
  const [search, setSearch] = useState("");
  const [pageIdx, setPageIdx] = useState(1);
  const [end, setEnd] = useState(today);
  const [start, setStart] = useState(() => {
    const d = new Date(`${today}T00:00:00Z`);
    d.setUTCDate(1);
    return d.toISOString().slice(0, 10);
  });
  const [report, setReport] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    const q = new URLSearchParams({ start, end });
    if (region !== "All Regions") q.set("region", region);
    if (dealer !== "All Dealers") q.set("dealer", dealer);
    setLoading(true);
    setError("");
    setReport(null);
    adminApi(`/api/v1/admin/performance?${q}`)
      .then((data) => { if (active) setReport(data); })
      .catch((e) => { if (active) { setReport(null); setError(e.message); } })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [start, end, region, dealer]);

  useEffect(() => { setPageIdx(1); }, [tab, search, region, dealer, start, end]);

  const eyebrowDate = useMemo(() => {
    try {
      return new Intl.DateTimeFormat("en-US", {
        timeZone: "Asia/Phnom_Penh",
        weekday: "long",
        month: "long",
        day: "numeric",
        year: "numeric",
      }).format(new Date()).toUpperCase();
    } catch {
      return today;
    }
  }, [today]);

  const fmtDMY = (iso) => {
    if (!iso || iso.length < 10) return iso || "";
    const [y, m, d] = iso.slice(0, 10).split("-");
    return `${d}/${m}/${y}`;
  };

  const summary = useMemo(
    () => (report?.by_region || []).filter((x) => region === "All Regions" || x.region === region),
    [report, region],
  );
  const dealerRows = useMemo(
    () => (report?.by_dealer || []).filter((x) => (region === "All Regions" || x.region === region) && (dealer === "All Dealers" || x.dealer === dealer)),
    [report, region, dealer],
  );
  const individualRows = useMemo(() => {
    const q = search.trim().toLowerCase();
    return (report?.individuals || []).filter((x) => {
      if (region !== "All Regions" && x.region !== region) return false;
      if (dealer !== "All Dealers" && x.dealer !== dealer) return false;
      if (q) {
        const hay = [x.employee_id, x.telegram_id, x.real_name, x.region, x.dealer, x.telegram_group, x.controller].join(" ").toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
  }, [report, region, dealer, search]);

  const trendSeries = useMemo(() => {
    const raw = report?.timeline || [];
    if (granularity === "Daily") return raw;
    const buckets = new Map();
    for (const pt of raw) {
      const d = new Date(`${pt.date}T00:00:00Z`);
      let key = pt.date;
      let label = pt.label;
      if (granularity === "Weekly") {
        const dayOfWeek = (d.getUTCDay() + 6) % 7;
        d.setUTCDate(d.getUTCDate() - dayOfWeek);
        key = d.toISOString().slice(0, 10);
        label = `Wk ${d.toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" })}`;
      } else {
        key = pt.date.slice(0, 7);
        label = d.toLocaleDateString("en-US", { month: "short", year: "numeric", timeZone: "UTC" });
      }
      const cur = buckets.get(key) || { date: key, label, checkins: 0, miss_reports: 0, al_count: 0 };
      cur.checkins += Number(pt.checkins || 0);
      cur.miss_reports += Number(pt.miss_reports || 0);
      cur.al_count += Number(pt.al_count || 0);
      buckets.set(key, cur);
    }
    return [...buckets.values()];
  }, [report, granularity]);

  const totalCheckins = Number(report?.totals?.checkins ?? 0);
  const totalMiss = Number(report?.totals?.miss_reports ?? 0);
  const totalAL = Number(report?.totals?.al_days ?? report?.al_estimate ?? 0);
  const overallMix = useMemo(() => {
    const total = totalCheckins + totalMiss + totalAL;
    const pct = (v) => (total > 0 ? `${((v / total) * 100).toFixed(1)}%` : "0%");
    return [
      { name: "Check In", value: totalCheckins, color: "#0353a4", pct: pct(totalCheckins) },
      { name: "Miss Report", value: totalMiss, color: "#d96b43", pct: pct(totalMiss) },
      { name: "Annual Leave", value: totalAL, color: "#6366f1", pct: pct(totalAL) },
    ];
  }, [totalCheckins, totalMiss, totalAL]);

  const PIE_COLORS = ["#156454", "#0353a4", "#d96b43", "#6366f1", "#16796c", "#e09f3e", "#7a9e9f", "#8d5b4c"];
  const buildBreakdown = (field) => {
    const source = region === "All Regions" ? summary : dealerRows;
    const rows = source
      .map((r) => ({ name: region === "All Regions" ? r.region : `${r.region}-${r.dealer}`, value: Number(r[field] || 0) }))
      .filter((r) => r.value > 0)
      .sort((a, b) => b.value - a.value);
    const total = rows.reduce((s, r) => s + r.value, 0);
    return rows.slice(0, 6).map((r, i) => ({
      ...r,
      color: PIE_COLORS[i % PIE_COLORS.length],
      pct: total > 0 ? `${((r.value / total) * 100).toFixed(1)}%` : "0%",
    }));
  };

  const checkinBreakdown = useMemo(() => buildBreakdown("checkins"), [summary, dealerRows, region]);
  const missBreakdown = useMemo(() => buildBreakdown("miss_reports"), [summary, dealerRows, region]);
  const alBreakdown = useMemo(() => buildBreakdown("al_count"), [summary, dealerRows, region]);

  const pageSize = 20;
  const totalPages = Math.max(1, Math.ceil(individualRows.length / pageSize));
  const pagedIndividuals = individualRows.slice((pageIdx - 1) * pageSize, pageIdx * pageSize);

  const initialsOf = (name) =>
    String(name || "?").trim().split(/\s+/).map((x) => x[0] || "").join("").slice(0, 2).toUpperCase() || "?";

  function exportRows(targetTab = tab) {
    const items = targetTab === "By Region" ? summary : targetTab === "By Dealer" ? dealerRows : individualRows;
    if (!items.length) return;
    const keys = Object.keys(items[0]);
    const csv = [keys.join(","), ...items.map((x) => keys.map((k) => `"${String(x[k] ?? "").replaceAll('"', '""')}"`).join(","))].join("\n");
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob(["\ufeff" + csv], { type: "text/csv;charset=utf-8" }));
    a.download = `SO_Operations_${targetTab.replaceAll(" ", "_")}_${start}_${end}.csv`;
    a.click();
    URL.revokeObjectURL(a.href);
  }

  return (
    <div className="sa-perf-page">
      <div className="sa-perf-header">
        <div>
          <div className="sa-perf-eyebrow">— {eyebrowDate}</div>
          <h2 className="sa-perf-title">Operations overview</h2>
          <p className="sa-perf-sub">A live view of your field team and reporting activity.</p>
        </div>
        <div className="sa-perf-controls">
          <label className="sa-date-pill">
            <span>From Date</span>
            <input type="date" value={start} max={end} onChange={(e) => setStart(e.target.value)} />
          </label>
          <label className="sa-date-pill">
            <span>To Date</span>
            <input type="date" value={end} min={start} max={today} onChange={(e) => setEnd(e.target.value)} />
          </label>
          <button type="button" className="sa-btn-emerald" onClick={() => exportRows("SO Summary")} disabled={loading || !report}>
            <DownloadSimple size={16} weight="bold" />
            <span>Export report</span>
          </button>
        </div>
      </div>

      {error && <p className="api-error" role="alert">{error}</p>}

      <div className="sa-analyst-bar">
        <div>
          <h3 className="sa-analyst-heading">Performance Analyst</h3>
          <p className="sa-analyst-sub">{fmtDMY(start)} – {fmtDMY(end)} · Check In, Miss Report &amp; Annual Leave</p>
        </div>
        <div className="sa-analyst-filters">
          <RegionDealerFilters {...{ region, setRegion, dealer, setDealer }} />
          <div className="sa-analyst-pill-group" role="tablist" aria-label="Time granularity">
            {["Daily", "Weekly", "Monthly"].map((g) => (
              <button key={g} type="button" role="tab" aria-selected={granularity === g}
                className={`sa-analyst-pill ${granularity === g ? "active" : ""}`}
                onClick={() => setGranularity(g)}>{g}</button>
            ))}
          </div>
        </div>
      </div>

      <div className="sa-analyst-main-grid">
        <div className="panel sa-card-unified">
          <div className="panel-head">
            <div>
              <h2>Activity Trend ({granularity})</h2>
              <p>Check In, Miss Report &amp; Annual Leave over time</p>
            </div>
          </div>
          <div className="chart-wrap">
            {loading ? <div className="empty" role="status">Loading performance trend…</div> : error ? <div className="empty">Data unavailable.</div> : (
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={trendSeries}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#e6eeea" vertical={false} />
                  <XAxis dataKey="label" tick={{ fontSize: 11, fill: "#677975" }} />
                  <YAxis tick={{ fontSize: 11, fill: "#677975" }} />
                  <Tooltip />
                  <Legend />
                  <Line type="monotone" dataKey="checkins" name="Check In" stroke="#0353a4" strokeWidth={2.5} dot={{ r: 3 }} />
                  <Line type="monotone" dataKey="miss_reports" name="Miss Report" stroke="#d96b43" strokeWidth={2.5} dot={{ r: 3 }} />
                  <Line type="monotone" dataKey="al_count" name="Annual Leave" stroke="#6366f1" strokeWidth={2.5} dot={{ r: 3 }} />
                </LineChart>
              </ResponsiveContainer>
            )}
          </div>
        </div>

        <div className="panel sa-card-unified">
          <div className="panel-head">
            <div>
              <h2>Overall Mix · Pie Chart</h2>
              <p>Share of Check In, Miss Report &amp; Annual Leave</p>
            </div>
          </div>
          <div className="sa-mix-body">
            {loading ? <div className="empty" role="status">Loading mix…</div> : error ? <div className="empty">Data unavailable.</div> : (
              <>
                <div className="sa-pie-box">
                  <ResponsiveContainer width="100%" height={220}>
                    <PieChart>
                      <Pie data={overallMix} dataKey="value" nameKey="name" cx="50%" cy="50%" innerRadius={54} outerRadius={84} paddingAngle={2}>
                        {overallMix.map((entry) => <Cell key={entry.name} fill={entry.color} />)}
                      </Pie>
                      <Tooltip />
                    </PieChart>
                  </ResponsiveContainer>
                </div>
                <div className="sa-mix-legend">
                  {overallMix.map((item) => (
                    <div className="sa-mix-row" key={item.name}>
                      <span className="sa-mix-dot" style={{ background: item.color }} />
                      <span className="sa-mix-name">{item.name}</span>
                      <strong className="sa-mix-val">{item.value.toLocaleString()}</strong>
                      <span className="sa-mix-pct">{item.pct}</span>
                    </div>
                  ))}
                </div>
              </>
            )}
          </div>
        </div>
      </div>

      <div className="sa-perf-triple-grid">
        {[
          { title: "Performance Check In", total: totalCheckins, accent: "#0353a4", rows: checkinBreakdown },
          { title: "Performance Miss Report", total: totalMiss, accent: "#d96b43", rows: missBreakdown },
          { title: "Performance AL", total: totalAL, accent: "#6366f1", rows: alBreakdown },
        ].map((card) => (
          <div className="panel sa-card-unified sa-perf-metric-card" key={card.title}>
            <div className="sa-perf-card-top">
              <div>
                <div className="sa-perf-card-label">{card.title}</div>
                <div className="sa-perf-metric-value" style={{ color: card.accent }}>
                  {loading ? "…" : card.total.toLocaleString()}
                </div>
              </div>
              <div className="sa-perf-card-pills">
                <span>{granularity}</span>
                <span>{region === "All Regions" ? "All Regions" : region}</span>
              </div>
            </div>
            {loading ? <div className="empty">Loading…</div> : card.rows.length ? (
              <div className="sa-perf-card-body">
                <div className="sa-mini-pie">
                  <ResponsiveContainer width="100%" height={130}>
                    <PieChart>
                      <Pie data={card.rows} dataKey="value" nameKey="name" cx="50%" cy="50%" innerRadius={30} outerRadius={52} paddingAngle={2}>
                        {card.rows.map((entry) => <Cell key={entry.name} fill={entry.color} />)}
                      </Pie>
                      <Tooltip />
                    </PieChart>
                  </ResponsiveContainer>
                </div>
                <div className="sa-perf-mini-list">
                  {card.rows.map((r) => (
                    <div className="sa-perf-breakdown-row" key={r.name}>
                      <span className="sa-mix-dot" style={{ background: r.color }} />
                      <span className="sa-breakdown-name">{r.name}</span>
                      <b>{r.value.toLocaleString()}</b>
                      <small>{r.pct}</small>
                    </div>
                  ))}
                </div>
              </div>
            ) : <div className="empty">No records in range.</div>}
          </div>
        ))}
      </div>

      <div className="panel sa-card-unified" style={{ marginTop: 16 }}>
        <div className="panel-head sa-recent-head">
          <div>
            <h2>Recent activity · SO Visit &amp; Miss Summary</h2>
            <p>{individualRows.length.toLocaleString()} SO in view · {summary.length} regions · {dealerRows.length} dealers</p>
          </div>
          <div className="sa-recent-tools">
            <div className="tabs">
              {["SO Summary", "By Region", "By Dealer"].map((t) => (
                <button key={t} type="button" className={tab === t ? "active" : ""} onClick={() => setTab(t)}>{t}</button>
              ))}
            </div>
            {tab === "SO Summary" && (
              <div className="sa-inline-search">
                <MagnifyingGlass size={15} />
                <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search SO, ID, group…" />
              </div>
            )}
            <button type="button" className="secondary" onClick={() => exportRows(tab)} disabled={loading || !report}>Export CSV</button>
          </div>
        </div>

        <div className="table-wrap">
          {tab === "By Region" ? (
            <table className="data-table">
              <thead>
                <tr>
                  <th>Region</th><th>Roster</th><th>Active SO</th><th>Check-ins</th><th>Miss Reports</th>
                  <th>Districts</th><th>Communes</th><th>Over 10 km</th><th>AL</th><th>Completion</th>
                </tr>
              </thead>
              <tbody>
                {summary.map((r) => (
                  <tr key={r.region}>
                    <td><span className="region-tag">{r.region}</span></td>
                    <td>{r.roster_count}</td>
                    <td>{r.active_sale}</td>
                    <td><b>{Number(r.checkins).toLocaleString()}</b></td>
                    <td>{Number(r.miss_reports || 0).toLocaleString()}</td>
                    <td>{r.districts}</td><td>{r.communes}</td><td>{r.over_10km}</td><td>{r.al_count}</td>
                    <td><span className="status">{r.roster_count ? `${Math.round((100 * r.active_sale) / r.roster_count)}%` : "—"}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : tab === "By Dealer" ? (
            <table className="data-table">
              <thead>
                <tr>
                  <th>Region</th><th>Dealer</th><th>Roster</th><th>Active SO</th><th>Check-ins</th>
                  <th>Miss Reports</th><th>Districts</th><th>Communes</th><th>Over 10 km</th><th>AL</th>
                </tr>
              </thead>
              <tbody>
                {dealerRows.map((r) => (
                  <tr key={`${r.region}-${r.dealer}`}>
                    <td>{r.region}</td>
                    <td><span className="dealer-tag">{r.dealer}</span></td>
                    <td>{r.roster_count}</td><td>{r.active_sale}</td>
                    <td><b>{Number(r.checkins).toLocaleString()}</b></td>
                    <td>{Number(r.miss_reports || 0).toLocaleString()}</td>
                    <td>{r.districts}</td><td>{r.communes}</td><td>{r.over_10km}</td><td>{r.al_count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <>
              <table className="data-table">
                <thead>
                  <tr>
                    <th>SO NAME</th><th>EMPLOYEE ID</th><th>TELEGRAM ID</th><th>GROUP</th><th>CONTROLLER</th>
                    <th>CHECK IN</th><th>MISS REPORT</th><th>AL</th><th>OVER 10 KM</th><th>STATUS</th>
                  </tr>
                </thead>
                <tbody>
                  {loading ? (
                    <tr><td className="empty" colSpan={10}>Loading SO summary…</td></tr>
                  ) : pagedIndividuals.length ? pagedIndividuals.map((x) => (
                    <tr key={`${x.employee_id || x.telegram_id}-${x.region}-${x.dealer}`}>
                      <td>
                        <div className="sa-name-cell">
                          <div className="sa-avatar">
                            <span>{initialsOf(x.real_name)}</span>
                            {x.telegram_id && (
                              <img src={`/api/v1/dashboard/telegram-avatar/${encodeURIComponent(x.telegram_id)}`} alt="" loading="lazy"
                                onError={(e) => { e.currentTarget.style.display = "none"; }} />
                            )}
                          </div>
                          <div className="sa-name-copy">
                            <b>{x.real_name}</b>
                            <small>{x.position || "SO"} · {x.dealer || "—"}</small>
                          </div>
                        </div>
                      </td>
                      <td className="sa-mono">{x.employee_id || "—"}</td>
                      <td className="sa-mono">{x.telegram_id || "—"}</td>
                      <td>{x.telegram_group || `${x.region}-${x.dealer}`}</td>
                      <td>{x.controller || "—"}</td>
                      <td><b>{Number(x.checkins || 0).toLocaleString()}</b></td>
                      <td>{Number(x.miss_count || 0).toLocaleString()}</td>
                      <td>{Number(x.al_days || 0)}</td>
                      <td>{Number(x.over_10km || 0)}</td>
                      <td>
                        <span className={`sa-al-status-pill ${x.attendance === "Present" ? "is-scheduled" : x.attendance === "AL" ? "is-on-leave" : "is-no-leave"}`}>
                          <i>•</i> {x.attendance}
                        </span>
                      </td>
                    </tr>
                  )) : (
                    <tr><td className="empty" colSpan={10}>No matching SO records for this period.</td></tr>
                  )}
                </tbody>
              </table>
              {individualRows.length > pageSize && (
                <div className="plan-pagination" style={{ padding: "12px 16px", borderTop: "1px solid #edf1ed" }}>
                  <span>Showing {(pageIdx - 1) * pageSize + 1}–{Math.min(pageIdx * pageSize, individualRows.length)} of {individualRows.length}</span>
                  <div style={{ display: "flex", gap: 8 }}>
                    <button type="button" className="secondary" disabled={pageIdx <= 1} onClick={() => setPageIdx((p) => Math.max(1, p - 1))}>Previous</button>
                    <button type="button" className="secondary" disabled={pageIdx >= totalPages} onClick={() => setPageIdx((p) => Math.min(totalPages, p + 1))}>Next</button>
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
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
  const DEFAULT_DAYS = { mon: true, tue: true, wed: true, thu: true, fri: true, sat: false, sun: false };
  const DAY_LABELS = [
    ["mon", "Mon"], ["tue", "Tue"], ["wed", "Wed"], ["thu", "Thu"],
    ["fri", "Fri"], ["sat", "Sat"], ["sun", "Sun"],
  ];
  useEffect(()=>{
    let active=true;
    adminApi("/api/v1/admin/settings")
      .then(x=>{if(active)setS({
        ...x.questions,
        automation_enabled:x.automation.configured_enabled ?? x.automation.enabled,
        weekend_muted:Boolean(x.automation.weekend_muted),
        working_days:x.working_days || x.automation.working_days || DEFAULT_DAYS,
        alert_interval_minutes:x.alert_interval_minutes,
      })})
      .catch(e=>{if(active)setError(e.message)})
      .finally(()=>{if(active)setSettingsLoading(false)});
    refreshTunnel();
    return ()=>{active=false};
  },[]);
  async function change(key,value){if(!s||saving)return;setError("");const before=s[key];setS(x=>({...x,[key]:value}));setSaving(true);try{const result=await adminApi("/api/v1/admin/settings",{method:"PATCH",headers:{"Content-Type":"application/json"},body:JSON.stringify({[key]:value})});setS(prev=>({...result.questions,automation_enabled:result.automation.configured_enabled ?? result.automation.enabled,weekend_muted:Boolean(result.automation.weekend_muted ?? prev?.weekend_muted),working_days:result.working_days || result.automation.working_days || prev?.working_days || DEFAULT_DAYS,alert_interval_minutes:result.alert_interval_minutes}))}catch(e){setS(x=>({...x,[key]:before}));setError(e.message)}finally{setSaving(false)}}
  function toggleWorkingDay(dayKey){
    if(!s||saving)return;
    const next = { ...(s.working_days || DEFAULT_DAYS), [dayKey]: !(s.working_days?.[dayKey]) };
    change("working_days", next);
  }
  return (
    <div className="panel" style={{ padding: "4px 24px 24px" }}>
      <div className="panel-head" style={{ paddingLeft: 0, paddingRight: 0 }}>
        <div>
          <h2>Alert Rules & Automation</h2>
          <p>{s?.weekend_muted ? "Non-working day active: Alert Rules & Automation are paused, while Report Forwarding (Group_SO_Controll topics) and 24h/24h Dashboard Check-In Tracking (00:00 AM – 00:00 AM) remain active." : "Active working day: Alert Rules & Automation are running. Dashboard Check-In tracking & Report Forwarding run 24h/24h (00:00 AM – 00:00 AM, Mon–Sun) across all pages."}</p>
        </div>
      </div>
      {error&&<p className="api-error" role="alert">{error}</p>}
      {settingsLoading ? <div className="empty" role="status">Loading persisted settings…</div> : !s ? <div className="empty">Settings could not be loaded. Correct the API/database error before changing bot automation.</div> : <>
      <div className="settings-row">
        <div>
          <b>Working Time</b>
          <small>Choose which days Alert Rules &amp; Automation run. Dashboard Check-In tracking and Report Forwarding run 24h/24h (00:00 AM – 00:00 AM, Mon–Sun) regardless of Working Time.</small>
        </div>
        <div className="sa-working-days">
          {DAY_LABELS.map(([key, label]) => {
            const on = Boolean((s.working_days || DEFAULT_DAYS)[key]);
            return (
              <button
                key={key}
                type="button"
                disabled={saving}
                aria-pressed={on}
                className={`sa-day-pill ${on ? "on" : "off"}`}
                onClick={() => toggleWorkingDay(key)}
              >
                {label}
              </button>
            );
          })}
        </div>
      </div>
      <div className="settings-row"><div><b>Alert check interval</b><small>Applies on active Working Time days (Cambodia time)</small></div><select disabled={saving} value={s.alert_interval_minutes} onChange={e=>change("alert_interval_minutes",Number(e.target.value))}>{[5,10,15,20,25,30,35,40,45,50,55,60].map(v=><option key={v} value={v}>{v} minutes</option>)}</select></div>
      {[
        [
          "wrong_plan",
          "Wrong Plan Check-in questions",
          "Ask when actual commune differs from the uploaded plan.",
        ],
        [
          "over_10km",
          "Road Distance Over 10 km questions",
          "Alert dealer-to-first and check-in-to-check-in exceptions.",
        ],
        [
          "missing_report_followup",
          "Missing Report follow-up",
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
      <div className="settings-row"><div><b>Automatic bot alerts</b><small>Report forwarding and dashboard tracking stay active when alerts are off.</small></div><button disabled={saving} className={"toggle "+(s.automation_enabled?"on":"")} onClick={()=>change("automation_enabled",!s.automation_enabled)}><span/></button></div>
      <div className="settings-row"><div><b>Public ngrok dashboard link</b></div><div style={{display:"flex",gap:10,alignItems:"center"}}>{tunnel.active&&<a className="secondary compact" href={tunnel.public_url} target="_blank" rel="noreferrer">Open public dashboard</a>}<button className="secondary compact" onClick={refreshTunnel}>Refresh link</button></div></div>
      </>}
    </div>
  );
}

function ReasonSummary() {
 return <ReasonSummaryPage api={adminApi} RegionDealerFilters={RegionDealerFilters} Metric={Metric}/>;
}

function ALPage() {
  const initialDay = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Phnom_Penh",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date());
  const [fromDate, setFromDate] = useState(initialDay);
  const [toDate, setToDate] = useState(initialDay);
  const [attendanceSearch, setAttendanceSearch] = useState("");
  const [groupFilter, setGroupFilter] = useState("All Telegram groups");
  const [controllerFilter, setControllerFilter] = useState("All controllers");
  const [status, setStatus] = useState("All SO");
  const [rows, setRows] = useState([]);
  const [controllers, setControllers] = useState([]);
  const [scheduledCount, setScheduledCount] = useState(0);
  const [cancellingId, setCancellingId] = useState("");
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
    const q = new URLSearchParams({ day: fromDate || initialDay });
    if (fromDate) q.set("start", fromDate);
    if (toDate && toDate >= fromDate) q.set("end", toDate);
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
          leaveTypeLabel: x.leave_type_label || "—",
          leaveFrom: x.leave_from || "",
          leaveTo: x.leave_to || "",
          statusKind: x.status_kind || (x.leave_status ? "on_leave" : "no_leave"),
          statusLabel: x.status_label || (x.leave_status ? "On leave" : "No leave"),
          hasCheckin: x.has_checkin,
          fullDayConflict: x.full_day_conflict,
          telegramGroup: x.telegram_group,
          controller: x.controller || "—",
          source: x.source,
        })),
      );
      setControllers(Array.isArray(payload?.controllers) ? payload.controllers : []);
      setScheduledCount(Number(payload?.scheduled_count ?? 0));
    } catch (e) {
      setRows([]);
      setControllers([]);
      setScheduledCount(0);
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadRows();
  }, [fromDate, toDate]);

  const groupOptions = useMemo(
    () =>
      [...new Set(rows.map((r) => r.telegramGroup).filter((g) => g && g !== "—"))].sort((a, b) =>
        a.localeCompare(b),
      ),
    [rows],
  );

  const controllerOptions = useMemo(() => {
    const seen = new Set(controllers);
    for (const r of rows) {
      if (r.controller && r.controller !== "—") {
        for (const part of r.controller.split(",")) {
          const clean = part.trim();
          if (clean) seen.add(clean);
        }
      }
    }
    return [...seen].sort((a, b) => a.localeCompare(b));
  }, [controllers, rows]);

  const attendanceFiltered = useMemo(() => {
    const q = attendanceSearch.trim().toLowerCase();
    return rows
      .filter((r) => {
        if (groupFilter !== "All Telegram groups" && r.telegramGroup !== groupFilter) return false;
        if (controllerFilter !== "All controllers" && !String(r.controller || "").includes(controllerFilter)) return false;
        if (status === "On leave" && r.statusKind !== "on_leave") return false;
        if (status === "Scheduled" && r.statusKind !== "scheduled") return false;
        if (status === "No leave" && r.statusKind !== "no_leave") return false;
        if (q) {
          const hay = [r.id, r.telegramId, r.name, r.telegramGroup, r.controller, r.region, r.dealer]
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
  }, [rows, groupFilter, controllerFilter, status, attendanceSearch]);

  const alCount = attendanceFiltered.filter((r) => r.statusKind === "on_leave").length;
  const scheduledFilteredCount = attendanceFiltered.filter((r) => r.statusKind === "scheduled").length;

  const fmtLeaveDates = (fromIso, toIso) => {
    if (!fromIso) return "—";
    const fmt = (iso) => {
      try {
        return new Date(`${iso}T00:00:00Z`).toLocaleDateString("en-US", {
          month: "short", day: "numeric", year: "numeric", timeZone: "UTC",
        });
      } catch { return iso; }
    };
    if (!toIso || toIso === fromIso) return fmt(fromIso);
    return `${fmt(fromIso)} – ${fmt(toIso)}`;
  };

  async function handleCancelLeave(row) {
    if (!row || cancellingId) return;
    if (!window.confirm(`Cancel annual leave for ${row.name}?`)) return;
    setCancellingId(row.id);
    setError("");
    setSuccess("");
    try {
      const q = new URLSearchParams();
      if (row.leaveFrom) q.set("start", row.leaveFrom);
      if (row.leaveTo) q.set("end", row.leaveTo);
      const qs = q.toString() ? `?${q}` : "";
      await adminApi(`/api/v1/admin/al/${encodeURIComponent(row.id)}${qs}`, { method: "DELETE" });
      setSuccess(`Annual leave cancelled for ${row.name}.`);
      await loadRows();
    } catch (e) {
      setError(e.message);
    } finally {
      setCancellingId("");
    }
  }

  function openLeaveModal(row) {
    setModalRow(row);
    setModalError("");
    if (row.leaveFrom && row.leaveTo) {
      setModalType("AL Date Range");
      setModalStart(row.leaveFrom);
      setModalEnd(row.leaveTo);
    } else {
      setModalType("AL Date Range");
      setModalStart(fromDate || initialDay);
      setModalEnd(toDate && toDate >= fromDate ? toDate : (fromDate || initialDay));
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
          <strong>{loading ? "…" : error ? "—" : (scheduledFilteredCount || scheduledCount)}</strong>
          <span>Scheduled leave</span>
        </div>
        <div className="sa-al-kpi">
          <strong>{loading ? "…" : error ? "—" : attendanceFiltered.length}</strong>
          <span>SO in roster</span>
        </div>
      </div>

      <section className="panel sa-card-unified">
        <div className="sa-al-toolbar">
          <div className="sa-al-search-wrap">
            <MagnifyingGlass size={15} className="sa-al-search-icon" />
            <input
              value={attendanceSearch}
              onChange={(e) => setAttendanceSearch(e.target.value)}
              placeholder="Search SO or Telegram ID…"
              aria-label="Search SO or Telegram ID"
            />
          </div>
          <label className="sa-al-date-field">
            <span>From Date</span>
            <input
              type="date"
              value={fromDate}
              onChange={(e) => {
                const v = e.target.value;
                setFromDate(v);
                if (toDate && v && toDate < v) setToDate(v);
              }}
            />
          </label>
          <label className="sa-al-date-field">
            <span>To Date</span>
            <input
              type="date"
              value={toDate}
              min={fromDate || undefined}
              onChange={(e) => setToDate(e.target.value)}
            />
          </label>
          <select
            className="sa-al-select"
            value={groupFilter}
            onChange={(e) => setGroupFilter(e.target.value)}
            aria-label="Filter by Telegram group"
          >
            <option value="All Telegram groups">All Telegram groups</option>
            {groupOptions.map((g) => (
              <option key={g} value={g}>{g}</option>
            ))}
          </select>
          <select
            className="sa-al-select"
            value={controllerFilter}
            onChange={(e) => setControllerFilter(e.target.value)}
            aria-label="Filter by controller"
          >
            <option value="All controllers">All controllers</option>
            {controllerOptions.map((c) => (
              <option key={c} value={c}>{c}</option>
            ))}
          </select>
          <select
            className="sa-al-select sa-al-select-status"
            value={status}
            onChange={(e) => setStatus(e.target.value)}
            aria-label="Filter by leave status"
          >
            <option value="All SO">All SO</option>
            <option value="On leave">On leave</option>
            <option value="Scheduled">Scheduled</option>
            <option value="No leave">No leave</option>
          </select>
          <span className="sa-member-count">{attendanceFiltered.length} members</span>
        </div>

        <div className="table-wrap">
          <table className="data-table sa-al-table">
            <thead>
              <tr>
                <th>SO NAME</th>
                <th>TELEGRAM ID</th>
                <th>GROUP</th>
                <th>CONTROLLER</th>
                <th>LEAVE TYPE</th>
                <th>DATES</th>
                <th>STATUS</th>
                <th>ACTION</th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr>
                  <td className="empty" colSpan={8}>
                    Loading SO roster…
                  </td>
                </tr>
              ) : (
                attendanceFiltered.map((r) => {
                  const hasLeave = r.statusKind === "on_leave" || r.statusKind === "scheduled";
                  const pillCls =
                    r.statusKind === "on_leave"
                      ? "is-on-leave"
                      : r.statusKind === "scheduled"
                        ? "is-scheduled"
                        : "is-no-leave";
                  return (
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
                      <td className="sa-mono">{r.telegramId || r.id || "—"}</td>
                      <td>{r.telegramGroup || `${r.region}-${r.dealer}`}</td>
                      <td>{r.controller || "—"}</td>
                      <td>{r.leaveTypeLabel || "—"}</td>
                      <td>{fmtLeaveDates(r.leaveFrom, r.leaveTo)}</td>
                      <td>
                        <span className={`sa-al-status-pill ${pillCls}`}>
                          <i>•</i> {r.statusLabel}
                        </span>
                      </td>
                      <td>
                        <div className="sa-al-actions">
                          <button
                            type="button"
                            className="sa-al-action-btn"
                            onClick={() => openLeaveModal(r)}
                          >
                            <CalendarBlank size={14} weight="bold" />
                            <span>{hasLeave ? "Edit AL" : "Set AL"}</span>
                          </button>
                          {hasLeave && (
                            <button
                              type="button"
                              className="sa-al-cancel-btn"
                              disabled={cancellingId === r.id}
                              onClick={() => handleCancelLeave(r)}
                            >
                              {cancellingId === r.id ? "Cancelling…" : "Cancel AL"}
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  );
                })
              )}
              {!loading && !error && attendanceFiltered.length === 0 && (
                <tr>
                  <td className="empty" colSpan={8}>
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


