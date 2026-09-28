import { useEffect, useRef, useState } from "react";
import { FiBarChart2, FiChevronLeft, FiChevronRight, FiSearch } from "react-icons/fi";
import { Badge, Button, CONSOLE, Panel, cx } from "../../components/admin";
import { bytes } from "../../components/admin/format";
import api from "../../api";
import useApi from "../../hooks/useApi";

// Usage & Entitlements — per-organization entitlements, quota evaluation and measured usage,
// from GET /admin/usage (services/admin.usage_overview).
//
// Nothing here is calculated in the browser. Each quota bar is the SAME object the
// organization's own console renders (services/org.entitlements), so the Super Admin view and
// the tenant view cannot disagree about a tenant's usage. Filters, sort and paging are applied
// in SQL, so they reach every organization rather than the page that happened to load.

const PAGE_SIZE = 25;

// Canonical subscription states (models/subscription.SUBSCRIPTION_STATES). The server matches
// legacy spellings too (stored_spellings), so "Trialing" also finds rows stored as "trial".
const STATUS_OPTIONS = [
  ["active", "Active"], ["trialing", "Trialing"], ["past_due", "Past due"],
  ["suspended", "Suspended"], ["canceled", "Canceled"], ["trial_expired", "Trial expired"],
  ["pending_activation", "Pending activation"],
];
const SORT_OPTIONS = [["name", "Name A–Z"], ["-name", "Name Z–A"], ["storage", "Most storage used"]];

const STATE_TONE = { within: "success", exceeded: "danger", unlimited: "info", not_applicable: "neutral" };
const STATE_LABEL = { within: "Within quota", exceeded: "Over quota", unlimited: "Unlimited", not_applicable: "Not applicable" };

const fmtDate = (iso) => (iso ? new Date(iso).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" }) : null);

// One quota, exactly as the server evaluated it. The three "no number" states stay distinct:
//   not_applicable — no entitled plan, so nothing is granted and nothing is measured against
//   unlimited      — a plan with no cap on this dimension
//   within/exceeded — a real limit, with remaining and percent
// `enforced` says whether the platform actually REFUSES anything at this limit; a bar that is
// only shown against the plan is labelled so it is not read as a control.
export function QuotaBar({ quota: q }) {
  const pct = q.percent == null ? null : Math.min(100, Math.max(0, q.percent));
  const reading =
    q.quota_state === "not_applicable" ? `${q.used} ${q.unit}`
      : q.unlimited ? `${q.used} ${q.unit} · no plan cap`
        : `${q.used} / ${q.limit} ${q.unit}`;
  const notes = [];
  if (q.quota_state === "within") notes.push(`${q.remaining} ${q.unit} remaining · ${q.percent}%`);
  if (q.quota_state === "exceeded") notes.push(`Over by ${Math.abs(q.remaining)} ${q.unit} · ${q.percent}%`);
  if (q.quota_state === "not_applicable") notes.push("No entitled plan");
  if (!q.enforced) notes.push("Not enforced — shown against the plan only");
  // The ceiling that actually applies can differ from the plan's: a lapsed subscription stays
  // capped at its old plan, and storage is further bounded by the platform ceiling.
  if (q.enforced && q.enforced_limit != null && q.enforced_limit !== q.limit) {
    notes.push(`Enforced ceiling ${q.enforced_limit} ${q.unit}`);
  }
  return (
    <div data-testid={`quota-${q.key}`}>
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-xs font-medium text-slate-700 dark:text-slate-200">{q.label}</span>
        <Badge tone={STATE_TONE[q.quota_state] || "neutral"} size="sm">{STATE_LABEL[q.quota_state] || q.quota_state}</Badge>
      </div>
      <p className="mt-1 font-mono text-[12px] text-slate-600 dark:text-slate-300">{reading}</p>
      {pct != null && (
        <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800" aria-hidden="true">
          <div
            className={cx("h-full rounded-full",
              q.quota_state === "exceeded" ? "bg-rose-500" : pct >= 80 ? "bg-amber-500" : "bg-emerald-500")}
            style={{ width: `${pct}%` }}
          />
        </div>
      )}
      {notes.length > 0 && <p className="mt-1 text-[11px] text-slate-500 dark:text-slate-400">{notes.join(" · ")}</p>}
    </div>
  );
}

// A metric as the server reported it. `unavailable` is shown as a stated gap, never as 0.
function Metric({ m }) {
  let value;
  if (m.state === "unavailable") value = <span className="text-slate-400">Not measured</span>;
  else if (m.unit === "bytes") value = bytes(m.value);
  else value = m.value;
  return (
    <div data-testid={`metric-${m.key}`}>
      <dt className="text-[11px] text-slate-500 dark:text-slate-400">{m.label}</dt>
      <dd className="font-mono text-[13px] text-slate-800 dark:text-slate-100">
        {value}
        {m.unknown_count > 0 && (
          <span className="ml-1 font-sans text-[11px] text-amber-700 dark:text-amber-300">
            + {m.unknown_count} with unknown size
          </span>
        )}
      </dd>
    </div>
  );
}

function OrgUsage({ row }) {
  const renews = fmtDate(row.current_period_end);
  const trialEnds = fmtDate(row.trial_ends_at);
  return (
    <li className="px-5 py-4" data-testid="usage-row">
      <div className="flex flex-wrap items-center gap-2">
        <p className="font-medium text-slate-800 dark:text-slate-100">{row.organization}</p>
        {row.is_test && <Badge tone="neutral" size="sm">Test</Badge>}
        {row.plan
          ? <Badge tone="brand" size="sm">{row.plan}</Badge>
          : <Badge tone="neutral" size="sm">No entitled plan</Badge>}
        {row.subscription_status && <Badge tone="info" size="sm">{row.subscription_status.replace(/_/g, " ")}</Badge>}
        <span className="text-[11px] text-slate-400">
          {[renews && `Period ends ${renews}`, trialEnds && `Trial ends ${trialEnds}`].filter(Boolean).join(" · ")}
        </span>
      </div>
      <div className="mt-3 grid gap-4 sm:grid-cols-3">
        {row.quotas.map((q) => <QuotaBar key={q.key || q.label} quota={q} />)}
      </div>
      <dl className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-5">
        {row.metrics.map((m) => <Metric key={m.key} m={m} />)}
      </dl>
    </li>
  );
}

export default function UsageEntitlements({ plans = [] }) {
  const [qInput, setQInput] = useState("");
  const [q, setQ] = useState("");
  const [plan, setPlan] = useState("all");
  const [status, setStatus] = useState("all");
  const [sort, setSort] = useState("name");
  const [page, setPage] = useState(1);

  const { data, loading, error, reload } = useApi(() =>
    api.get("/admin/usage", {
      params: {
        q: q || undefined,
        plan: plan === "all" ? undefined : plan,
        status: status === "all" ? undefined : status,
        sort, page, page_size: PAGE_SIZE,
      },
    }).then((r) => r.data)
  );

  useEffect(() => {
    const t = setTimeout(() => setQ(qInput.trim()), 300);
    return () => clearTimeout(t);
  }, [qInput]);

  const mounted = useRef(false);
  useEffect(() => {
    if (!mounted.current) { mounted.current = true; return; }
    reload();
  }, [q, plan, status, sort, page, reload]);

  // A new filter is a new result set, so it starts from its first page.
  const setFilter = (setter) => (e) => { setter(e.target.value); setPage(1); };

  const items = data?.items || [];
  const total = data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const filtered = q || plan !== "all" || status !== "all";

  return (
    <Panel flush>
      <div className="flex flex-wrap items-center gap-3 px-4 py-3">
        <div className="relative min-w-[200px] flex-1">
          <FiSearch className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" aria-hidden="true" />
          <input
            value={qInput}
            onChange={(e) => { setQInput(e.target.value); setPage(1); }}
            placeholder="Search organizations…"
            aria-label="Search organizations"
            className={CONSOLE.search}
          />
        </div>
        <select value={plan} onChange={setFilter(setPlan)} className={CONSOLE.select} aria-label="Filter by plan">
          <option value="all">All plans</option>
          {(Array.isArray(plans) ? plans : []).map((p) => <option key={p.id} value={p.slug}>{p.name}</option>)}
        </select>
        <select value={status} onChange={setFilter(setStatus)} className={CONSOLE.select} aria-label="Filter by subscription status">
          <option value="all">All subscription states</option>
          {STATUS_OPTIONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
        <select value={sort} onChange={setFilter(setSort)} className={CONSOLE.select} aria-label="Sort organizations">
          {SORT_OPTIONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
      </div>
      <div className={cx("border-t", CONSOLE.divider)} />

      {error ? (
        <div className="flex items-center justify-between gap-3 px-5 py-6 text-sm text-rose-700 dark:text-rose-300" role="alert">
          <span>Couldn&apos;t load usage and entitlements.</span>
          <Button variant="secondary" size="sm" onClick={reload}>Retry</Button>
        </div>
      ) : loading && !data ? (
        <ul className="divide-y divide-slate-100 dark:divide-slate-800">
          {Array.from({ length: 3 }).map((_, i) => (
            <li key={i} className="px-5 py-5"><div className="zk-skeleton h-4 w-56 rounded bg-slate-200 dark:bg-slate-800" /></li>
          ))}
        </ul>
      ) : items.length === 0 ? (
        <div className="px-5 py-14 text-center">
          <FiBarChart2 className="mx-auto mb-2 text-xl text-slate-400" aria-hidden="true" />
          <p className="text-sm font-semibold text-slate-700 dark:text-slate-200">
            {filtered ? "No organizations match these filters" : "No customer organizations yet"}
          </p>
        </div>
      ) : (
        <ul className={cx("divide-y divide-slate-100 dark:divide-slate-800", loading && "opacity-60")}>
          {items.map((row) => <OrgUsage key={row.org_id} row={row} />)}
        </ul>
      )}

      {total > 0 && (
        <div className={cx("flex items-center justify-between gap-3 border-t px-4 py-3 text-xs text-slate-500 dark:text-slate-400", CONSOLE.divider)}>
          <span data-testid="usage-total">
            {total} organization{total === 1 ? "" : "s"} · page {page} of {pages}
          </span>
          <div className="flex gap-2">
            <Button variant="secondary" size="sm" leftIcon={FiChevronLeft} disabled={page <= 1 || loading} onClick={() => setPage((p) => p - 1)}>Previous</Button>
            <Button variant="secondary" size="sm" leftIcon={FiChevronRight} disabled={page >= pages || loading} onClick={() => setPage((p) => p + 1)}>Next</Button>
          </div>
        </div>
      )}
      <p className="px-4 pb-3 text-[11px] text-slate-400">
        Usage is counted from platform records. Delivery volume per period is not metered on this platform and is shown as not measured.
      </p>
    </Panel>
  );
}
