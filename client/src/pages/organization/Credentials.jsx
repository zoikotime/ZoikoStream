import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
// Feather has no "infinity" glyph, so a non-expiring key uses FiSlash — read as "no
// lifetime set", which is exactly what the metric counts.
import {
  FiKey, FiCopy, FiLifeBuoy, FiShield, FiRefreshCw, FiCheckCircle,
  FiClock, FiSlash, FiFilter,
} from "react-icons/fi";
import api from "../../api";
import useApi from "../../hooks/useApi";
import { CONSOLE, cx, focusRing, type } from "../../ui/tokens";
import { ConsoleButton } from "../../ui/Button";
import Badge from "../../ui/Badge";
import Dropdown from "../../ui/Dropdown";
import { notify } from "../../ui/Toast";
import DataTable from "../../components/admin/DataTable";
import MetricCard from "../../components/admin/MetricCard";
import Panel from "../../components/admin/Panel";
import StatRow from "../../components/admin/StatRow";
import { timeAgo } from "../../components/admin/format";
import OrganizationPageHeader from "../../components/organization/OrganizationPageHeader";
import OrganizationErrorState from "../../components/organization/OrganizationErrorState";

// Credentials — the API keys this organization authenticates with.
//
// READ-ONLY on purpose. GET /organization/developer returns the inventory, but minting and
// revoking keys exist only under /admin/organizations/{id}/api-keys, which requires
// super-admin. Rendering an enabled "Create key" button here would produce a 403 on click, so
// the action states who can do it and routes to Support instead. This page gets its write
// half the day an org-scoped endpoint does — no frontend change can grant that authority.
const WARN_DAYS = 30;

const daysUntil = (iso) => {
  if (!iso) return null;
  const ms = new Date(iso).getTime() - Date.now();
  return Number.isNaN(ms) ? null : Math.ceil(ms / 86_400_000);
};

// A key's real state, in precedence order: revoked beats expired beats expiring.
// Tones are TONE keys (the console Badge palette), not STATUS keys. `group` is the coarse
// bucket the status filter works on, so the filter and the badge can never disagree.
function keyState(k) {
  if (k.revoked) return { tone: "neutral", label: "Revoked", group: "revoked" };
  const d = daysUntil(k.expires_at);
  if (d == null) return { tone: "success", label: "Active", group: "active" };
  if (d < 0) return { tone: "danger", label: "Expired", group: "expired" };
  if (d <= WARN_DAYS) return { tone: "warning", label: `Expires in ${d}d`, group: "expiring" };
  return { tone: "success", label: "Active", group: "active" };
}

// Only the states this page can actually produce (see keyState) — no aspirational options.
const STATUS_FILTERS = [
  { value: "all", label: "All statuses" },
  { value: "active", label: "Active", dot: <Dot className="bg-green-500" /> },
  { value: "expiring", label: `Expiring within ${WARN_DAYS} days`, dot: <Dot className="bg-amber-500" /> },
  { value: "expired", label: "Expired", dot: <Dot className="bg-rose-500" /> },
  { value: "revoked", label: "Revoked", dot: <Dot className="bg-slate-400" /> },
];

function Dot({ className }) {
  return <span aria-hidden="true" className={cx("h-1.5 w-1.5 shrink-0 rounded-full", className)} />;
}

// Handling rules, as separated items rather than a bullet list — each is a distinct decision
// an operator is being asked to make, and a run-together list gets skimmed as one.
const RULES = [
  ["Server-side only", "A key in browser or mobile code is a published key."],
  ["One key per environment", "Per integration too, so a rotation is never all-or-nothing."],
  ["Rotate on staff change", "And on any suspected exposure — not on a calendar."],
  ["Prefer short lifetimes", "A non-expiring key is a permanent liability."],
];

export default function Credentials() {
  const { data, loading, error, reload } = useApi(() =>
    api.get("/organization/developer").then((r) => r.data)
  );
  const [status, setStatus] = useState("all");
  const keys = useMemo(() => data?.api_keys || [], [data]);

  const summary = useMemo(() => {
    const active = keys.filter((k) => !k.revoked && (daysUntil(k.expires_at) ?? 1) >= 0);
    const expiring = active.filter((k) => {
      const d = daysUntil(k.expires_at);
      return d != null && d <= WARN_DAYS;
    });
    return {
      total: keys.length,
      active: active.length,
      expiring: expiring.length,
      nonExpiring: active.filter((k) => !k.expires_at).length,
    };
  }, [keys]);

  // Client-side narrowing of an already-fetched inventory — no new request, no API change.
  const visible = useMemo(
    () => (status === "all" ? keys : keys.filter((k) => keyState(k).group === status)),
    [keys, status]
  );

  const copyPrefix = async (prefix) => {
    try {
      await navigator.clipboard.writeText(prefix);
      notify.success("Key prefix copied");
    } catch {
      notify.error("Clipboard is unavailable in this browser");
    }
  };

  const columns = [
    {
      key: "label",
      header: "Credential",
      sortable: true,
      render: (k) => (
        <div className="flex min-w-0 items-center gap-3">
          <span
            className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-violet-100 text-violet-600 dark:bg-violet-500/15 dark:text-violet-400"
            aria-hidden="true"
          >
            <FiKey className="text-[14px]" />
          </span>
          <div className="min-w-0">
            <p className={cx("truncate text-[13px] font-semibold", CONSOLE.heading)}>
              {k.label || "Untitled key"}
            </p>
            <p className={cx("truncate text-[11px]", CONSOLE.faint)}>
              Created {k.created_at ? timeAgo(k.created_at) : "—"}
            </p>
          </div>
        </div>
      ),
    },
    {
      key: "prefix",
      header: "Prefix",
      mono: true,
      // The secret is shown once at creation and never stored in readable form, so the
      // prefix is all this console can ever display. Saying so beats a masked field that
      // implies the rest is retrievable.
      render: (k) => (
        <span className="inline-flex items-center gap-2">
          <span
            className={cx(
              type.mono,
              "rounded-md border px-2 py-1 text-[12px]",
              CONSOLE.divider,
              CONSOLE.body,
              "bg-slate-50 dark:bg-white/[0.03]"
            )}
          >
            {k.prefix || "—"}
          </span>
          <span className={cx("text-[11px]", CONSOLE.faint)}>••••••••</span>
        </span>
      ),
    },
    {
      key: "expires_at",
      header: "Expires",
      sortable: true,
      sortValue: (k) => (k.expires_at ? new Date(k.expires_at).getTime() : Infinity),
      render: (k) =>
        k.expires_at ? (
          <span className={cx("text-[13px]", CONSOLE.body)}>
            {new Date(k.expires_at).toLocaleDateString()}
          </span>
        ) : (
          <span className={cx("text-[13px]", CONSOLE.faint)} title="Minted without a lifetime">
            Never
          </span>
        ),
    },
    {
      key: "state",
      header: "Status",
      align: "right",
      render: (k) => {
        const s = keyState(k);
        return <Badge tone={s.tone} dot>{s.label}</Badge>;
      },
    },
  ];

  return (
    <div className="space-y-6">
      <OrganizationPageHeader
        title="Credentials"
        subtitle="API keys this organization authenticates with. Secrets are shown once at creation and never stored in readable form."
        actions={
          <>
            <ConsoleButton variant="secondary" leftIcon={FiRefreshCw} onClick={reload} loading={loading}>
              Refresh
            </ConsoleButton>
            {/* Deliberately not a create button — see the file header. */}
            <ConsoleButton href="/organization/support" leftIcon={FiLifeBuoy}>
              Request a key
            </ConsoleButton>
          </>
        }
      />

      {error && <OrganizationErrorState error={error} onRetry={reload} title="Couldn't load credentials" />}

      {/* Inventory at a glance. Every figure is derived from the fetched key list — the
          accents carry meaning (green healthy, amber needs attention) rather than decorating. */}
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <MetricCard
          icon={FiKey}
          accent="violet"
          label="Credentials total"
          value={summary.total}
          note="All environments"
          loading={loading}
        />
        <MetricCard
          icon={FiCheckCircle}
          accent="green"
          label="Active"
          value={summary.active}
          note="Usable right now"
          loading={loading}
        />
        <MetricCard
          icon={FiClock}
          accent="amber"
          label={`Expiring within ${WARN_DAYS} days`}
          value={summary.expiring}
          note={summary.expiring ? "Plan a rotation" : "Nothing due"}
          loading={loading}
        />
        <MetricCard
          icon={FiSlash}
          accent="blue"
          label="Non-expiring keys"
          value={summary.nonExpiring}
          note={summary.nonExpiring ? "Permanent liability" : "No permanent keys"}
          loading={loading}
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
        <Panel title="Key inventory" count={summary.expiring} flush>
          <DataTable
            columns={columns}
            rows={visible}
            rowKey={(k) => k.id || k.prefix}
            loading={loading}
            searchable
            searchShortcut
            searchKeys={["label", "prefix"]}
            searchPlaceholder="Search by label or prefix…"
            toolbar={
              <Dropdown
                label="Status"
                className="w-[13.5rem]"
                width="w-[15rem]"
                value={status}
                onChange={setStatus}
                options={STATUS_FILTERS}
                icon={FiFilter}
              />
            }
            initialSort={{ key: "label", dir: "asc" }}
            pageSize={10}
            minWidth={680}
            rowActions={(k) =>
              k.prefix ? (
                <button
                  type="button"
                  onClick={() => copyPrefix(k.prefix)}
                  aria-label={`Copy the prefix of ${k.label || "this key"}`}
                  title="Copy prefix"
                  className={cx(
                    "rounded-md p-1.5 transition-colors duration-150 motion-reduce:transition-none",
                    CONSOLE.faint,
                    "hover:bg-slate-100 hover:text-slate-900 dark:hover:bg-white/10 dark:hover:text-white",
                    focusRing
                  )}
                >
                  <FiCopy className="text-[14px]" />
                </button>
              ) : null
            }
            empty={
              // The filtered-to-nothing case is a different problem from having no keys at
              // all, and offering "Request a key" to someone who has ten of them is noise.
              status === "all"
                ? {
                    icon: FiKey,
                    title: "No credentials yet",
                    description:
                      "This organization has no API keys. Ask the platform team to mint one for your workspace — creation is a platform-admin operation.",
                    action: (
                      <ConsoleButton href="/organization/support" size="sm" leftIcon={FiLifeBuoy}>
                        Request a key
                      </ConsoleButton>
                    ),
                  }
                : {
                    icon: FiFilter,
                    title: "No keys match this status",
                    description: `None of this organization's ${summary.total} ${
                      summary.total === 1 ? "key is" : "keys are"
                    } in that state.`,
                    action: (
                      <ConsoleButton variant="secondary" size="sm" onClick={() => setStatus("all")}>
                        Clear filter
                      </ConsoleButton>
                    ),
                  }
            }
          />
        </Panel>

        <div className="space-y-4">
          <Panel title="Security posture">
            <StatRow
              label="Credentials total"
              value={loading ? null : summary.total}
              reason="Loading"
              dot="brand"
              separated
            />
            <StatRow
              label="Active"
              value={loading ? null : summary.active}
              reason="Loading"
              dot="success"
              separated
            />
            <StatRow
              label={`Expiring within ${WARN_DAYS} days`}
              value={loading ? null : summary.expiring}
              reason="Loading"
              dot={summary.expiring ? "warning" : "neutral"}
              tone={summary.expiring ? "text-amber-600 dark:text-amber-400" : undefined}
              separated
            />
            <StatRow
              label="Non-expiring keys"
              value={loading ? null : summary.nonExpiring}
              reason="Loading"
              dot={summary.nonExpiring ? "warning" : "neutral"}
              tone={summary.nonExpiring ? "text-amber-600 dark:text-amber-400" : undefined}
              separated
            />
            <StatRow
              label="Requests per key (24h)"
              value={null}
              reason="Per-credential request attribution is not integrated"
              dot="neutral"
              separated
            />
          </Panel>

          <Panel title="Handling rules">
            <ul className="space-y-1">
              {RULES.map(([title, body]) => (
                <li
                  key={title}
                  className={cx(
                    "group flex gap-2.5 rounded-lg p-2.5",
                    "transition-colors duration-150 motion-reduce:transition-none",
                    "hover:bg-slate-50 dark:hover:bg-white/[0.03]"
                  )}
                >
                  <FiShield
                    className={cx(
                      "mt-0.5 shrink-0 text-[14px] transition-colors duration-150 motion-reduce:transition-none",
                      CONSOLE.faint,
                      "group-hover:text-violet-600 dark:group-hover:text-violet-400"
                    )}
                    aria-hidden="true"
                  />
                  <div className="min-w-0">
                    <p className={cx("text-[13px] font-semibold", CONSOLE.heading)}>{title}</p>
                    <p className={cx("mt-0.5 text-[12px] leading-relaxed", CONSOLE.faint)}>{body}</p>
                  </div>
                </li>
              ))}
            </ul>
            <p className={cx("mt-3 border-t pt-3 text-[12px]", CONSOLE.divider, CONSOLE.faint)}>
              Creating and revoking keys is a platform-admin operation.{" "}
              <Link to="/organization/support" className={cx("font-semibold", CONSOLE.link)}>
                Open a request
              </Link>
              .
            </p>
          </Panel>
        </div>
      </div>
    </div>
  );
}
