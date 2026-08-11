import { useMemo, useState } from "react";
import { FiCheckSquare, FiDownload, FiRefreshCw, FiSearch } from "react-icons/fi";
import {
  Badge, Button, DataTable, KpiCard, Panel, CONSOLE, cx, focusRing, type,
} from "../../components/admin";
import ConsoleScreen from "../../components/admin/ConsoleScreen";
import useInterval from "../../hooks/useInterval";
import ReadinessRecordDrawer from "./ReadinessRecordDrawer";
import {
  EVENTS, EVIDENCE_LABEL, EVIDENCE_TONE, GATES, GATE_LABEL, GATE_TONE, OPERATORS,
  ORGANIZATIONS, REGIONS, REQUIREMENT_CATEGORIES, RISK_LABEL, RISK_TONE, SERVICE_TIERS,
} from "./readinessData";

// Event Readiness (ZST-WF-SA-ER-001 · screen S04) — the pre-broadcast gate for managed Live
// Events. It is a gate, not a score: there is no percentage anywhere on this page, because a
// percentage implies negotiability.
//
// WHAT THIS BUILD IS: the canonical interface over a STATIC demonstration payload. §34 of the
// spec lists the Readiness Policy Registry, the back-end event state machine, Identity &
// Access and the Audit service as build blockers, so there is nothing authoritative to call
// yet and nothing here mutates anything. The page states that on its face — a console read as
// ground truth during a launch must never let demonstration figures pass for measured state.
//
// Two structural rules from the spec shape the code below:
//   · STD-04 — gate, risk, evidence and lifecycle are separate axes. No column collapses them.
//   · §3.2 — the gate is DERIVED. Nothing in this UI edits a gate state, and the record drawer
//     says so where an operator would otherwise go looking for the control.

// Field skins come from the console tokens so a hover or focus change lands on every filter
// row at once, instead of being re-typed per page.
const inputCls = CONSOLE.search;
const selectCls = CONSOLE.select;

const HORIZONS = [
  [1, "Next 24 hours"],
  [3, "Next 3 days"],
  [7, "Next 7 days"],
  [14, "Next 14 days"],
];

const TIME_BANDS = [
  ["all", "Any time to start"],
  ["24h", "Starts within 24h"],
  ["3d", "Starts within 3 days"],
  ["7d", "Starts within 7 days"],
];

const EVIDENCE_FILTERS = ["missing", "failed", "expired", "expiring", "valid", "conflicting", "indeterminate"];

// §8.2 default sort. Unrepeatable events are pinned ahead of everything else regardless of
// ordinary sort preference, then the gate ladder, then time to start.
const GATE_RANK = { BLOCKED: 0, CONDITIONAL: 1, IN_PROGRESS: 2, PASSED: 3, NOT_STARTED: 4 };
const TIER_RANK = { Enterprise: 0, Business: 1, Standard: 2 };

// One evidence axis rendered as one pill. `configured` and `not_applicable` are real, distinct
// answers — neither is coerced into a pass.
function AxisPill({ state }) {
  if (state === "configured") return <Badge tone="info">Configured</Badge>;
  return <Badge tone={EVIDENCE_TONE[state] || "neutral"}>{EVIDENCE_LABEL[state] || state}</Badge>;
}

export default function EventReadiness() {
  // Header controls (§6)
  const [horizon, setHorizon] = useState(7);
  const [region, setRegion] = useState("all");
  const [risk, setRisk] = useState("all");
  const [org, setOrg] = useState("all");
  const [includeCompleted, setIncludeCompleted] = useState(false);

  // Pipeline filters (§8.3)
  const [q, setQ] = useState("");
  const [gate, setGate] = useState("all");
  const [operator, setOperator] = useState("all");
  const [evidence, setEvidence] = useState("all");
  const [category, setCategory] = useState("all");
  const [tier, setTier] = useState("all");
  const [band, setBand] = useState("all");

  const [selected, setSelected] = useState(null);

  // Freshness receipt. The payload is static, so this measures time since the operator last
  // asked for it — which is the honest thing for a snapshot to report.
  const [ageSeconds, setAgeSeconds] = useState(0);
  useInterval(() => setAgeSeconds((s) => s + 1), 1000);

  const clearFilters = () => {
    setQ("");
    setGate("all");
    setOperator("all");
    setEvidence("all");
    setCategory("all");
    setTier("all");
    setBand("all");
    setRisk("all");
    setOrg("all");
    setRegion("all");
  };

  // The horizon-scoped, live-mode set. Everything else — KPI strip and pipeline — is derived
  // from this, so the strip can never report a count the table cannot show.
  //
  // An unrepeatable event scheduled inside 30 days stays in the window whatever the horizon
  // says (§6): narrowing the horizon must never be a way to make one disappear.
  const inHorizon = useMemo(() => {
    const inWindow = (e) =>
      e.completed ? includeCompleted : e.risk === "unrepeatable" ? e.days_out <= 30 : e.days_out <= horizon;
    return EVENTS.filter(
      (e) => inWindow(e) && (region === "all" || e.region === region) && (org === "all" || e.org === org)
    );
  }, [horizon, includeCompleted, region, org]);

  const counts = useMemo(() => {
    const live = inHorizon.filter((e) => !e.completed);
    const by = (g) => live.filter((e) => e.gate === g).length;
    const unrepeatable = live.filter((e) => e.risk === "unrepeatable");
    return {
      blocked: by("BLOCKED"),
      conditional: by("CONDITIONAL"),
      conditionalLapsed: live.filter((e) => e.gate === "CONDITIONAL" && e.evidence_expiring_soon).length,
      passed: by("PASSED"),
      within24h: live.filter((e) => e.within_24h).length,
      within24hAtRisk: live.filter((e) => e.within_24h && ["BLOCKED", "CONDITIONAL"].includes(e.gate)).length,
      unrepeatable: unrepeatable.length,
      unrepeatableBlocked: unrepeatable.filter((e) => e.gate === "BLOCKED").length,
      expiring: live.filter((e) => e.evidence_expiring_soon).length,
    };
  }, [inHorizon]);

  const rows = useMemo(() => {
    const query = q.trim().toLowerCase();
    const maxDays = { all: Infinity, "24h": 0, "3d": 3, "7d": 7 }[band];
    return inHorizon
      .filter((e) => {
        if (query && !`${e.name} ${e.event_id} ${e.org} ${e.operator}`.toLowerCase().includes(query)) return false;
        if (gate !== "all" && e.gate !== gate) return false;
        if (risk !== "all" && e.risk !== risk) return false;
        if (operator !== "all" && e.operator !== operator) return false;
        if (tier !== "all" && e.tier !== tier) return false;
        if (!e.completed && e.days_out > maxDays) return false;
        if (evidence !== "all" && !e.requirements.some((r) => r.state === evidence)) return false;
        // Requirement category narrows to events where that category is actually a problem —
        // filtering to "has a Contribute rule" would match every row and mean nothing.
        if (
          category !== "all" &&
          !e.requirements.some((r) => r.stage === category && !["valid", "not_applicable"].includes(r.state))
        )
          return false;
        return true;
      })
      .sort(
        (a, b) =>
          Number(b.risk === "unrepeatable") - Number(a.risk === "unrepeatable") ||
          GATE_RANK[a.gate] - GATE_RANK[b.gate] ||
          a.days_out - b.days_out ||
          TIER_RANK[a.tier] - TIER_RANK[b.tier] ||
          b.blockers - a.blockers
      );
  }, [inHorizon, q, gate, risk, operator, tier, band, evidence, category]);

  const columns = [
    {
      key: "start",
      header: "Start",
      sortable: true,
      sortValue: (e) => e.days_out,
      render: (e) => (
        <div className="min-w-0">
          <p className={cx("whitespace-nowrap text-[13px] font-medium", CONSOLE.heading)}>{e.start_local}</p>
          <p className={cx("whitespace-nowrap text-[11px]", type.mono, CONSOLE.faint)}>
            {e.start_utc} · {e.starts_in}
          </p>
        </div>
      ),
    },
    {
      key: "name",
      header: "Event",
      sortable: true,
      render: (e) => (
        <div className="min-w-0">
          <p className={cx("text-[13px] font-semibold", CONSOLE.heading)}>{e.name}</p>
          <p className={cx("text-[11px]", type.mono, CONSOLE.faint)}>{e.event_id}</p>
        </div>
      ),
    },
    {
      key: "org",
      header: "Organization",
      sortable: true,
      render: (e) => (
        <div className="min-w-0">
          <p className={cx("text-[13px]", CONSOLE.body)}>{e.org}</p>
          <p className={cx("text-[11px]", type.mono, CONSOLE.faint)} title={`tenant ${e.tenant_id}`}>
            {e.tier}
          </p>
        </div>
      ),
    },
    {
      key: "risk",
      header: "Risk",
      sortable: true,
      render: (e) => (
        <Badge tone={RISK_TONE[e.risk]} dot={e.risk === "unrepeatable"}>
          {RISK_LABEL[e.risk]}
        </Badge>
      ),
    },
    {
      key: "gate",
      header: "Gate",
      sortable: true,
      sortValue: (e) => GATE_RANK[e.gate],
      render: (e) => (
        <Badge tone={GATE_TONE[e.gate]} dot>
          {GATE_LABEL[e.gate]}
        </Badge>
      ),
    },
    { key: "primary_path", header: "Primary path", render: (e) => <AxisPill state={e.primary_path} /> },
    { key: "backup_path", header: "Backup path", render: (e) => <AxisPill state={e.backup_path} /> },
    { key: "access", header: "Access", render: (e) => <AxisPill state={e.access} /> },
    { key: "captions", header: "Captions", render: (e) => <AxisPill state={e.captions} /> },
    { key: "preservation", header: "Preservation", render: (e) => <AxisPill state={e.preservation} /> },
    {
      key: "operator",
      header: "Operator",
      sortable: true,
      render: (e) => (
        <div className="min-w-0">
          <p className={cx("whitespace-nowrap text-[13px]", CONSOLE.body)}>{e.operator}</p>
          <p className={cx("whitespace-nowrap text-[11px]", CONSOLE.faint)}>backup {e.backup_operator}</p>
        </div>
      ),
    },
    {
      key: "blockers",
      header: "Blockers",
      align: "right",
      sortable: true,
      render: (e) =>
        e.blockers > 0 ? (
          <span className="font-semibold tabular-nums text-rose-600 dark:text-rose-400">{e.blockers}</span>
        ) : (
          <span className={cx("tabular-nums", CONSOLE.faint)}>0</span>
        ),
    },
    {
      key: "evidence_expiry",
      header: "Evidence expiry",
      render: (e) => (
        <span
          className={cx(
            "whitespace-nowrap text-[12px]",
            type.mono,
            e.evidence_expiring_soon ? "font-semibold text-amber-600 dark:text-amber-400" : CONSOLE.muted
          )}
        >
          {e.evidence_expiry || "—"}
        </span>
      ),
    },
    {
      key: "action",
      header: "Action",
      align: "right",
      render: (e) => (
        <Button
          variant="secondary"
          size="sm"
          onClick={() => setSelected(e)}
          aria-label={`Open readiness record for ${e.name}`}
        >
          Open
        </Button>
      ),
    },
  ];

  return (
    <ConsoleScreen
      title="Event Readiness"
      demoData
      subtitle="Evidence-backed operational gate for managed ZoikoStream Live Events. A gate state is derived by the readiness policy engine — it is never a score, and never edited by hand."
      ageSeconds={ageSeconds}
      hasData
      actions={
        <>
          <Button variant="secondary" leftIcon={FiRefreshCw} onClick={() => setAgeSeconds(0)}>
            Refresh
          </Button>
          <Button
            variant="secondary"
            leftIcon={FiDownload}
            title="Display only in this build — export is permission-controlled and audited"
          >
            Export snapshot
          </Button>
        </>
      }
    >
      {/* §6 Page header controls. Readiness is live mode only — there is deliberately no
          "include test mode" toggle, because a test session is not an event that can go live. */}
      <Panel title="Readiness horizon and scope" flush>
        <div className="flex flex-wrap items-center gap-3 px-4 py-3">
          <select
            value={horizon}
            onChange={(e) => setHorizon(Number(e.target.value))}
            className={selectCls}
            aria-label="Readiness horizon"
          >
            {HORIZONS.map(([v, l]) => (
              <option key={v} value={v}>
                {l}
              </option>
            ))}
          </select>
          <select
            value={region}
            onChange={(e) => setRegion(e.target.value)}
            className={selectCls}
            aria-label="Filter by production region"
          >
            <option value="all">All regions</option>
            {REGIONS.map((r) => (
              <option key={r.id} value={r.id}>
                {r.label}
              </option>
            ))}
          </select>
          <select
            value={risk}
            onChange={(e) => setRisk(e.target.value)}
            className={selectCls}
            aria-label="Filter by risk classification"
          >
            <option value="all">All risk classes</option>
            {Object.entries(RISK_LABEL).map(([v, l]) => (
              <option key={v} value={v}>
                {l}
              </option>
            ))}
          </select>
          <select
            value={org}
            onChange={(e) => setOrg(e.target.value)}
            className={selectCls}
            aria-label="Filter by Organization"
          >
            <option value="all">All Organizations</option>
            {ORGANIZATIONS.map((o) => (
              <option key={o} value={o}>
                {o}
              </option>
            ))}
          </select>
          <label className={cx(CONSOLE.checkboxRow, "text-[13px]", CONSOLE.body)}>
            <input
              type="checkbox"
              checked={includeCompleted}
              onChange={(e) => setIncludeCompleted(e.target.checked)}
              className={cx(CONSOLE.checkbox, focusRing)}
            />
            Include completed
          </label>
          <span className={cx("ml-auto text-[11px] leading-snug", CONSOLE.faint)}>
            Live mode only. Test-mode events never enter readiness or attention counts.
          </span>
        </div>
      </Panel>

      {/* §7 KPI strip — operational workload, not executive analytics. Each card filters the
          pipeline below rather than navigating away, so the two can never disagree. */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-3 xl:grid-cols-6">
        <KpiCard
          label="Blocked"
          value={counts.blocked}
          note={
            counts.within24hAtRisk > 0
              ? `${counts.within24hAtRisk} inside the 24h escalation window`
              : "Live mode only"
          }
          tone={counts.blocked ? "text-rose-600 dark:text-rose-400" : undefined}
          pressed={gate === "BLOCKED"}
          onClick={() => setGate(gate === "BLOCKED" ? "all" : "BLOCKED")}
        />
        <KpiCard
          label="Conditional"
          value={counts.conditional}
          note={`${counts.conditionalLapsed} with a deadline inside the safety window`}
          tone={counts.conditional ? "text-amber-600 dark:text-amber-400" : undefined}
          pressed={gate === "CONDITIONAL"}
          onClick={() => setGate(gate === "CONDITIONAL" ? "all" : "CONDITIONAL")}
        />
        <KpiCard
          label="Passed"
          value={counts.passed}
          note="Expired evidence can reduce this immediately — a pass does not freeze readiness"
          pressed={gate === "PASSED"}
          onClick={() => setGate(gate === "PASSED" ? "all" : "PASSED")}
        />
        <KpiCard
          label="Starting <24h"
          value={counts.within24h}
          note={`${counts.within24hAtRisk} blocked or conditional`}
          pressed={band === "24h"}
          onClick={() => setBand(band === "24h" ? "all" : "24h")}
        />
        <KpiCard
          label="Unrepeatable"
          value={counts.unrepeatable}
          note={`${counts.unrepeatableBlocked} blocked · always pinned to the top of the pipeline`}
          tone={counts.unrepeatableBlocked ? "text-rose-600 dark:text-rose-400" : undefined}
          pressed={risk === "unrepeatable"}
          onClick={() => setRisk(risk === "unrepeatable" ? "all" : "unrepeatable")}
        />
        <KpiCard
          label="Evidence expiring"
          value={counts.expiring}
          note="Threshold from the controlled readiness policy registry"
          tone={counts.expiring ? "text-amber-600 dark:text-amber-400" : undefined}
          pressed={evidence === "expiring"}
          onClick={() => setEvidence(evidence === "expiring" ? "all" : "expiring")}
        />
      </div>

      {/* §8 S04-V01 Readiness Pipeline */}
      <Panel
        title="Readiness pipeline"
        description="Unrepeatable events first, then Blocked before Conditional before In progress before Passed before Not started."
        count={counts.blocked}
        flush
      >
        <div className="flex flex-wrap items-center gap-3 px-4 py-3">
          <div className="relative min-w-[220px] flex-1">
            <FiSearch
              className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400"
              aria-hidden="true"
            />
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Search event, ID, Organization or operator…"
              aria-label="Search the readiness pipeline"
              className={inputCls}
            />
          </div>
          <select value={gate} onChange={(e) => setGate(e.target.value)} className={selectCls} aria-label="Filter by gate state">
            <option value="all">All gate states</option>
            {GATES.map((g) => (
              <option key={g} value={g}>
                {GATE_LABEL[g]}
              </option>
            ))}
          </select>
          <select value={band} onChange={(e) => setBand(e.target.value)} className={selectCls} aria-label="Filter by time to start">
            {TIME_BANDS.map(([v, l]) => (
              <option key={v} value={v}>
                {l}
              </option>
            ))}
          </select>
          <select
            value={operator}
            onChange={(e) => setOperator(e.target.value)}
            className={selectCls}
            aria-label="Filter by primary operator"
          >
            <option value="all">All operators</option>
            {OPERATORS.map((o) => (
              <option key={o} value={o}>
                {o}
              </option>
            ))}
          </select>
          <select
            value={evidence}
            onChange={(e) => setEvidence(e.target.value)}
            className={selectCls}
            aria-label="Filter by evidence status"
          >
            <option value="all">Any evidence status</option>
            {EVIDENCE_FILTERS.map((s) => (
              <option key={s} value={s}>
                {EVIDENCE_LABEL[s]}
              </option>
            ))}
          </select>
          <select
            value={category}
            onChange={(e) => setCategory(e.target.value)}
            className={selectCls}
            aria-label="Filter by requirement category"
          >
            <option value="all">Any requirement category</option>
            {REQUIREMENT_CATEGORIES.map((c) => (
              <option key={c} value={c}>
                {c} unsatisfied
              </option>
            ))}
          </select>
          <select value={tier} onChange={(e) => setTier(e.target.value)} className={selectCls} aria-label="Filter by service tier">
            <option value="all">All service tiers</option>
            {SERVICE_TIERS.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </div>
        <div className={cx("border-t", CONSOLE.divider)} />
        <DataTable
          columns={columns}
          rows={rows}
          rowKey={(e) => e.id}
          onRowClick={setSelected}
          pageSize={6}
          minWidth={1760}
          empty={{
            icon: FiCheckSquare,
            title: "No events match these filters",
            description:
              "No scheduled event inside the readiness horizon satisfies every active filter. Widen the horizon or clear the filters.",
            action: (
              <Button variant="secondary" size="sm" onClick={clearFilters}>
                Clear filters
              </Button>
            ),
          }}
        />
      </Panel>

      <div className="grid gap-4 xl:grid-cols-2">
        <Panel title="How a gate state is reached">
          <ul className={cx("space-y-2 text-[13px]", CONSOLE.body)}>
            <li>
              <strong className={CONSOLE.heading}>Blocked</strong> — at least one mandatory
              requirement is unsatisfied, invalid, expired, conflicting or unverifiable. The
              commercial live transition is rejected server-side, not merely hidden.
            </li>
            <li>
              <strong className={CONSOLE.heading}>Conditional</strong> — every mandatory
              requirement passes; one or more non-mandatory items remain open with a named owner
              and a pre-start deadline. A lapsed deadline moves the event to Blocked.
            </li>
            <li>
              <strong className={CONSOLE.heading}>Passed</strong> — every applicable mandatory
              requirement is satisfied by valid evidence. This can regress: expiring evidence or a
              material configuration change moves a passed event back.
            </li>
          </ul>
          <p className={cx("mt-4 border-t pt-3 text-[12px] leading-snug", CONSOLE.divider, CONSOLE.faint)}>
            Operators resolve evidence, correct configuration or submit a governed exception. No
            control in this console edits a gate state.
          </p>
        </Panel>

        <Panel title="Unrepeatable-event hard gate">
          <p className={cx("text-[13px] leading-[20px]", CONSOLE.body)}>
            The unrepeatable classification is a product-operating constraint, not a badge. For
            these events a verified independent backup contribution path is mandatory before
            commercial live operation, and the backup must not share a failure domain the
            readiness policy prohibits.
          </p>
          <ul className={cx("mt-3 space-y-1.5 text-[13px]", CONSOLE.muted)}>
            <li>Missing, invalid, expired or unverifiable backup evidence forces Blocked.</li>
            <li>The authorization layer rejects the prohibited transition even if this UI is bypassed.</li>
            <li>Every rejected transition attempt is audited with actor, gate state, blockers and policy version.</li>
            <li>The only route past it is a dual-authorized exception, which excludes its own requester.</li>
          </ul>
          <p className={cx("mt-4 border-t pt-3 text-[12px] leading-snug", CONSOLE.divider, CONSOLE.faint)}>
            A scheduled time does not make an event ready. Valid evidence, applicable policy,
            authorized decisions and verified operating conditions do.
          </p>
        </Panel>
      </div>

      <ReadinessRecordDrawer event={selected} open={Boolean(selected)} onClose={() => setSelected(null)} />
    </ConsoleScreen>
  );
}
