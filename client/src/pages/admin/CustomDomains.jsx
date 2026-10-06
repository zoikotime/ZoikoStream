// Support console: every organization's custom domain and the evidence behind its state.
//
// GET /admin/custom-domains (server/app/services/custom_domains.py admin_view). Staff can see
// the requested hostname, the CNAME and ownership-TXT results, the certificate state, the
// dates and the exact failure, and can re-run the checks, disable, re-enable or release a
// claim. There is deliberately NO "mark verified" action: verified and active come only from
// the DNS records, the certificate provider and the HTTPS probe. The verification token is
// never sent to this page.
import { useMemo, useState } from "react";
import toast from "react-hot-toast";
import { FiCheck, FiGlobe, FiPlay, FiRefreshCw, FiSearch, FiSlash, FiTrash2, FiX } from "react-icons/fi";
import { Badge, Button, CONSOLE, DataTable, Panel, StatCard, timeAgo } from "../../components/admin";
import api, { errMsg } from "../../api";
import useApi from "../../hooks/useApi";

const NONE = [];

const STATUS = {
  pending_dns: { label: "Pending DNS", tone: "warning" },
  verifying: { label: "Verifying", tone: "info" },
  verified: { label: "Verified · cert pending", tone: "info" },
  active: { label: "Active", tone: "success" },
  failed: { label: "Failed", tone: "danger" },
  disabled: { label: "Disabled", tone: "neutral" },
};

// Why the feature is off, in operator terms (custom_domains.availability()).
const UNAVAILABLE = {
  provider_unconfigured: "No certificate provider is configured (CUSTOM_DOMAIN_PROVIDER, plus CLOUDFLARE_ZONE_ID and CLOUDFLARE_API_TOKEN for Cloudflare).",
  cname_target_unconfigured: "CUSTOM_DOMAIN_CNAME_TARGET is not set.",
  platform_hosts_unconfigured: "CUSTOM_DOMAIN_PLATFORM_HOSTS is not set.",
  cname_target_unresolved: "The CNAME target does not resolve in public DNS, so customers would be pointing at nothing.",
};

function Check({ ok, label }) {
  if (ok === null || ok === undefined) return <span className="text-slate-300 dark:text-slate-600">—</span>;
  return (
    <span className={`inline-flex items-center gap-1 text-xs font-medium ${ok ? "text-emerald-600 dark:text-emerald-400" : "text-rose-600 dark:text-rose-400"}`}>
      {ok ? <FiCheck aria-hidden="true" /> : <FiX aria-hidden="true" />} {label}
    </span>
  );
}

export default function CustomDomains() {
  const { data, loading, error, reload } = useApi(() => api.get("/admin/custom-domains").then((r) => r.data));
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("all");
  const [busy, setBusy] = useState(null);

  const items = data?.items || NONE;
  const availability = data?.availability;

  const kpis = useMemo(() => ({
    total: items.length,
    pending: items.filter((d) => ["pending_dns", "verifying", "verified"].includes(d.status)).length,
    active: items.filter((d) => d.status === "active").length,
    attention: items.filter((d) => d.status === "failed" || d.status === "disabled" || d.failing_since).length,
  }), [items]);

  const filtered = useMemo(() => {
    const query = q.trim().toLowerCase();
    return items.filter((d) =>
      (status === "all" || d.status === status) &&
      (!query || d.domain.includes(query) || (d.organization || "").toLowerCase().includes(query)));
  }, [items, q, status]);

  const act = async (row, kind, request, done) => {
    setBusy(`${row.org_id}:${kind}`);
    try {
      await request();
      toast.success(done);
      reload();
    } catch (e) {
      toast.error(errMsg(e));
    } finally {
      setBusy(null);
    }
  };

  const verify = (d) => act(d, "verify", () => api.post(`/admin/custom-domains/${d.org_id}/verify`), `Checked ${d.domain}`);
  const enable = (d) => act(d, "enable", () => api.post(`/admin/custom-domains/${d.org_id}/enable`), `${d.domain} re-enabled — it must verify again`);
  const disable = (d) => {
    const reason = window.prompt(`Disable ${d.domain}? It stops serving immediately. Reason (recorded in the audit log):`);
    if (!reason || reason.trim().length < 3) return;
    act(d, "disable", () => api.post(`/admin/custom-domains/${d.org_id}/disable`, { reason: reason.trim() }), `${d.domain} disabled`);
  };
  const release = (d) => {
    const reason = window.prompt(`Release ${d.domain} from ${d.organization}? Their claim is removed and the hostname becomes available. Reason (recorded in the audit log):`);
    if (!reason || reason.trim().length < 3) return;
    act(d, "release", () => api.post(`/admin/custom-domains/${d.org_id}/release`, { reason: reason.trim() }), `${d.domain} released`);
  };

  const columns = [
    {
      key: "domain",
      header: "Domain",
      sortable: true,
      render: (d) => (
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="break-all font-mono text-[13px] font-medium text-slate-800 dark:text-slate-100">{d.domain}</span>
            <Badge tone={STATUS[d.status]?.tone || "neutral"} dot>{STATUS[d.status]?.label || d.status}</Badge>
          </div>
          <span className="text-xs text-slate-400">{d.organization}</span>
        </div>
      ),
    },
    {
      key: "cname_ok",
      header: "CNAME",
      render: (d) => (
        <div>
          <Check ok={d.cname_ok} label={d.cname_ok ? "Routed" : "Missing"} />
          {d.cname_found && !d.cname_ok && <p className="mt-0.5 break-all font-mono text-[11px] text-slate-400">→ {d.cname_found}</p>}
        </div>
      ),
    },
    { key: "txt_ok", header: "Ownership", render: (d) => <Check ok={d.txt_ok} label={d.txt_ok ? "Proven" : d.txt_present ? "Wrong value" : "Missing"} /> },
    { key: "certificate_status", header: "Certificate", render: (d) => <span className="text-xs">{d.certificate_status ? d.certificate_status.replace(/_/g, " ") : "—"}</span> },
    { key: "requested_at", header: "Requested", sortable: true, render: (d) => <span className="text-xs">{d.requested_at ? timeAgo(d.requested_at) : "—"}</span> },
    { key: "verified_at", header: "Verified", render: (d) => <span className="text-xs">{d.verified_at ? timeAgo(d.verified_at) : "—"}</span> },
    { key: "last_checked_at", header: "Last check", sortable: true, render: (d) => <span className="text-xs">{d.last_checked_at ? timeAgo(d.last_checked_at) : "never"}</span> },
    {
      key: "error",
      header: "Problem",
      render: (d) => d.error ? (
        <p className="max-w-xs text-xs text-rose-600 dark:text-rose-400">
          {d.error}
          {d.deactivates_at && <span className="mt-0.5 block font-medium">Deactivates {new Date(d.deactivates_at).toLocaleString()}</span>}
        </p>
      ) : <span className="text-slate-300 dark:text-slate-600">—</span>,
    },
  ];

  const rowActions = (d) => {
    const working = busy?.startsWith(`${d.org_id}:`);
    return (
      <>
        {d.status !== "disabled" && (
          <Button variant="ghost" size="sm" iconOnly title={`Re-run checks for ${d.domain}`} leftIcon={FiRefreshCw}
                  disabled={working || !availability?.available} onClick={() => verify(d)} />
        )}
        {d.status === "disabled" ? (
          <Button variant="ghost" size="sm" iconOnly title={`Re-enable ${d.domain}`} leftIcon={FiPlay} disabled={working} onClick={() => enable(d)} />
        ) : (
          <Button variant="ghost" size="sm" iconOnly title={`Disable ${d.domain}`} leftIcon={FiSlash} disabled={working} onClick={() => disable(d)} />
        )}
        <Button variant="ghost" size="sm" iconOnly title={`Release ${d.domain}`} leftIcon={FiTrash2}
                className="hover:text-rose-600 dark:hover:text-rose-400" disabled={working} onClick={() => release(d)} />
      </>
    );
  };

  if (error) {
    return (
      <div className="mx-auto max-w-[1440px] rounded-xl border border-rose-200 bg-rose-50 px-5 py-4 text-sm text-rose-700 dark:border-rose-500/20 dark:bg-rose-500/10 dark:text-rose-300">
        Couldn&apos;t load custom domains. Try refreshing the page.
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-[1440px] space-y-6">
      <div>
        <h1 className="text-[24px] font-semibold tracking-tight text-slate-900 dark:text-white">Custom Domains</h1>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
          Every organization&apos;s domain request and the DNS, ownership and certificate evidence behind its status
        </p>
      </div>

      {availability && (
        <div role="status" className={`rounded-xl border px-4 py-3 text-sm ${availability.available
          ? "border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-500/20 dark:bg-emerald-500/10 dark:text-emerald-300"
          : "border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-500/20 dark:bg-amber-500/10 dark:text-amber-300"}`}>
          {availability.available ? (
            <>Custom domains are available. Customers point a CNAME at <span className="font-mono">{availability.cname_target}</span>; certificates via <strong>{availability.provider}</strong>.</>
          ) : (
            <>Custom domains are unavailable to customers. {UNAVAILABLE[availability.reason] || availability.reason}</>
          )}
        </div>
      )}

      <div className="grid grid-cols-2 gap-4 xl:grid-cols-4">
        <StatCard label="Domains" value={kpis.total} loading={loading} />
        <StatCard label="In progress" value={kpis.pending} loading={loading} />
        <StatCard label="Active" value={kpis.active} loading={loading} />
        <StatCard label="Need attention" value={kpis.attention} loading={loading} />
      </div>

      <Panel flush>
        <div className="flex flex-wrap items-center gap-3 px-4 py-3">
          <div className="relative min-w-0 flex-1">
            <FiSearch className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search by domain or organization…" className={CONSOLE.search} />
          </div>
          <select value={status} onChange={(e) => setStatus(e.target.value)} className={CONSOLE.select} aria-label="Filter by status">
            <option value="all">All statuses</option>
            {Object.entries(STATUS).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
          </select>
        </div>
        <div className="border-t border-slate-100 dark:border-slate-800/70" />
        <DataTable
          columns={columns}
          rows={filtered}
          rowKey={(d) => d.org_id}
          loading={loading}
          rowActions={rowActions}
          initialSort={{ key: "requested_at", dir: "desc" }}
          pageSize={10}
          minWidth={1100}
          empty={{
            icon: FiGlobe,
            title: items.length ? "No domains match your filters" : "No organization has requested a custom domain",
            description: items.length ? "Try clearing the search or the status filter." : "Requests appear here as soon as an organization saves one.",
          }}
        />
      </Panel>
    </div>
  );
}
