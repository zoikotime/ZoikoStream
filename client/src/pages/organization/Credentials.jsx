import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
// Feather has no "infinity" glyph, so a non-expiring key uses FiSlash — read as "no
// lifetime set", which is exactly what the metric counts.
import {
  FiKey, FiCopy, FiShield, FiRefreshCw, FiCheckCircle,
  FiClock, FiSlash, FiFilter, FiPlus, FiTrash2,
} from "react-icons/fi";
import api, { errMsg } from "../../api";
import useApi from "../../hooks/useApi";
import { CONSOLE, cx, focusRing, type } from "../../ui/tokens";
import { ConsoleButton } from "../../ui/Button";
import Badge from "../../ui/Badge";
import Dropdown from "../../ui/Dropdown";
import Modal from "../../ui/Modal";
import ConfirmDialog from "../../ui/ConfirmDialog";
import { Input, Label } from "../../ui/forms";
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
// Org-admin scoped create/revoke, mirroring the same mint/hash logic the platform's own
// super-admin console uses (crud.admin.create_api_key/revoke_api_key) — this is a separate,
// org-bound endpoint (POST/DELETE /organization/developer/api-keys), not a frontend-only
// unlock of the super-admin path.
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

// The raw key exists in the response exactly once — the platform stores only its hash
// (crud.admin.create_api_key), so it can never be shown again after this dialog closes.
function RevealKeyDialog({ created, onClose }) {
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(created.key);
      notify.success("API key copied");
    } catch {
      notify.error("Couldn't copy — select the key and copy it manually");
    }
  };
  return (
    <Modal
      open={!!created}
      onClose={onClose}
      title="API key created"
      size="lg"
      footer={
        <>
          <ConsoleButton variant="secondary" onClick={onClose}>Done</ConsoleButton>
          <ConsoleButton leftIcon={FiCopy} onClick={copy}>Copy key</ConsoleButton>
        </>
      }
    >
      {created && (
        <>
          <p className={cx("text-[13px] leading-[20px]", CONSOLE.muted)}>
            Store this somewhere safe — it is shown{" "}
            <strong className="font-semibold text-slate-800 dark:text-slate-100">only now</strong>.
            The platform keeps a hash, not the key, so it cannot be recovered later. Revoke and
            mint a new one if it's lost.
          </p>
          <code
            className={cx(
              "mt-4 block break-all rounded-lg px-3 py-2 text-[12px]",
              type.mono, CONSOLE.inset, CONSOLE.body
            )}
          >
            {created.key}
          </code>
        </>
      )}
    </Modal>
  );
}

function CreateKeyModal({ open, onClose, onCreated }) {
  const [label, setLabel] = useState("");
  const [expiresInDays, setExpiresInDays] = useState("");
  const [saving, setSaving] = useState(false);
  const [wasOpen, setWasOpen] = useState(open);
  if (open !== wasOpen) {
    setWasOpen(open);
    if (open) { setLabel(""); setExpiresInDays(""); }
  }

  const create = async () => {
    setSaving(true);
    try {
      const { data } = await api.post("/organization/developer/api-keys", {
        label: label.trim(),
        expires_in_days: expiresInDays ? Number(expiresInDays) : null,
      });
      onCreated(data);
      onClose();
    } catch (e) {
      notify.error(errMsg(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="New API key"
      size="md"
      footer={
        <>
          <ConsoleButton variant="secondary" onClick={onClose} disabled={saving}>Cancel</ConsoleButton>
          <ConsoleButton disabled={!label.trim() || saving} loading={saving} onClick={create}>
            Create key
          </ConsoleButton>
        </>
      }
    >
      <div className="space-y-4">
        <div>
          <Label variant="console" htmlFor="ck-label">Label</Label>
          <Input
            variant="console" id="ck-label" value={label}
            onChange={(e) => setLabel(e.target.value)}
            placeholder="Production — ingest worker"
            maxLength={80}
          />
        </div>
        <div>
          <Label variant="console" htmlFor="ck-expiry">Expires in (days, optional)</Label>
          <Input
            variant="console" id="ck-expiry" type="number" min="1" max="730"
            value={expiresInDays}
            onChange={(e) => setExpiresInDays(e.target.value)}
            placeholder="Leave blank for a non-expiring key"
          />
        </div>
      </div>
    </Modal>
  );
}

export default function Credentials() {
  const { data, loading, error, reload } = useApi(() =>
    api.get("/organization/developer").then((r) => r.data)
  );
  const [status, setStatus] = useState("all");
  const [creating, setCreating] = useState(false);
  const [created, setCreated] = useState(null);
  const [revoking, setRevoking] = useState(null);
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

  const revokeKey = async () => {
    try {
      await api.delete(`/organization/developer/api-keys/${revoking.id}`);
      notify.success(`${revoking.label || "Key"} revoked`);
      setRevoking(null);
      reload();
    } catch (e) {
      notify.error(errMsg(e));
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
            <ConsoleButton leftIcon={FiPlus} onClick={() => setCreating(true)}>
              New key
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
            rowActions={(k) => (
              <>
                {k.prefix && (
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
                )}
                {!k.revoked && (
                  <button
                    type="button"
                    onClick={() => setRevoking(k)}
                    aria-label={`Revoke ${k.label || "this key"}`}
                    title="Revoke"
                    className={cx(
                      "rounded-md p-1.5 transition-colors duration-150 motion-reduce:transition-none",
                      CONSOLE.faint,
                      "hover:bg-rose-50 hover:text-rose-600 dark:hover:bg-rose-500/10 dark:hover:text-rose-400",
                      focusRing
                    )}
                  >
                    <FiTrash2 className="text-[14px]" />
                  </button>
                )}
              </>
            )}
            empty={
              // The filtered-to-nothing case is a different problem from having no keys at
              // all, and offering to create one to someone who has ten already is noise.
              status === "all"
                ? {
                    icon: FiKey,
                    title: "No credentials yet",
                    description: "This organization has no API keys. Create one to authenticate a server-side integration.",
                    action: (
                      <ConsoleButton size="sm" leftIcon={FiPlus} onClick={() => setCreating(true)}>
                        New key
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
              Need help with an integration?{" "}
              <Link to="/organization/support" className={cx("font-semibold", CONSOLE.link)}>
                Open a request
              </Link>
              .
            </p>
          </Panel>
        </div>
      </div>

      <CreateKeyModal open={creating} onClose={() => setCreating(false)} onCreated={(k) => { setCreated(k); reload(); }} />
      <RevealKeyDialog created={created} onClose={() => setCreated(null)} />
      <ConfirmDialog
        open={!!revoking}
        onClose={() => setRevoking(null)}
        onConfirm={revokeKey}
        title="Revoke this key?"
        confirmLabel="Revoke"
        body={
          <>
            Anything authenticating with{" "}
            <strong className="font-semibold text-slate-800 dark:text-slate-100">
              {revoking?.label || "this key"}
            </strong>{" "}
            loses access immediately. The row stays so the revocation remains auditable.
          </>
        }
      />
    </div>
  );
}
