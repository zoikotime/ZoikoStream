// Settings › General › Custom Domain.
//
// Every state on this panel is the server's (GET/PATCH/DELETE /organization/domain and
// POST /organization/domain/verify, all answered by server/app/services/custom_domains.py
// public_view). Nothing here decides that a domain is verified or active: the badge used to be
// computed locally and flipped to "Pending" the moment anyone typed, and an earlier "Verify"
// button toasted success without checking anything.
//
//   no domain        input + Add Custom Domain (no status badge: there is nothing pending)
//   pending_dns      the two records to publish, Copy CNAME / Copy TXT, Verify now
//   verified         DNS proven, certificate being provisioned
//   active           the https:// address event links now use
//   failed/disabled  exactly what is wrong, in the server's words
import { useState } from "react";
import { FiAlertTriangle, FiCheck, FiCopy, FiEdit2, FiGlobe, FiLock, FiRefreshCw, FiTrash2, FiX } from "react-icons/fi";
import api, { errMsg } from "../../api";
import Card from "../../ui/Card";
import Button from "../../ui/Button";
import Badge from "../../ui/Badge";
import { Input } from "../../ui/forms";
import { notify } from "../../ui/Toast";

const STATUS = {
  pending_dns: { label: "Pending DNS verification", badge: "warning" },
  verifying: { label: "Verifying", badge: "info" },
  verified: { label: "DNS verified · certificate pending", badge: "info" },
  active: { label: "Active", badge: "success" },
  failed: { label: "Action needed", badge: "error" },
  disabled: { label: "Disabled", badge: "neutral" },
};

const UNAVAILABLE = "Custom domains are temporarily unavailable.";

const when = (iso) => (iso ? new Date(iso).toLocaleString() : null);

// A 422 names the field ([{loc:["body","domain"], msg}]); a 409/503 is a plain sentence.
const domainError = (err) => {
  const detail = err?.response?.data?.detail;
  if (Array.isArray(detail)) {
    const hit = detail.find((d) => d?.loc?.[d.loc.length - 1] === "domain") || detail[0];
    return String(hit?.msg || "").replace(/^Value error, /, "") || errMsg(err);
  }
  return errMsg(err);
};

function StatusNote({ view }) {
  const { status, public_url: url, certificate_status: cert } = view;
  if (status === "pending_dns")
    return <>Add both records below at your DNS provider, then select <strong>Verify now</strong>. We also check automatically, so you can come back later.</>;
  if (status === "verifying") return <>Checking your DNS records…</>;
  if (status === "verified")
    return <>DNS verified. Secure certificate is being provisioned{cert ? ` (${cert.replace(/_/g, " ")})` : ""}. This usually takes a few minutes.</>;
  if (status === "active")
    return <>Event pages and event links now use <span className="font-mono font-medium text-slate-800 dark:text-slate-100">{url}</span>. Keep both records in place.</>;
  if (status === "failed") return <>Automatic checks have stopped. Fix the problem below, then select <strong>Verify now</strong>.</>;
  if (status === "disabled") return <>ZoikoStream support disabled this domain. Contact support to re-enable it, or remove it.</>;
  return null;
}

function Records({ records, check, onCopy }) {
  const ok = { CNAME: check?.cname_ok, TXT: check?.txt_ok };
  return (
    <div className="mt-4 overflow-x-auto rounded-xl border border-slate-200 dark:border-slate-700">
      <table className="w-full min-w-[560px] text-left text-sm">
        <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500 dark:bg-slate-800/60 dark:text-slate-400">
          <tr>
            <th className="px-3 py-2 font-medium">Type</th>
            <th className="px-3 py-2 font-medium">Name</th>
            <th className="px-3 py-2 font-medium">Value</th>
            <th className="px-3 py-2 font-medium"><span className="sr-only">Actions</span></th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
          {records.map((r) => (
            <tr key={r.type}>
              <td className="px-3 py-2.5 align-top">
                <span className="font-mono text-xs font-semibold text-slate-700 dark:text-slate-200">{r.type}</span>
                {check && (
                  <span className={`ml-2 inline-flex items-center gap-1 text-xs ${ok[r.type] ? "text-emerald-600 dark:text-emerald-400" : "text-slate-400"}`}>
                    {ok[r.type] ? <FiCheck aria-hidden="true" /> : <FiX aria-hidden="true" />}
                    {ok[r.type] ? "Found" : "Not found"}
                  </span>
                )}
                <p className="mt-0.5 text-xs text-slate-400">{r.purpose}</p>
              </td>
              <td className="break-all px-3 py-2.5 align-top font-mono text-xs text-slate-700 dark:text-slate-200">{r.name}</td>
              <td className="break-all px-3 py-2.5 align-top font-mono text-xs text-slate-700 dark:text-slate-200">{r.value}</td>
              <td className="px-3 py-2.5 text-right align-top">
                <Button size="sm" variant="secondary" onClick={() => onCopy(r.value, `${r.type} value`)} aria-label={`Copy ${r.type}`}>
                  <FiCopy className="text-base" /> Copy {r.type}
                </Button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="border-t border-slate-100 px-3 py-2 text-xs text-slate-400 dark:border-slate-800">
        Some DNS providers want only the part before your domain in the Name field (for example <span className="font-mono">events</span>).
        If your DNS is on Cloudflare, set the CNAME to “DNS only”.
      </p>
    </div>
  );
}

export default function CustomDomainPanel({ initial }) {
  const [view, setView] = useState(initial);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(null);

  const run = async (kind, request, success) => {
    setBusy(kind);
    try {
      const { data } = await request();
      setView(data);
      setError(null);
      if (success) notify.success(success(data));
      return data;
    } catch (err) {
      const message = domainError(err);
      setError(message);
      notify.error(message);
      return null;
    } finally {
      setBusy(null);
    }
  };

  const copy = async (text, label) => {
    try {
      await navigator.clipboard.writeText(text);
      notify.success(`${label} copied`);
    } catch {
      notify.error("Clipboard is blocked — copy it manually.");
    }
  };

  const submit = async (e) => {
    e.preventDefault();
    const value = draft.trim();
    if (!value) return;
    if (view.status === "active" && value.toLowerCase() !== view.domain &&
        !window.confirm(`Event pages will stop being served from ${view.domain} until ${value} is verified. Continue?`)) return;
    const data = await run("save", () => api.patch("/organization/domain", { domain: value }), (d) => `${d.domain} added — add the DNS records to verify it`);
    if (data) {
      setEditing(false);
      setDraft("");
    }
  };

  const verify = () =>
    run("verify", () => api.post("/organization/domain/verify"), (d) =>
      d.status === "active" ? "Domain is active" : d.status === "verified" ? "DNS verified — provisioning the certificate" : "Checked your DNS records");

  const remove = () => {
    if (!window.confirm(`Remove ${view.domain}? Event pages stop being served from it and links go back to ZoikoStream's address.`)) return;
    run("remove", () => api.delete("/organization/domain"), () => "Custom domain removed");
  };

  const status = STATUS[view.status];
  const showForm = view.available && (!view.domain || editing);

  return (
    <Card padding="lg">
      <div className="mb-5">
        <h2 className="font-semibold text-slate-900 dark:text-white">Custom Domain</h2>
        <p className="mt-0.5 text-sm text-slate-500 dark:text-slate-400">Serve your event pages from your own domain</p>
      </div>

      {!view.available && (
        <p role="status" className="mb-4 flex items-center gap-2 rounded-xl bg-slate-50 px-3 py-2.5 text-sm text-slate-600 dark:bg-slate-800/60 dark:text-slate-300">
          <FiLock aria-hidden="true" className="shrink-0" /> {UNAVAILABLE}
        </p>
      )}

      {view.domain && !editing && (
        <div>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex min-w-0 items-center gap-2">
              <FiGlobe aria-hidden="true" className="shrink-0 text-slate-400" />
              <span className="break-all font-mono text-sm font-medium text-slate-900 dark:text-white">{view.domain}</span>
              {status && <Badge status={status.badge} dot>{status.label}</Badge>}
            </div>
            <div className="flex flex-wrap gap-2">
              {view.can_verify && (
                <Button size="sm" onClick={verify} disabled={busy !== null}>
                  <FiRefreshCw className={`text-base ${busy === "verify" ? "animate-spin" : ""}`} /> {busy === "verify" ? "Checking…" : "Verify now"}
                </Button>
              )}
              {view.available && (
                <Button size="sm" variant="secondary" onClick={() => { setEditing(true); setDraft(""); setError(null); }} disabled={busy !== null}>
                  <FiEdit2 className="text-base" /> Change domain
                </Button>
              )}
              <Button size="sm" variant="secondary" onClick={remove} disabled={busy !== null}>
                <FiTrash2 className="text-base" /> Remove domain
              </Button>
            </div>
          </div>
          <p className="mt-2 text-sm text-slate-500 dark:text-slate-400"><StatusNote view={view} /></p>

          {view.error && (
            <div role="alert" className="mt-3 flex gap-2 rounded-xl bg-rose-50 px-3 py-2.5 text-sm text-rose-700 dark:bg-rose-500/10 dark:text-rose-300">
              <FiAlertTriangle aria-hidden="true" className="mt-0.5 shrink-0" />
              <div>
                <p>{view.error.message}</p>
                {view.deactivates_at && <p className="mt-1 font-medium">Stops serving on {when(view.deactivates_at)} unless the records are restored.</p>}
              </div>
            </div>
          )}

          {view.dns_records?.length > 0 && <Records records={view.dns_records} check={view.check} onCopy={copy} />}

          {view.last_checked_at && (
            <p className="mt-3 text-xs text-slate-400">Last checked {when(view.last_checked_at)}</p>
          )}
        </div>
      )}

      {showForm && (
        <form onSubmit={submit} className="flex flex-col gap-3 sm:flex-row sm:items-start" noValidate>
          <div className="flex-1">
            <label htmlFor="custom-domain-input" className="mb-1.5 block text-sm font-medium text-slate-700 dark:text-slate-300">
              {editing ? "New domain" : "Domain"}
            </label>
            <div className="relative">
              <FiGlobe className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" aria-hidden="true" />
              <Input
                id="custom-domain-input"
                variant="form"
                className="pl-9"
                maxLength={255}
                value={draft}
                error={error}
                onChange={(e) => { setDraft(e.target.value); setError(null); }}
                placeholder="events.yourcompany.com"
              />
            </div>
            {error ? (
              <p className="mt-1 text-xs font-medium text-rose-600 dark:text-rose-400">{error}</p>
            ) : (
              <p className="mt-1 text-xs text-slate-400">Use a subdomain you control, like events.yourcompany.com. You&apos;ll get two DNS records to add next.</p>
            )}
          </div>
          <div className="flex gap-2 sm:mt-7">
            <Button type="submit" size="sm" disabled={!draft.trim() || busy !== null}>
              {busy === "save" ? "Saving…" : editing ? "Save new domain" : "Add Custom Domain"}
            </Button>
            {editing && (
              <Button type="button" size="sm" variant="secondary" onClick={() => { setEditing(false); setError(null); }}>
                Cancel
              </Button>
            )}
          </div>
        </form>
      )}

      {error && view.domain && !editing && (
        <p className="mt-3 text-xs font-medium text-rose-600 dark:text-rose-400">{error}</p>
      )}
    </Card>
  );
}
