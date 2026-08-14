import { useMemo, useState } from "react";
import { FiCheckSquare, FiRefreshCw, FiSearch } from "react-icons/fi";
import {
  Badge, Button, DataTable, KpiCard, Panel, CONSOLE, cx, focusRing, type,
} from "../../components/admin";
import ConsoleScreen from "../../components/admin/ConsoleScreen";
import api from "../../api";
import useApi from "../../hooks/useApi";
import useInterval from "../../hooks/useInterval";
import ReadinessRecordDrawer from "./ReadinessRecordDrawer";

// Event Readiness — the pre-broadcast gate for upcoming events. Real GET /admin/event-
// readiness (services/ops.py's event_readiness), the same gate computation the Command
// Center's "Event Readiness" badge and upcoming-events widget already use — this page is
// just that computation without the dashboard's top-8/high-impact-only narrowing.
//
// It is a gate, not a score: there is no percentage anywhere on this page. A gate is
// derived from an event's REAL configuration (title set, start time scheduled, host
// assigned, moderator assigned, recording enabled, account in good standing, no unresolved
// single-path override) — nothing here is edited by hand; resolve the underlying
// configuration (assign a host, enable recording, ...) and the verdict changes itself.
const VERDICT_LABEL = { blocked: "Blocked", conditional: "Conditional", passed: "Passed" };
const VERDICT_TONE = { blocked: "danger", conditional: "warning", passed: "success" };
const VERDICT_RANK = { blocked: 0, conditional: 1, passed: 2 };
const IMPACT_LABEL = { standard: "Standard", high: "High", unrepeatable: "Unrepeatable" };
const IMPACT_TONE = { standard: "neutral", high: "warning", unrepeatable: "danger" };

const REFRESH_MS = 30000;

function useReadinessData(includeTest) {
  return useApi(() =>
    api
      .get("/admin/event-readiness", { params: { include_test: includeTest, high_impact_only: false, limit: 200 } })
      .then((r) => ({ items: r.data, fetched_at: Date.now() }))
  );
}

export default function EventReadiness() {
  const [includeTest, setIncludeTest] = useState(false);
  const { data, loading, error, reload } = useReadinessData(includeTest);

  const [q, setQ] = useState("");
  const [verdict, setVerdict] = useState("all");
  const [impact, setImpact] = useState("all");
  const [selected, setSelected] = useState(null);

  useInterval(reload, REFRESH_MS);

  const [ageSeconds, setAgeSeconds] = useState(0);
  useInterval(() => setAgeSeconds(Math.floor((Date.now() - data.fetched_at) / 1000)), 1000, Boolean(data));

  const events = data?.items || [];

  const counts = useMemo(() => ({
    blocked: events.filter((e) => e.verdict === "blocked").length,
    conditional: events.filter((e) => e.verdict === "conditional").length,
    passed: events.filter((e) => e.verdict === "passed").length,
    total: events.length,
  }), [events]);

  const clearFilters = () => {
    setQ("");
    setVerdict("all");
    setImpact("all");
  };

  const rows = useMemo(() => {
    const query = q.trim().toLowerCase();
    return events
      .filter((e) => {
        if (query && !`${e.title} ${e.organization || ""}`.toLowerCase().includes(query)) return false;
        if (verdict !== "all" && e.verdict !== verdict) return false;
        if (impact !== "all" && e.impact !== impact) return false;
        return true;
      })
      .sort(
        (a, b) =>
          VERDICT_RANK[a.verdict] - VERDICT_RANK[b.verdict] ||
          new Date(a.start_time) - new Date(b.start_time)
      );
  }, [events, q, verdict, impact]);

  const columns = [
    {
      key: "start",
      header: "Start",
      sortable: true,
      sortValue: (e) => new Date(e.start_time).getTime(),
      render: (e) => (
        <div className="min-w-0">
          <p className={cx("whitespace-nowrap text-[13px] font-medium", CONSOLE.heading)}>
            {e.start_time ? new Date(e.start_time).toLocaleString() : "—"}
          </p>
          {e.timezone && <p className={cx("whitespace-nowrap text-[11px]", type.mono, CONSOLE.faint)}>{e.timezone}</p>}
        </div>
      ),
    },
    {
      key: "title",
      header: "Event",
      sortable: true,
      render: (e) => <p className={cx("text-[13px] font-semibold", CONSOLE.heading)}>{e.title}</p>,
    },
    {
      key: "organization",
      header: "Organization",
      sortable: true,
      render: (e) => <p className={cx("text-[13px]", CONSOLE.body)}>{e.organization || "—"}</p>,
    },
    {
      key: "impact",
      header: "Impact",
      sortable: true,
      render: (e) => <Badge tone={IMPACT_TONE[e.impact]}>{IMPACT_LABEL[e.impact] || e.impact}</Badge>,
    },
    {
      key: "verdict",
      header: "Gate",
      sortable: true,
      sortValue: (e) => VERDICT_RANK[e.verdict],
      render: (e) => <Badge tone={VERDICT_TONE[e.verdict]} dot>{VERDICT_LABEL[e.verdict]}</Badge>,
    },
    {
      key: "failing",
      header: "Failing gates",
      render: (e) =>
        e.failing.length ? (
          <span className="text-[12px] text-rose-600 dark:text-rose-400">{e.failing.join(", ")}</span>
        ) : (
          <span className={cx("text-[12px]", CONSOLE.faint)}>None</span>
        ),
    },
    {
      key: "action",
      header: "Action",
      align: "right",
      render: (e) => (
        <Button variant="secondary" size="sm" onClick={() => setSelected(e)} aria-label={`Open readiness record for ${e.title}`}>
          Open
        </Button>
      ),
    },
  ];

  return (
    <ConsoleScreen
      title="Event Readiness"
      subtitle="Pre-broadcast gate for upcoming events, derived from each event's real configuration — assigned host/moderator, recording, account standing, and any unresolved single-path override."
      ageSeconds={ageSeconds}
      loading={loading}
      error={error}
      hasData={Boolean(data)}
      endpoint="/admin/event-readiness"
      onRetry={reload}
      actions={
        <Button variant="secondary" leftIcon={FiRefreshCw} onClick={reload}>
          Refresh
        </Button>
      }
    >
      <Panel title="Scope" flush>
        <div className="flex flex-wrap items-center gap-3 px-4 py-3">
          <label className={cx(CONSOLE.checkboxRow, "text-[13px]", CONSOLE.body)}>
            <input
              type="checkbox"
              checked={includeTest}
              onChange={(e) => setIncludeTest(e.target.checked)}
              className={cx(CONSOLE.checkbox, focusRing)}
            />
            Include test-mode organizations
          </label>
          <span className={cx("ml-auto text-[11px] leading-snug", CONSOLE.faint)}>
            Every upcoming event with a scheduled start time. Standard-impact events have no
            mandatory gates, so missing configuration can never Block them — but an unset gate
            (no host, no recording, ...) still reads Conditional even on a Standard event; only
            High and Unrepeatable events can actually Block on it.
          </span>
        </div>
      </Panel>

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <KpiCard
          label="Blocked"
          value={counts.blocked}
          tone={counts.blocked ? "text-rose-600 dark:text-rose-400" : undefined}
          pressed={verdict === "blocked"}
          onClick={() => setVerdict(verdict === "blocked" ? "all" : "blocked")}
        />
        <KpiCard
          label="Conditional"
          value={counts.conditional}
          tone={counts.conditional ? "text-amber-600 dark:text-amber-400" : undefined}
          pressed={verdict === "conditional"}
          onClick={() => setVerdict(verdict === "conditional" ? "all" : "conditional")}
        />
        <KpiCard
          label="Passed"
          value={counts.passed}
          pressed={verdict === "passed"}
          onClick={() => setVerdict(verdict === "passed" ? "all" : "passed")}
        />
        <KpiCard label="Total upcoming" value={counts.total} note={`inside GET /admin/event-readiness's window`} />
      </div>

      <Panel title="Readiness pipeline" description="Blocked before Conditional before Passed, then soonest first." flush>
        <div className="flex flex-wrap items-center gap-3 px-4 py-3">
          <div className="relative min-w-[220px] flex-1">
            <FiSearch className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" aria-hidden="true" />
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Search event or organization…"
              aria-label="Search the readiness pipeline"
              className={CONSOLE.search}
            />
          </div>
          <select value={verdict} onChange={(e) => setVerdict(e.target.value)} className={CONSOLE.select} aria-label="Filter by gate state">
            <option value="all">All gate states</option>
            {Object.entries(VERDICT_LABEL).map(([v, l]) => (
              <option key={v} value={v}>{l}</option>
            ))}
          </select>
          <select value={impact} onChange={(e) => setImpact(e.target.value)} className={CONSOLE.select} aria-label="Filter by impact class">
            <option value="all">All impact classes</option>
            {Object.entries(IMPACT_LABEL).map(([v, l]) => (
              <option key={v} value={v}>{l}</option>
            ))}
          </select>
        </div>
        <div className={cx("border-t", CONSOLE.divider)} />
        <DataTable
          columns={columns}
          rows={rows}
          rowKey={(e) => e.id}
          onRowClick={setSelected}
          pageSize={10}
          minWidth={1100}
          empty={{
            icon: FiCheckSquare,
            title: "No events match these filters",
            description: "No upcoming event satisfies every active filter. Clear the filters or include test-mode organizations.",
            action: (
              <Button variant="secondary" size="sm" onClick={clearFilters}>
                Clear filters
              </Button>
            ),
          }}
        />
      </Panel>

      <ReadinessRecordDrawer event={selected} open={Boolean(selected)} onClose={() => setSelected(null)} />
    </ConsoleScreen>
  );
}
