import { sessionFetch } from "./dashboardSession.js";
import React, { useEffect, useMemo, useState } from "react";

function quoteCsv(value) {
  let text = String(value ?? "");
  if (/^[=+\-@]/.test(text)) text = "'" + text;
  return '"' + text.replaceAll('"', '""') + '"';
}

function downloadCsv(filename, headers, rows) {
  const csv = [headers, ...rows].map((r) => r.map(quoteCsv).join(",")).join("\r\n");
  const url = URL.createObjectURL(new Blob(["\ufeff" + csv], { type: "text/csv;charset=utf-8" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function initialsOf(name) {
  return (
    String(name || "?")
      .trim()
      .split(/\s+/)
      .map((x) => x[0] || "")
      .join("")
      .slice(0, 2)
      .toUpperCase() || "?"
  );
}

function Avatar({ name, telegramId }) {
  return (
    <div className="sa-avatar">
      <span>{initialsOf(name)}</span>
      {telegramId && (
        <img
          src={`/api/v1/dashboard/telegram-avatar/${encodeURIComponent(telegramId)}`}
          alt=""
          loading="lazy"
          onError={(e) => {
            e.currentTarget.style.display = "none";
          }}
        />
      )}
    </div>
  );
}

const PencilSvg = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z" />
    <path d="m15 5 4 4" />
  </svg>
);

const TrashSvg = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M3 6h18" />
    <path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6" />
    <path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2" />
  </svg>
);

export default function ReasonSummaryPage({ api, RegionDealerFilters, Metric }) {
  const today = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Phnom_Penh",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date());

  const [start, setStart] = useState(today);
  const [end, setEnd] = useState(today);
  const [region, setRegion] = useState("All Regions");
  const [dealer, setDealer] = useState("All Dealers");
  const [controller, setController] = useState("All controllers");
  const [language, setLanguage] = useState("km");
  const [search, setSearch] = useState("");
  const [view, setView] = useState("miss");

  const [missData, setMissData] = useState(null);
  const [report, setReport] = useState(null);
  const [users, setUsers] = useState(null);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [exporting, setExporting] = useState(false);

  // Modals
  const [detailModal, setDetailModal] = useState(null); // "written" | "not_written" | null
  const [modalSearch, setModalSearch] = useState("");
  const [editRow, setEditRow] = useState(null);
  const [editText, setEditText] = useState("");
  const [savingEdit, setSavingEdit] = useState(false);
  const [editError, setEditError] = useState("");

  const params = () => {
    const p = new URLSearchParams({ start, end });
    if (region !== "All Regions") p.set("region", region);
    if (dealer !== "All Dealers") p.set("dealer", dealer);
    return p;
  };

  useEffect(() => {
    let alive = true;
    if (!start || !end || end < start) {
      setError("Choose From date and To date in order.");
      setMissData(null);
      setReport(null);
      setUsers(null);
      setBusy(false);
      return;
    }
    setBusy(true);
    const pUsers = params();
    pUsers.set("language", language);
    const pMiss = params();
    pMiss.set("language", language);
    if (controller !== "All controllers") pMiss.set("controller", controller);

    Promise.all([
      api(`/api/v1/admin/miss-report?${pMiss}`).catch(() => null),
      api(`/api/v1/admin/reasons?${params()}`),
      api(`/api/v1/admin/reasons/users?${pUsers}`),
    ])
      .then(([miss, data, grouped]) => {
        if (!alive) return;
        setMissData(miss && Array.isArray(miss.items) && miss.summary ? miss : null);
        setReport(data);
        setUsers(grouped);
        setError("");
      })
      .catch((e) => {
        if (!alive) return;
        setError(e.message);
        setMissData(null);
        setReport(null);
        setUsers(null);
      })
      .finally(() => {
        if (alive) setBusy(false);
      });

    return () => {
      alive = false;
    };
  }, [start, end, region, dealer, controller, refresh, language]);

  const replyItems = Array.isArray(report?.items) ? report.items : [];
  const legacyEmployees = useMemo(
    () =>
      (users?.items || []).map((x) => {
        const hasReason = Boolean(x.summary) && x.has_reason !== false;
        return {
          employee_id: x.employee_id || "—",
          telegram_id: x.telegram_id || null,
          name: x.name || "—",
          region: x.region || "—",
          dealer: x.dealer || "—",
          group: x.region && x.dealer ? `${x.region}-${x.dealer}` : "—",
          controller: "—",
          total_visit: x.total_outlets ?? 0,
          total_miss: x.reply_count ?? (hasReason ? 1 : 0),
          miss_report_status: hasReason ? "Written" : "Awaiting reason",
          has_written_reason: hasReason,
          written_reasons: hasReason ? String(x.summary || "").split("\n").filter(Boolean) : [],
          written_count: hasReason ? x.reply_count ?? 1 : 0,
          not_written_count: hasReason ? 0 : 1,
          summary_reason: x.summary || "—",
        };
      }),
    [users],
  );

  const allMissItems = useMemo(
    () => (Array.isArray(missData?.items) ? missData.items : legacyEmployees),
    [missData, legacyEmployees],
  );

  const filteredMissItems = useMemo(() => {
    const q = search.trim().toLowerCase();
    return allMissItems.filter((x) => {
      if (
        controller !== "All controllers" &&
        !String(x.controller || "").toLowerCase().includes(controller.toLowerCase())
      ) {
        return false;
      }
      if (!q) return true;
      const hay = [
        x.name,
        x.employee_id,
        x.telegram_id,
        x.region,
        x.dealer,
        x.group,
        x.controller,
        x.summary_reason,
      ]
        .join(" ")
        .toLowerCase();
      return hay.includes(q);
    });
  }, [allMissItems, controller, search]);

  const summary = useMemo(() => {
    if (missData?.summary && !search.trim()) {
      return missData.summary;
    }
    return {
      total_so: filteredMissItems.length,
      so_miss_report: filteredMissItems.filter((x) => Number(x.total_miss || 0) > 0).length,
      total_visit: filteredMissItems.reduce((n, x) => n + Number(x.total_visit || 0), 0),
      total_miss: filteredMissItems.reduce((n, x) => n + Number(x.total_miss || 0), 0),
      written_reasons: filteredMissItems.reduce((n, x) => n + Number(x.written_count || 0), 0),
      not_written_reasons: filteredMissItems.reduce((n, x) => n + Number(x.not_written_count || 0), 0),
      written_so_count: filteredMissItems.filter((x) => x.has_written_reason).length,
      not_written_so_count: filteredMissItems.filter((x) => Number(x.not_written_count || 0) > 0).length,
    };
  }, [missData, filteredMissItems, search]);

  const controllerOptions = useMemo(() => {
    if (Array.isArray(missData?.controllers) && missData.controllers.length) {
      return missData.controllers;
    }
    return [...new Set(allMissItems.map((x) => x.controller).filter((c) => c && c !== "—"))].sort();
  }, [missData, allMissItems]);

  const writtenBaseRows = useMemo(
    () =>
      filteredMissItems
        .filter((x) => x.has_written_reason)
        .sort((a, b) => Number(b.total_miss || 0) - Number(a.total_miss || 0) || a.name.localeCompare(b.name)),
    [filteredMissItems],
  );

  const notWrittenBaseRows = useMemo(
    () =>
      filteredMissItems
        .filter((x) => Number(x.not_written_count || 0) > 0)
        .sort((a, b) => Number(b.total_miss || 0) - Number(a.total_miss || 0) || a.name.localeCompare(b.name)),
    [filteredMissItems],
  );

  const modalRows = useMemo(() => {
    const src = detailModal === "written" ? writtenBaseRows : detailModal === "not_written" ? notWrittenBaseRows : [];
    const q = modalSearch.trim().toLowerCase();
    if (!q) return src;
    return src.filter((x) =>
      [x.name, x.employee_id, x.telegram_id, x.region, x.dealer, x.group, x.controller, x.summary_reason]
        .join(" ")
        .toLowerCase()
        .includes(q),
    );
  }, [detailModal, writtenBaseRows, notWrittenBaseRows, modalSearch]);

  function exportMissCsv() {
    if (view === "replies") {
      downloadCsv(
        `Reason_Summary_${start}_${end}.csv`,
        ["Date", "Employee ID", "Name", "Region", "Dealer", "Classification", "Category", "Khmer Summary", "English Summary", "Analysis Method", "Status", "Original Reply"],
        replyItems.map((x) => [
          x.recorded_at,
          x.employee_id,
          x.name,
          x.region,
          x.dealer,
          x.is_reason === true ? "Reason" : x.is_reason === false ? "Not a reason" : "Needs review",
          x.category,
          x.summary_km,
          x.summary_en || x.summary,
          x.method,
          x.ai_status,
          x.message,
        ]),
      );
      return;
    }
    downloadCsv(
      `Miss_Report_${start}_${end}.csv`,
      ["SO Name", "Employee ID", "Telegram ID", "Region", "Dealer", "Group", "Controller", "Total Visit", "Miss Report", "Total Miss", "Summary Reason"],
      filteredMissItems.map((x) => [
        x.name,
        x.employee_id,
        x.telegram_id || "",
        x.region,
        x.dealer,
        x.group,
        x.controller,
        x.total_visit,
        x.miss_report_status,
        x.total_miss,
        x.summary_reason,
      ]),
    );
  }

  function exportModalCsv() {
    if (detailModal === "written") {
      downloadCsv(
        `Written_Reasons_${start}_${end}.csv`,
        ["SO Name", "Employee ID", "Telegram ID", "Region", "Dealer", "Group", "Controller", "Total Visit", "Total Miss", "Written Reasons"],
        modalRows.map((x) => [
          x.name,
          x.employee_id,
          x.telegram_id || "",
          x.region,
          x.dealer,
          x.group,
          x.controller,
          x.total_visit,
          x.total_miss,
          (x.written_reasons || []).join(" | ") || x.summary_reason,
        ]),
      );
    } else if (detailModal === "not_written") {
      downloadCsv(
        `Not_Written_Reasons_${start}_${end}.csv`,
        ["SO Name", "Telegram ID", "Employee ID", "Region", "Dealer", "Group", "Controller", "Total Visit", "Total Miss"],
        modalRows.map((x) => [
          x.name,
          x.telegram_id || "",
          x.employee_id,
          x.region,
          x.dealer,
          x.group,
          x.controller,
          x.total_visit,
          x.total_miss,
        ]),
      );
    }
  }

  async function exportUsersExcel() {
    setExporting(true);
    setError("");
    try {
      const p = params();
      p.set("language", language);
      if (search.trim()) p.set("q", search.trim());
      const response = await sessionFetch(`/api/v1/admin/reasons/users/export?${p}`);
      if (!response.ok) {
        const data = await response.json();
        throw Error(data.detail || "Export failed");
      }
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = url;
      link.download = `Reason_Summary_By_Employee_${start}_${end}.xlsx`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) {
      setError(e.message);
    } finally {
      setExporting(false);
    }
  }

  function openEditModal(row) {
    setEditRow(row);
    setEditText(
      row.has_written_reason
        ? (row.written_reasons || []).join("\n") || (row.summary_reason !== "—" ? row.summary_reason : "")
        : "",
    );
    setEditError("");
  }

  async function saveEditModal(event) {
    event?.preventDefault();
    if (!editRow || savingEdit) return;
    setSavingEdit(true);
    setEditError("");
    try {
      await api(`/api/v1/admin/miss-report/${encodeURIComponent(editRow.employee_id || editRow.telegram_id)}/reason`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ start, end, reason: editText }),
      });
      setEditRow(null);
      setRefresh((x) => x + 1);
    } catch (e) {
      setEditError(e.message);
    } finally {
      setSavingEdit(false);
    }
  }

  async function handleDeleteReason(row) {
    const id = row.employee_id || row.telegram_id;
    if (!id) return;
    if (typeof window !== "undefined" && typeof window.confirm === "function") {
      if (!window.confirm(`Clear written reason for ${row.name} (${start} to ${end})?`)) return;
    }
    setError("");
    try {
      await api(
        `/api/v1/admin/miss-report/${encodeURIComponent(id)}/reason?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`,
        { method: "DELETE" },
      );
      setRefresh((x) => x + 1);
    } catch (e) {
      setError(e.message);
    }
  }

  return (
    <div className="reason-page">
      <div className="section-title">
        <div>
          <h2>Miss Report &amp; Reason Summary</h2>
        </div>
        <div className="reason-export-actions">
          <div className="reason-view-tabs" role="tablist" aria-label="Reason report view">
            <button
              type="button"
              role="tab"
              aria-selected={view === "miss"}
              className={view === "miss" ? "selected" : ""}
              onClick={() => setView("miss")}
            >
              Miss Report
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={view === "replies"}
              className={view === "replies" ? "selected" : ""}
              onClick={() => setView("replies")}
            >
              Reply details
            </button>
          </div>
          <button
            type="button"
            className="secondary"
            disabled={busy || exporting || !start || !end || end < start}
            onClick={exportUsersExcel}
          >
            {exporting ? "Exporting…" : "Export Excel"}
          </button>
          <button
            type="button"
            className="secondary"
            disabled={busy || !start || !end || end < start}
            onClick={exportMissCsv}
          >
            Export CSV
          </button>
        </div>
      </div>

      {error && (
        <p className="api-error" role="alert">
          {error}
        </p>
      )}

      {view === "miss" ? (
        <section className="panel sa-card-unified">
          <div className="sa-filter-bar sa-miss-filters">
            <label className="field">
              <span>From Date</span>
              <input
                type="date"
                value={start}
                max={end || undefined}
                onChange={(e) => setStart(e.target.value)}
              />
            </label>
            <label className="field">
              <span>To Date</span>
              <input
                type="date"
                value={end}
                min={start || undefined}
                onChange={(e) => setEnd(e.target.value)}
              />
            </label>
            <RegionDealerFilters {...{ region, setRegion, dealer, setDealer }} />
            <label className="field">
              <span>Controller</span>
              <select value={controller} onChange={(e) => setController(e.target.value)}>
                <option value="All controllers">All controllers</option>
                {controllerOptions.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>Language</span>
              <select value={language} onChange={(e) => setLanguage(e.target.value)}>
                <option value="km">Khmer</option>
                <option value="en">English</option>
                <option value="both">Khmer + English</option>
              </select>
            </label>
            <label className="field sa-filter-search">
              <span>Search SO</span>
              <input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Name, Telegram ID, Employee ID, dealer…"
              />
            </label>
          </div>

          <div className="sa-miss-summary-bar">
            <div className="sa-miss-stat">
              <small>Total SO</small>
              <strong>{busy && !allMissItems.length ? "…" : summary.total_so}</strong>
            </div>
            <div className="sa-miss-stat">
              <small>SO Miss Report</small>
              <strong>{busy && !allMissItems.length ? "…" : summary.so_miss_report}</strong>
            </div>
            <div className="sa-miss-stat">
              <small>Total Visit · pins</small>
              <strong>{busy && !allMissItems.length ? "…" : summary.total_visit}</strong>
            </div>
            <div className="sa-miss-stat">
              <small>Total Miss · alerts</small>
              <strong>{busy && !allMissItems.length ? "…" : summary.total_miss}</strong>
            </div>
            <button
              type="button"
              className="sa-miss-stat sa-miss-stat-btn"
              onClick={() => {
                setModalSearch("");
                setDetailModal("written");
              }}
            >
              <small>Written reasons</small>
              <strong className="sa-miss-emerald">{busy && !allMissItems.length ? "…" : summary.written_reasons}</strong>
              <span className="sa-miss-click-hint emerald">Click to view detail ↗</span>
            </button>
            <button
              type="button"
              className="sa-miss-stat sa-miss-stat-btn"
              onClick={() => {
                setModalSearch("");
                setDetailModal("not_written");
              }}
            >
              <small>Not Written reasons</small>
              <strong className="sa-miss-terracotta">
                {busy && !allMissItems.length ? "…" : summary.not_written_reasons}
              </strong>
              <span className="sa-miss-click-hint terracotta">Click to view detail ↗</span>
            </button>
          </div>

          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>SO NAME</th>
                  <th>TELEGRAM ID</th>
                  <th>REGION / DEALER</th>
                  <th>CONTROLLER</th>
                  <th>TOTAL VISIT</th>
                  <th>MISS REPORT</th>
                  <th>TOTAL MISS</th>
                  <th>SUMMARY REASON</th>
                  <th>ACTIONS</th>
                </tr>
              </thead>
              <tbody>
                {filteredMissItems.map((x, i) => (
                  <tr key={`${x.employee_id || x.telegram_id}-${i}`}>
                    <td>
                      <div className="sa-name-cell">
                        <Avatar name={x.name} telegramId={x.telegram_id} />
                        <div className="sa-name-copy">
                          <b>{x.name}</b>
                          <small>{x.group || `${x.region}-${x.dealer}`}</small>
                        </div>
                      </div>
                    </td>
                    <td className="sa-mono">
                      <div>{x.telegram_id || "—"}</div>
                      {x.employee_id && x.employee_id !== String(x.telegram_id || "") && (
                        <small className="sa-muted-sub">ID {x.employee_id}</small>
                      )}
                    </td>
                    <td>
                      {x.region} / {x.dealer}
                    </td>
                    <td>{x.controller || "—"}</td>
                    <td>{x.total_visit ?? 0}</td>
                    <td>
                      <span
                        className={
                          x.has_written_reason
                            ? "status"
                            : Number(x.total_miss || 0) > 0
                              ? "sa-awaiting-text"
                              : "sa-muted-text"
                        }
                      >
                        {x.miss_report_status || (x.has_written_reason ? "Written" : "Awaiting reason")}
                      </span>
                    </td>
                    <td>
                      <span className={Number(x.total_miss || 0) > 0 ? "sa-miss-num" : ""}>
                        {x.total_miss ?? 0}
                      </span>
                    </td>
                    <td
                      className="reason-user-summary sa-summary-cell"
                      lang={language === "en" ? "en" : "km"}
                    >
                      {x.summary_reason || "—"}
                    </td>
                    <td>
                      <div className="sa-row-actions">
                        <button
                          type="button"
                          className="sa-icon-action"
                          title="Edit written reason"
                          aria-label={`Edit reason for ${x.name}`}
                          onClick={() => openEditModal(x)}
                        >
                          <PencilSvg />
                        </button>
                        <button
                          type="button"
                          className="sa-icon-action sa-icon-danger"
                          title="Clear written reason"
                          aria-label={`Delete reason for ${x.name}`}
                          onClick={() => handleDeleteReason(x)}
                        >
                          <TrashSvg />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
                {!filteredMissItems.length && !busy && (
                  <tr>
                    <td colSpan={9} className="empty">
                      No employees match these filters.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </section>
      ) : (
        <>
          <div className="panel controls">
            <label className="field">
              <span>From date</span>
              <input type="date" value={start} max={end || undefined} onChange={(e) => setStart(e.target.value)} />
            </label>
            <label className="field">
              <span>To date</span>
              <input type="date" value={end} min={start || undefined} onChange={(e) => setEnd(e.target.value)} />
            </label>
            <RegionDealerFilters {...{ region, setRegion, dealer, setDealer }} />
            <label className="field">
              <span>Summary language</span>
              <select value={language} onChange={(e) => setLanguage(e.target.value)}>
                <option value="km">Khmer</option>
                <option value="en">English</option>
                <option value="both">Khmer + English</option>
              </select>
            </label>
            <label className="field">
              <span>Search</span>
              <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Name or Employee ID…" />
            </label>
            <button type="button" className="secondary" onClick={() => setRefresh((x) => x + 1)} disabled={busy}>
              Refresh
            </button>
          </div>
          <div className="metrics">
            <Metric name="Replies received" value={report?.received ?? 0} />
            <Metric name="Meaningful reasons" value={report?.with_reason ?? 0} />
            <Metric name="Not reasons" value={report?.not_reason ?? 0} />
            <Metric name="Needs review" value={report?.needs_review ?? 0} />
          </div>
          <section className="panel table-wrap">
            <div className="panel-head">
              <div>
                <h2>Consolidated reply reasons</h2>
              </div>
              {busy && <span className="status">Updating…</span>}
            </div>
            <table className="data-table">
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Employee</th>
                  <th>Region / Dealer</th>
                  <th>Classification</th>
                  <th>
                    Summary · {language === "km" ? "Khmer" : language === "en" ? "English" : "Khmer / English"}
                  </th>
                  <th>Original reply</th>
                </tr>
              </thead>
              <tbody>
                {replyItems.map((x, i) => (
                  <tr key={`${x.chat}-${x.reply_to}-${x.recorded_at}-${i}`}>
                    <td>{String(x.recorded_at || "").replace("T", " ").slice(0, 16)}</td>
                    <td>
                      <b>{x.name || "—"}</b>
                      <div>{x.employee_id || ""}</div>
                    </td>
                    <td>
                      {x.region || "—"} / {x.dealer || "—"}
                    </td>
                    <td>
                      <span className={`status ${x.is_reason === false ? "danger" : ""}`}>
                        {x.is_reason === true ? "Reason" : x.is_reason === false ? "Not a reason" : "Needs review"}
                      </span>
                    </td>
                    <td className="reason-summary-cell">
                      {language !== "en" && (
                        <p lang="km" className={!x.summary_km ? "reason-untranslated" : ""}>
                          {x.summary_km || "Khmer summary not available yet."}
                        </p>
                      )}
                      {language !== "km" && <p lang="en">{x.summary_en || x.summary || "—"}</p>}
                      <small>
                        {x.method === "rules" ? "Rule summary" : "Reviewed summary"}
                        {x.analyzed_at ? ` · ${String(x.analyzed_at).slice(11, 16)}` : ""}
                      </small>
                    </td>
                    <td
                      className="reason-original"
                      lang={/[\u1780-\u17ff]/.test(x.message || "") ? "km" : "en"}
                    >
                      {x.message || "—"}
                    </td>
                  </tr>
                ))}
                {!replyItems.length && !busy && (
                  <tr>
                    <td colSpan={6} className="empty">
                      No employee replies found for these filters.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </section>
        </>
      )}

      {detailModal && (
        <div
          className="sa-modal-backdrop"
          role="dialog"
          aria-modal="true"
          aria-labelledby="sa-miss-detail-title"
          onClick={(e) => {
            if (e.target === e.currentTarget) setDetailModal(null);
          }}
        >
          <div className="sa-modal sa-modal--wide">
            <div className="sa-modal-head">
              <div>
                <div className="sa-modal-eyebrow">
                  {start} to {end} · {modalRows.length} SO
                </div>
                <h3 id="sa-miss-detail-title">
                  {detailModal === "written" ? "Written reasons detail" : "Not Written reasons detail"}
                </h3>
              </div>
              <div className="sa-modal-head-tools">
                <input
                  type="search"
                  className="sa-modal-search"
                  value={modalSearch}
                  onChange={(e) => setModalSearch(e.target.value)}
                  placeholder="Search SO, ID, region, dealer…"
                />
                <button type="button" className="secondary" onClick={exportModalCsv}>
                  Export CSV
                </button>
                <button
                  type="button"
                  className="sa-modal-close"
                  aria-label="Close"
                  onClick={() => setDetailModal(null)}
                >
                  ×
                </button>
              </div>
            </div>
            <div className="sa-modal-table-wrap">
              {detailModal === "written" ? (
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>SO NAME</th>
                      <th>REGION / DEALER</th>
                      <th>GROUP</th>
                      <th>CONTROLLER</th>
                      <th>TOTAL VISIT</th>
                      <th>TOTAL MISS</th>
                      <th>WRITTEN REASONS</th>
                      <th>ACTION</th>
                    </tr>
                  </thead>
                  <tbody>
                    {modalRows.map((x, idx) => (
                      <tr key={`w-${x.employee_id || x.telegram_id}-${idx}`}>
                        <td>
                          <div className="sa-name-cell">
                            <Avatar name={x.name} telegramId={x.telegram_id} />
                            <div className="sa-name-copy">
                              <b>{x.name}</b>
                              <small>{x.employee_id}</small>
                            </div>
                          </div>
                        </td>
                        <td>
                          {x.region} / {x.dealer}
                        </td>
                        <td>{x.group}</td>
                        <td>{x.controller}</td>
                        <td>{x.total_visit}</td>
                        <td>
                          <span className="sa-miss-num">{x.total_miss}</span>
                        </td>
                        <td
                          className="sa-written-list"
                          lang={language === "en" ? "en" : "km"}
                        >
                          {(x.written_reasons || []).length
                            ? x.written_reasons.map((line, k) => <div key={k}>{line}</div>)
                            : x.summary_reason}
                        </td>
                        <td>
                          <button
                            type="button"
                            className="secondary sa-edit-inline-btn"
                            onClick={() => openEditModal(x)}
                          >
                            <PencilSvg />
                            <span>Edit</span>
                          </button>
                        </td>
                      </tr>
                    ))}
                    {!modalRows.length && (
                      <tr>
                        <td colSpan={8} className="empty">
                          No written reasons match these filters.
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              ) : (
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>SO NAME</th>
                      <th>TELEGRAM ID</th>
                      <th>EMPLOYEE ID</th>
                      <th>REGION / DEALER</th>
                      <th>GROUP</th>
                      <th>CONTROLLER</th>
                      <th>TOTAL VISIT</th>
                      <th>TOTAL MISS</th>
                    </tr>
                  </thead>
                  <tbody>
                    {modalRows.map((x, idx) => (
                      <tr key={`nw-${x.employee_id || x.telegram_id}-${idx}`}>
                        <td>
                          <div className="sa-name-cell">
                            <Avatar name={x.name} telegramId={x.telegram_id} />
                            <b>{x.name}</b>
                          </div>
                        </td>
                        <td className="sa-mono">{x.telegram_id || "—"}</td>
                        <td className="sa-mono">{x.employee_id || "—"}</td>
                        <td>
                          {x.region} / {x.dealer}
                        </td>
                        <td>{x.group}</td>
                        <td>{x.controller}</td>
                        <td>{x.total_visit}</td>
                        <td>
                          <span className="sa-miss-num">{x.total_miss}</span>
                        </td>
                      </tr>
                    ))}
                    {!modalRows.length && (
                      <tr>
                        <td colSpan={8} className="empty">
                          No unwritten missing reports for these filters.
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              )}
            </div>
          </div>
        </div>
      )}

      {editRow && (
        <div
          className="sa-modal-backdrop"
          role="dialog"
          aria-modal="true"
          aria-labelledby="sa-edit-reason-title"
          onClick={(e) => {
            if (e.target === e.currentTarget && !savingEdit) setEditRow(null);
          }}
        >
          <form className="sa-modal" onSubmit={saveEditModal}>
            <div className="sa-modal-head">
              <div>
                <div className="sa-modal-eyebrow">
                  {editRow.name} ({editRow.group || `${editRow.region}-${editRow.dealer}`})
                </div>
                <h3 id="sa-edit-reason-title">Edit reason</h3>
              </div>
              <button
                type="button"
                className="sa-modal-close"
                aria-label="Close"
                disabled={savingEdit}
                onClick={() => setEditRow(null)}
              >
                ×
              </button>
            </div>
            <div className="sa-modal-body">
              {editError && (
                <p className="api-error" role="alert">
                  {editError}
                </p>
              )}
              <label className="field">
                <span>
                  Summary reason ({start} to {end})
                </span>
                <textarea
                  className="sa-reason-textarea"
                  rows={4}
                  value={editText}
                  onChange={(e) => setEditText(e.target.value)}
                  placeholder="Enter reason(s) in Khmer or English (one per line)…"
                />
              </label>
            </div>
            <div className="sa-modal-footer">
              <button
                type="button"
                className="sa-btn-ghost"
                disabled={savingEdit}
                onClick={() => setEditRow(null)}
              >
                Cancel
              </button>
              <button type="submit" className="sa-btn-emerald" disabled={savingEdit}>
                {savingEdit ? "Saving…" : "✓ Save changes"}
              </button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}
