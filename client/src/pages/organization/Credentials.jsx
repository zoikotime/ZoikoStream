import { useMemo } from "react";
import { Link } from "react-router-dom";
import { FiKey, FiCopy, FiLifeBuoy, FiShield, FiRefreshCw } from "react-icons/fi";
import api from "../../api";
import useApi from "../../hooks/useApi";
import { CONSOLE, cx, focusRing, type } from "../../ui/tokens";
import { ConsoleButton } from "../../ui/Button";
import Badge from "../../ui/Badge";
import { notify } from "../../ui/Toast";
import DataTable from "../../components/admin/DataTable";
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
// Tones are TONE keys (the console Badge palette), not STATUS keys.
function keyState(k) {
  if (k.revoked) return { tone: "neutral", label: "Revoked" };
  const d = daysUntil(k.expires_at);
  if (d == null) return { tone: "success", label: "Active" };
  if (d < 0) return { tone: "danger", label: "Expired" };
  if (d <= WARN_DAYS) return { tone: "warning", label: `Expires in ${d}d` };
  return { tone: "success", label: "Active" };
}

export default function Credentials() {
  const { data, loading, error, reload } = useApi(() =>
    api.get("/organization/developer").then((r) => r.data)
  );
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
        <div className="min-w-0">
          <p className={cx("truncate text-[13px] font-semibold", CONSOLE.heading)}>
            {k.label || "Untitled key"}
          </p>
          <p className={cx("truncate text-[11px]", CONSOLE.faint)}>
            Created {k.created_at ? timeAgo(k.created_at) : "—"}
          </p>
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
          <span className={cx(type.mono, "text-[12px]", CONSOLE.body)}>{k.prefix || "—"}</span>
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
        return <Badge tone={s.tone}>{s.label}</Badge>;
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
            <ConsoleButton variant="secondary" leftIcon={FiRefreshCw} onClick={reload} disabled={loading}>
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

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
        <Panel title="Key inventory" count={summary.expiring} flush>
          <DataTable
            columns={columns}
            rows={keys}
            rowKey={(k) => k.id || k.prefix}
            loading={loading}
            searchable
            searchKeys={["label", "prefix"]}
            searchPlaceholder="Search by label or prefix…"
            initialSort={{ key: "label", dir: "asc" }}
            pageSize={10}
            minWidth={640}
            rowActions={(k) =>
              k.prefix ? (
                <button
                  type="button"
                  onClick={() => copyPrefix(k.prefix)}
                  aria-label={`Copy the prefix of ${k.label || "this key"}`}
                  className={cx("rounded p-1.5", CONSOLE.faint, "hover:text-slate-900 dark:hover:text-white", focusRing)}
                >
                  <FiCopy className="text-[14px]" />
                </button>
              ) : null
            }
            empty={{
              icon: FiKey,
              title: "No credentials yet",
              description:
                "This organization has no API keys. Ask the platform team to mint one for your workspace — creation is a platform-admin operation.",
              action: (
                <ConsoleButton href="/organization/support" size="sm" leftIcon={FiLifeBuoy}>
                  Request a key
                </ConsoleButton>
              ),
            }}
          />
        </Panel>

        <div className="space-y-4">
          <Panel title="Posture">
            <StatRow label="Credentials total" value={loading ? null : summary.total} reason="Loading" />
            <StatRow label="Active" value={loading ? null : summary.active} reason="Loading" />
            <StatRow
              label={`Expiring within ${WARN_DAYS} days`}
              value={loading ? null : summary.expiring}
              reason="Loading"
              tone={summary.expiring ? "text-amber-600 dark:text-amber-400" : undefined}
            />
            <StatRow
              label="Non-expiring keys"
              value={loading ? null : summary.nonExpiring}
              reason="Loading"
              tone={summary.nonExpiring ? "text-amber-600 dark:text-amber-400" : undefined}
            />
            <StatRow
              label="Requests per key (24h)"
              value={null}
              reason="Per-credential request attribution is not integrated"
            />
          </Panel>

          <Panel title="Handling rules">
            <ul className={cx("space-y-2.5 text-[13px]", CONSOLE.body)}>
              {[
                "Server-side only — a key in browser or mobile code is a published key.",
                "One key per environment and per integration, so a rotation is never all-or-nothing.",
                "Rotate on staff change and on any suspected exposure, not on a calendar.",
                "Prefer short lifetimes; a non-expiring key is a permanent liability.",
              ].map((rule) => (
                <li key={rule} className="flex gap-2.5">
                  <FiShield className={cx("mt-0.5 shrink-0 text-[14px]", CONSOLE.faint)} aria-hidden="true" />
                  <span>{rule}</span>
                </li>
              ))}
            </ul>
            <p className={cx("mt-4 border-t pt-3 text-[12px]", CONSOLE.divider, CONSOLE.faint)}>
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
