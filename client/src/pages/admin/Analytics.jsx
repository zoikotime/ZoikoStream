import { useEffect, useRef, useState } from "react";
import { FiInfo } from "react-icons/fi";
import { Badge, Button, CONSOLE, Panel, StatCard, cx, money } from "../../components/admin";
import OrgFilter from "../../components/admin/OrgFilter";
import { AreaChart, LineChart } from "../../ui/charts";
import { SERIES } from "../../ui/tokens";
import api from "../../api";
import useApi from "../../hooks/useApi";

// Platform Analytics — GET /admin/analytics (services/admin.analytics).
//
// Every filter goes to the server and is applied in SQL: the window (24h/7d/30d/90d/custom)
// and the organization. The endpoint used to take no parameters, so every chart was
// all-time and all-tenant and the page could not be narrowed at all.
//
// Each figure is labelled with how it is known, straight from the response:
//   Measured — counted from platform rows (events, sessions, users, recordings)
//   Derived  — computed from measured rows under a stated rule (contracted MRR, list price)
// Delivery telemetry the platform does not collect is LISTED from `unmeasured`, never charted:
// a chart of zeros for it would read as "measured, nothing happened".

const RANGES = [["24h", "24 hours"], ["7d", "7 days"], ["30d", "30 days"], ["90d", "90 days"], ["custom", "Custom"]];
const MEASURE_TONE = { measured: "success", derived: "info" };
const MEASURE_LABEL = { measured: "Measured", derived: "Derived" };

function Measure({ kind }) {
  if (!kind) return null;
  return <Badge tone={MEASURE_TONE[kind] || "neutral"} size="sm">{MEASURE_LABEL[kind] || kind}</Badge>;
}

// A custom window is whole days, inclusive, in UTC — the same zone the server buckets in.
const dayStart = (d) => (d ? `${d}T00:00:00Z` : undefined);
const dayEnd = (d) => (d ? `${d}T23:59:59Z` : undefined);
const fmtWindow = (iso) => (iso ? new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "—");

export default function Analytics() {
  const [range, setRange] = useState("30d");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [org, setOrg] = useState(null);
  // A custom range is applied on request, not per keystroke in a date field, and not at all
  // until it has a start date (the server refuses one without).
  const [applied, setApplied] = useState({ from: "", to: "" });

  const customReady = range !== "custom" || Boolean(applied.from);
  const { data, loading, error, reload } = useApi(() => {
    if (!customReady) return Promise.resolve(null);
    return api.get("/admin/analytics", {
      params: {
        range,
        from: range === "custom" ? dayStart(applied.from) : undefined,
        to: range === "custom" ? dayEnd(applied.to) : undefined,
        org_id: org?.id,
      },
    }).then((r) => r.data);
  });

  const mounted = useRef(false);
  useEffect(() => {
    if (!mounted.current) { mounted.current = true; return; }
    reload();
  }, [range, applied, org, reload]);

  const bucket = data?.window?.bucket === "month" ? "month" : "day";
  const revenue = data?.revenue || [];
  const organizations = data?.organizations || [];
  const users = data?.users || [];
  const events = data?.events || [];
  const totals = data?.totals || {};
  const measurement = data?.measurement || {};
  const show = !loading && data;

  const kpi = (key, label, hint) => (
    <StatCard
      key={key}
      label={<span className="inline-flex items-center gap-1.5">{label} <Measure kind={totals[key]?.measurement} /></span>}
      value={show ? totals[key]?.value ?? "—" : 0}
      loading={loading}
      hint={hint}
    />
  );

  return (
    <div className="mx-auto max-w-[1440px] space-y-6">
      <div>
        <h1 className="text-[24px] font-semibold tracking-tight text-slate-900 dark:text-white">Analytics</h1>
        {/* "Contracted", not "revenue". ZoikoStream owns plans and entitlements; invoicing and
            payment collection sit outside it, so the most this page can truthfully report is
            what is contracted at list price — not what was collected. */}
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
          Contracted value and platform activity over a chosen window. Contracted figures are
          list-price and exclude trials; collected revenue is not measured here.
        </p>
      </div>

      <Panel>
        <div className="flex flex-wrap items-center gap-3">
          <div className="inline-flex rounded-lg border border-slate-200 p-0.5 dark:border-white/10" role="group" aria-label="Time range">
            {RANGES.map(([key, label]) => (
              <button
                key={key}
                type="button"
                aria-pressed={range === key}
                onClick={() => setRange(key)}
                className={cx("rounded-md px-3 py-1 text-[13px] font-medium",
                  range === key ? CONSOLE.segmentOn : CONSOLE.segmentOff)}
              >
                {label}
              </button>
            ))}
          </div>
          {range === "custom" && (
            <div className="flex flex-wrap items-center gap-2 text-[13px]">
              <label className="flex items-center gap-1.5">From
                <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} className={CONSOLE.select} aria-label="From date" />
              </label>
              <label className="flex items-center gap-1.5">To
                <input type="date" value={to} min={from || undefined} onChange={(e) => setTo(e.target.value)} className={CONSOLE.select} aria-label="To date" />
              </label>
              <Button size="sm" variant="secondary" disabled={!from} onClick={() => setApplied({ from, to })}>Apply</Button>
            </div>
          )}
          <OrgFilter value={org} onChange={setOrg} />
        </div>
        {data?.window && (
          <p className="mt-2 text-xs text-slate-500 dark:text-slate-400" data-testid="analytics-window">
            {fmtWindow(data.window.since)} – {fmtWindow(data.window.until)} · per {bucket}
            {org ? ` · ${org.name}` : " · all organizations"}
          </p>
        )}
        {range === "custom" && !applied.from && (
          <p className="mt-2 text-xs text-slate-500 dark:text-slate-400">Choose a start date and apply to load a custom window.</p>
        )}
      </Panel>

      {error ? (
        <div className="flex items-center justify-between gap-3 rounded-xl border border-rose-200 bg-rose-50 px-5 py-4 text-sm text-rose-700 dark:border-rose-500/20 dark:bg-rose-500/10 dark:text-rose-300" role="alert">
          <span>Couldn&apos;t load analytics for this window.</span>
          <Button variant="secondary" size="sm" onClick={reload}>Retry</Button>
        </div>
      ) : customReady && (
        <>
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
            <StatCard
              label={<span className="inline-flex items-center gap-1.5">Contracted MRR <Measure kind={measurement.mrr} /></span>}
              value={show ? money(data.mrr) : 0}
              loading={loading}
              hint="Now, at list price — not windowed"
            />
            <StatCard
              label={<span className="inline-flex items-center gap-1.5">Streaming hours <Measure kind={measurement.streaming_hours} /></span>}
              value={show ? data.streaming_hours : 0}
              decimals={1}
              loading={loading}
              hint="Broadcast time inside the window"
            />
            {kpi("events_created", "Events created")}
            {kpi("broadcasts_completed", "Broadcasts completed")}
            {kpi("recordings", "Recordings started")}
            {kpi("new_users", "New users")}
          </div>

          {data?.trials?.count > 0 && (
            <p className="text-xs text-slate-500 dark:text-slate-400" data-testid="trial-note">
              {data.trials.count} subscription{data.trials.count === 1 ? " is" : "s are"} in trial
              {" "}({money(data.trials.list_value)}/mo at list price if converted) — not included in
              Contracted MRR.
            </p>
          )}

          <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
            <AreaChart
              title="New contracted MRR"
              subtitle={`List price of subscriptions started each ${bucket} that are still on a paying status — excludes trials`}
              data={revenue}
              keys={[{ key: "value", name: "Contracted MRR", color: SERIES.brand }]}
              loading={loading}
              empty={!loading && revenue.length === 0}
            />
            <AreaChart
              title="Events created"
              subtitle={`Events created each ${bucket}`}
              data={events}
              keys={[{ key: "value", name: "Events", color: SERIES.warning }]}
              loading={loading}
              empty={!loading && events.length === 0}
            />
          </div>

          <div className={cx("grid grid-cols-1 gap-6", !org && "xl:grid-cols-2")}>
            <LineChart
              title="New users"
              subtitle={`Users created each ${bucket}`}
              data={users}
              keys={[{ key: "value", name: "Users", color: SERIES.success }]}
              loading={loading}
              empty={!loading && users.length === 0}
            />
            {/* Omitted for a single organization: organization growth inside one organization is
                one flat point, and the server does not send it. */}
            {!org && (
              <AreaChart
                title="New organizations"
                subtitle={`Organizations created each ${bucket}`}
                data={organizations}
                keys={[{ key: "value", name: "Organizations", color: SERIES.info }]}
                loading={loading}
                empty={!loading && organizations.length === 0}
              />
            )}
          </div>

          {show && data.unmeasured?.length > 0 && (
            <div className="flex items-start gap-2.5 rounded-xl border border-slate-200 bg-slate-50 px-5 py-3.5 text-sm text-slate-600 dark:border-slate-800 dark:bg-slate-800/40 dark:text-slate-300" data-testid="unmeasured">
              <FiInfo className="mt-0.5 shrink-0 text-slate-400" />
              <div>
                <p className="font-medium">Not measured on this platform</p>
                <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">
                  {data.unmeasured.join(" · ")}. {data.note}
                </p>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
