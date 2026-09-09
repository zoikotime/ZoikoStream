import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import api, { errMsg } from "../api";
import {
  AlertTriangle, BadgeCheck, Bug, ChevronDown, FileText, History, Loader2, Lock,
  PencilLine, ShieldCheck,
} from "lucide-react";
import { Logo } from "../ui";

// Trust — the public Trust Center (ZST-EC-001 TRU-001 -> TRU-003).
//
// This is what every security advisory email links to, and where a vendor-security reviewer
// requests evidence and a researcher reports a vulnerability. Unauthenticated by design: a
// researcher has no account here, and neither does a reviewer at a prospect.
//
// ── WHAT THIS PAGE CAN AND CANNOT SHOW ───────────────────────────────────────────────────
// Everything comes from GET /api/trust, which serves PUBLISHED advisories and document
// METADATA only. There is no internal incident, no root-cause draft, no reporter identity
// and no `storage_reference` in that payload, so this file cannot leak them — it never asks.
//
// A confidential document is never a link. The catalogue shows what exists and says plainly
// that access needs an approved request; the approved link arrives by email, bound to one
// address, purpose, scope and document, and it expires.
//
// ── WHY THE ADVISORY HISTORY IS RENDERED IN FULL ─────────────────────────────────────────
// Published advisory versions are append-only on the server. A customer who acted on
// version 1 must be able to read what version 1 said, so every version is shown with what
// changed — hiding superseded text here would undo the guarantee the backend makes.

const SEVERITY_TONE = {
  low: { chip: "border-slate-200 bg-slate-50 text-slate-700", dot: "bg-slate-400" },
  medium: { chip: "border-amber-200 bg-amber-50 text-amber-800", dot: "bg-amber-500" },
  high: { chip: "border-orange-200 bg-orange-50 text-orange-800", dot: "bg-orange-500" },
  critical: { chip: "border-rose-200 bg-rose-50 text-rose-800", dot: "bg-rose-600" },
};

const STATUS_LABEL = {
  published: "Published",
  updated: "Updated",
  remediation_available: "Remediation available",
  remediated: "Remediated",
  closed: "Closed",
};

const tone = (severity) => SEVERITY_TONE[severity] || SEVERITY_TONE.low;

// Advisory timestamps arrive in UTC. Render in the reader's zone, keep UTC in the title so a
// customer and an engineer comparing notes are never arguing about which clock a time was in.
const fmt = (iso) => {
  if (!iso) return null;
  try {
    return new Date(iso).toLocaleString(undefined, {
      day: "numeric", month: "short", year: "numeric", hour: "numeric", minute: "2-digit",
    });
  } catch {
    return iso;
  }
};

const utc = (iso) => {
  if (!iso) return "";
  try {
    return `${new Date(iso).toISOString().slice(0, 16).replace("T", " ")} UTC`;
  } catch {
    return iso;
  }
};

function Stamp({ iso, prefix }) {
  if (!iso) return null;
  return (
    <time dateTime={iso} title={utc(iso)} className="whitespace-nowrap">
      {prefix ? `${prefix} ` : ""}{fmt(iso)}
    </time>
  );
}

function Chip({ className = "", children }) {
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-semibold uppercase tracking-wide ${className}`}>
      {children}
    </span>
  );
}

function Field({ label, children }) {
  if (!children) return null;
  return (
    <div className="flex flex-col gap-0.5 sm:flex-row sm:gap-3">
      <dt className="shrink-0 text-[12px] font-semibold uppercase tracking-wide text-slate-500 sm:w-44">
        {label}
      </dt>
      <dd className="whitespace-pre-line text-[13px] leading-relaxed text-slate-700">
        {children}
      </dd>
    </div>
  );
}

// ── advisory ─────────────────────────────────────────────────────────────────────────────

function Advisory({ advisory }) {
  const [open, setOpen] = useState(false);
  const t = tone(advisory.severity);
  const history = useMemo(
    () => [...(advisory.history || [])].sort((a, b) => b.version - a.version),
    [advisory.history],
  );
  // A version that a later correction supersedes stays published, and stays labelled.
  const superseded = useMemo(
    () => new Set(history.filter((h) => h.type === "update").map((h) => h.version - 1)),
    [history],
  );

  return (
    <article className="overflow-hidden rounded-2xl border border-slate-200 bg-white">
      <header className="flex flex-wrap items-start gap-x-4 gap-y-3 border-b border-slate-100 px-5 py-4">
        <span aria-hidden="true" className={`mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full ${t.dot}`} />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-[15px] font-semibold text-slate-900">{advisory.title}</h3>
            <Chip className={t.chip}>{advisory.severity_label}</Chip>
            <Chip className="border-slate-200 bg-slate-50 text-slate-600">
              {STATUS_LABEL[advisory.status] || advisory.status}
            </Chip>
          </div>
          <p className="mt-1 text-[13px] text-slate-500">
            <span className="font-mono text-slate-400">{advisory.reference}</span>
            {advisory.components?.length ? ` · ${advisory.components.join(", ")}` : ""}
          </p>
        </div>
        <div className="text-right text-[12px] text-slate-500">
          <Stamp iso={advisory.published_at} prefix="Published" />
          {advisory.closed_at && (
            <div className="text-emerald-700"><Stamp iso={advisory.closed_at} prefix="Closed" /></div>
          )}
        </div>
      </header>

      <div className="space-y-3 px-5 py-4">
        <p className="whitespace-pre-line text-[14px] leading-relaxed text-slate-700">
          {advisory.summary}
        </p>
        <dl className="space-y-2.5">
          <Field label="What this means for you">{advisory.customer_impact}</Field>
          {/* Only rendered when somebody recorded it. Never "all versions" by default. */}
          <Field label="Affected versions">{advisory.affected_versions}</Field>
          <Field label="Scope">{advisory.affected_scope_note}</Field>
          {/* Only present if a human recorded a real vector. There is no scoring here. */}
          <Field label="CVSS vector">{advisory.cvss_vector}</Field>
          <Field label="Immediate mitigation">{advisory.immediate_mitigation}</Field>
          <Field label="Workaround">
            {advisory.workaround_available && advisory.workaround_summary
              ? advisory.workaround_summary
              : "No workaround is available."}
          </Field>
          <Field label="Fixed version">{advisory.fixed_version}</Field>
          <Field label="Remediation steps">{advisory.remediation_steps}</Field>
          <Field label="Deadline">
            {advisory.remediation_deadline
              ? utc(advisory.remediation_deadline)
              : "No deadline has been set for this advisory."}
          </Field>
        </dl>

        {/* Imperative wording appears only when the advisory records the action as
            mandatory. Severity alone never licenses "upgrade immediately". */}
        {advisory.remediation_steps && (
          <p className={`rounded-xl border px-3.5 py-2.5 text-[13px] leading-relaxed ${
            advisory.action_mandatory
              ? "border-rose-200 bg-rose-50 text-rose-900"
              : "border-sky-200 bg-sky-50 text-sky-900"
          }`}>
            <strong className="font-semibold">
              {advisory.action_mandatory ? "Required action: " : "Recommended: "}
            </strong>
            {advisory.action_mandatory
              ? "apply this update as soon as you are able."
              : "apply this update during your normal change process."}
          </p>
        )}

        {advisory.status === "closed" && (
          <p className="rounded-xl border border-slate-200 bg-slate-50 px-3.5 py-2.5 text-[13px] leading-relaxed text-slate-700">
            <strong className="font-semibold">What closure means: </strong>
            {advisory.closure_note ? `${advisory.closure_note} ` : ""}
            This advisory is closed on our side. It does not confirm the update has been
            applied in your organization.
          </p>
        )}

        {history.length > 0 && (
          <>
            <button
              type="button"
              onClick={() => setOpen((v) => !v)}
              aria-expanded={open}
              className="inline-flex items-center gap-1.5 text-[13px] font-semibold text-slate-600 transition hover:text-slate-900"
            >
              <ChevronDown className={`h-4 w-4 transition ${open ? "rotate-180" : ""}`} aria-hidden="true" />
              {open ? "Hide" : "Show"} published history ({history.length})
            </button>
            {open && (
              <ol className="space-y-3 border-l border-slate-200 pl-4">
                {history.map((v) => (
                  <li key={v.version} className="relative">
                    <span aria-hidden="true" className="absolute -left-[21px] top-1.5 h-2 w-2 rounded-full bg-slate-300" />
                    <div className="flex flex-wrap items-center gap-2 text-[12px]">
                      <span className="font-semibold uppercase tracking-wide text-slate-500">
                        v{v.version} · {v.type}
                      </span>
                      <span className="text-slate-400"><Stamp iso={v.published_at} /></span>
                      <Chip className={tone(v.severity).chip}>{v.severity}</Chip>
                      {superseded.has(v.version) && (
                        <Chip className="border-amber-200 bg-amber-50 text-amber-800">
                          <PencilLine className="h-3 w-3" aria-hidden="true" />
                          Later updated
                        </Chip>
                      )}
                    </div>
                    {v.change_summary && (
                      <p className="mt-1 text-[13px] font-medium leading-relaxed text-slate-800">
                        {v.change_summary}
                      </p>
                    )}
                    {v.changed_fields?.length > 0 && (
                      <p className="mt-0.5 text-[12px] text-slate-500">
                        Changed: {v.changed_fields.join(", ")}
                      </p>
                    )}
                    <p className="mt-1 whitespace-pre-line text-[13px] leading-relaxed text-slate-600">
                      {v.summary}
                    </p>
                  </li>
                ))}
              </ol>
            )}
          </>
        )}
      </div>
    </article>
  );
}

// ── evidence request ─────────────────────────────────────────────────────────────────────

function EvidenceRequest({ documents, purposes, scopes }) {
  const [documentId, setDocumentId] = useState("");
  const [purpose, setPurpose] = useState("");
  const [scope, setScope] = useState("");
  const [form, setForm] = useState({ requester_email: "", requester_name: "", company_name: "", purpose_note: "" });
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState(null);
  const [error, setError] = useState(null);

  const selected = documents.find((d) => d.id === documentId);
  // Purpose and scope are bound to the DOCUMENT: only what it allows can be requested, and
  // an empty allow-list permits nothing.
  const allowedPurposes = selected ? selected.allowed_purposes : purposes;
  const allowedScopes = selected ? selected.allowed_scopes : scopes;

  const set = (key) => (e) => setForm((f) => ({ ...f, [key]: e.target.value }));

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setNote(null);
    try {
      const { data } = await api.post("/trust/evidence/requests", {
        ...form, document_id: documentId, purpose, scope,
      });
      setNote(`Request ${data.reference} received. We review requests individually and will email you when a decision has been made — nothing is released automatically.`);
      setForm({ requester_email: "", requester_name: "", company_name: "", purpose_note: "" });
      setDocumentId("");
      setPurpose("");
      setScope("");
    } catch (err) {
      setError(errMsg(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section id="evidence" className="rounded-2xl border border-slate-200 bg-white p-5 sm:p-6">
      <div className="flex items-start gap-3">
        <span aria-hidden="true" className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-slate-900 text-white">
          <FileText className="h-4 w-4" />
        </span>
        <div className="min-w-0">
          <h2 className="text-[17px] font-semibold text-slate-900">Request trust documentation</h2>
          <p className="mt-1 text-[13px] leading-relaxed text-slate-600">
            Confidential documents are released under an approved request. Approved access is
            issued to one address, for one document, purpose and scope, and it expires — so
            please request it for the address that will actually read it.
          </p>
        </div>
      </div>

      <ul className="mt-5 divide-y divide-slate-100 overflow-hidden rounded-xl border border-slate-200">
        {documents.map((d) => (
          <li key={d.id} className="flex flex-wrap items-center justify-between gap-3 px-4 py-3">
            <div className="min-w-0">
              <p className="text-[14px] font-medium text-slate-800">{d.title}</p>
              <p className="text-[12px] text-slate-500">Version {d.version}</p>
            </div>
            {d.requires_approval ? (
              <Chip className="border-slate-200 bg-slate-50 text-slate-600">
                <Lock className="h-3 w-3" aria-hidden="true" />
                Request required
              </Chip>
            ) : (
              <Chip className="border-emerald-200 bg-emerald-50 text-emerald-700">
                <BadgeCheck className="h-3 w-3" aria-hidden="true" />
                Public
              </Chip>
            )}
          </li>
        ))}
        {documents.length === 0 && (
          <li className="px-4 py-6 text-center text-[13px] text-slate-500">
            No documents are published yet.
          </li>
        )}
      </ul>

      {documents.length > 0 && (
        <form onSubmit={submit} className="mt-5 grid gap-4 sm:grid-cols-2">
          <label className="block sm:col-span-2">
            <span className="text-[12px] font-semibold uppercase tracking-wide text-slate-500">Document</span>
            <select
              required
              value={documentId}
              onChange={(e) => { setDocumentId(e.target.value); setPurpose(""); setScope(""); }}
              className="mt-2 w-full rounded-xl border border-slate-200 px-3 py-2.5 text-[14px] text-slate-900 outline-none focus:border-slate-400"
            >
              <option value="">Select a document</option>
              {documents.map((d) => (
                <option key={d.id} value={d.id}>{d.title} ({d.version})</option>
              ))}
            </select>
          </label>

          <label className="block">
            <span className="text-[12px] font-semibold uppercase tracking-wide text-slate-500">Purpose</span>
            <select
              required
              value={purpose}
              onChange={(e) => setPurpose(e.target.value)}
              className="mt-2 w-full rounded-xl border border-slate-200 px-3 py-2.5 text-[14px] text-slate-900 outline-none focus:border-slate-400"
            >
              <option value="">Select a purpose</option>
              {allowedPurposes.map((p) => (
                <option key={p} value={p}>{p.replace(/_/g, " ")}</option>
              ))}
            </select>
          </label>

          <label className="block">
            <span className="text-[12px] font-semibold uppercase tracking-wide text-slate-500">Scope</span>
            <select
              required
              value={scope}
              onChange={(e) => setScope(e.target.value)}
              className="mt-2 w-full rounded-xl border border-slate-200 px-3 py-2.5 text-[14px] text-slate-900 outline-none focus:border-slate-400"
            >
              <option value="">Select a scope</option>
              {allowedScopes.map((s) => (
                <option key={s} value={s}>{s.replace(/_/g, " ")}</option>
              ))}
            </select>
          </label>

          <label className="block">
            <span className="text-[12px] font-semibold uppercase tracking-wide text-slate-500">Your email</span>
            <input
              type="email" required value={form.requester_email} onChange={set("requester_email")}
              placeholder="you@company.com"
              className="mt-2 w-full rounded-xl border border-slate-200 px-3 py-2.5 text-[14px] text-slate-900 outline-none placeholder:text-slate-400 focus:border-slate-400"
            />
          </label>

          <label className="block">
            <span className="text-[12px] font-semibold uppercase tracking-wide text-slate-500">Company</span>
            <input
              type="text" value={form.company_name} onChange={set("company_name")}
              className="mt-2 w-full rounded-xl border border-slate-200 px-3 py-2.5 text-[14px] text-slate-900 outline-none focus:border-slate-400"
            />
          </label>

          <label className="block sm:col-span-2">
            <span className="text-[12px] font-semibold uppercase tracking-wide text-slate-500">
              What are you reviewing? (optional)
            </span>
            <textarea
              rows={3} value={form.purpose_note} onChange={set("purpose_note")}
              className="mt-2 w-full rounded-xl border border-slate-200 px-3 py-2.5 text-[14px] text-slate-900 outline-none focus:border-slate-400"
            />
          </label>

          <div className="sm:col-span-2">
            <button
              type="submit" disabled={busy}
              className="inline-flex items-center gap-2 rounded-xl bg-slate-900 px-4 py-2.5 text-[14px] font-semibold text-white transition hover:bg-slate-800 disabled:opacity-60"
            >
              {busy && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
              Submit request
            </button>
          </div>
        </form>
      )}

      {note && (
        <p className="mt-4 rounded-xl border border-emerald-200 bg-emerald-50 px-3.5 py-2.5 text-[13px] leading-relaxed text-emerald-900">{note}</p>
      )}
      {error && (
        <p className="mt-4 rounded-xl border border-rose-200 bg-rose-50 px-3.5 py-2.5 text-[13px] leading-relaxed text-rose-900">{error}</p>
      )}
    </section>
  );
}

// ── vulnerability report ─────────────────────────────────────────────────────────────────

function ReportVulnerability({ categories, policy }) {
  const [form, setForm] = useState({
    reporter_email: "", reporter_name: "", title: "", category: "",
    affected_service: "", description: "", reproduction: "",
  });
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);

  const set = (key) => (e) => setForm((f) => ({ ...f, [key]: e.target.value }));

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const { data } = await api.post("/trust/security/reports", form);
      setResult(data);
      setForm({
        reporter_email: "", reporter_name: "", title: "", category: "",
        affected_service: "", description: "", reproduction: "",
      });
    } catch (err) {
      setError(errMsg(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section id="report" className="rounded-2xl border border-slate-200 bg-white p-5 sm:p-6">
      <div className="flex items-start gap-3">
        <span aria-hidden="true" className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-slate-900 text-white">
          <Bug className="h-4 w-4" />
        </span>
        <div className="min-w-0">
          <h2 className="text-[17px] font-semibold text-slate-900">Report a security issue</h2>
          <p className="mt-1 text-[13px] leading-relaxed text-slate-600">
            This goes to our security team, not to general support. We will confirm receipt and
            tell you what we find.
          </p>
        </div>
      </div>

      {/* Stated up front rather than left to be inferred. Nothing about this programme is
          implied by omission — the API returns these as facts and the page repeats them. */}
      <dl className="mt-4 grid gap-2 rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-[13px] sm:grid-cols-2">
        <div className="flex gap-2">
          <dt className="font-semibold text-slate-700">Paid bounty:</dt>
          <dd className="text-slate-600">{policy?.bounty ? "Yes" : "No"}</dd>
        </div>
        <div className="flex gap-2">
          <dt className="font-semibold text-slate-700">Public credit:</dt>
          <dd className="text-slate-600">{policy?.public_credit ? "Yes" : "Not offered"}</dd>
        </div>
        <div className="flex gap-2 sm:col-span-2">
          <dt className="font-semibold text-slate-700">Coordinated disclosure policy:</dt>
          <dd className="text-slate-600">
            {policy?.coordinated_disclosure_policy
              ? "Published"
              : "None published — we will agree timing with you directly"}
          </dd>
        </div>
      </dl>

      <p className="mt-3 rounded-xl border border-amber-200 bg-amber-50 px-3.5 py-2.5 text-[13px] leading-relaxed text-amber-900">
        <strong className="font-semibold">Please do not send credentials. </strong>
        We never need a password, API key or private key to reproduce an issue, and a report
        containing one will be rejected rather than stored.
      </p>

      <form onSubmit={submit} className="mt-5 grid gap-4 sm:grid-cols-2">
        <label className="block">
          <span className="text-[12px] font-semibold uppercase tracking-wide text-slate-500">Your email</span>
          <input
            type="email" required value={form.reporter_email} onChange={set("reporter_email")}
            className="mt-2 w-full rounded-xl border border-slate-200 px-3 py-2.5 text-[14px] text-slate-900 outline-none focus:border-slate-400"
          />
        </label>
        <label className="block">
          <span className="text-[12px] font-semibold uppercase tracking-wide text-slate-500">Your name (optional)</span>
          <input
            type="text" value={form.reporter_name} onChange={set("reporter_name")}
            className="mt-2 w-full rounded-xl border border-slate-200 px-3 py-2.5 text-[14px] text-slate-900 outline-none focus:border-slate-400"
          />
        </label>
        <label className="block sm:col-span-2">
          <span className="text-[12px] font-semibold uppercase tracking-wide text-slate-500">Summary</span>
          <input
            type="text" required minLength={4} value={form.title} onChange={set("title")}
            placeholder="One line: what is the issue?"
            className="mt-2 w-full rounded-xl border border-slate-200 px-3 py-2.5 text-[14px] text-slate-900 outline-none placeholder:text-slate-400 focus:border-slate-400"
          />
        </label>
        <label className="block">
          <span className="text-[12px] font-semibold uppercase tracking-wide text-slate-500">Category</span>
          <select
            required value={form.category} onChange={set("category")}
            className="mt-2 w-full rounded-xl border border-slate-200 px-3 py-2.5 text-[14px] text-slate-900 outline-none focus:border-slate-400"
          >
            <option value="">Select a category</option>
            {(categories || []).map((c) => (
              <option key={c} value={c}>{c.replace(/_/g, " ")}</option>
            ))}
          </select>
        </label>
        <label className="block">
          <span className="text-[12px] font-semibold uppercase tracking-wide text-slate-500">Affected service (optional)</span>
          <input
            type="text" value={form.affected_service} onChange={set("affected_service")}
            className="mt-2 w-full rounded-xl border border-slate-200 px-3 py-2.5 text-[14px] text-slate-900 outline-none focus:border-slate-400"
          />
        </label>
        <label className="block sm:col-span-2">
          <span className="text-[12px] font-semibold uppercase tracking-wide text-slate-500">Description</span>
          <textarea
            rows={5} required minLength={20} value={form.description} onChange={set("description")}
            placeholder="What happens, what you expected, and why it matters."
            className="mt-2 w-full rounded-xl border border-slate-200 px-3 py-2.5 text-[14px] text-slate-900 outline-none placeholder:text-slate-400 focus:border-slate-400"
          />
        </label>
        <label className="block sm:col-span-2">
          <span className="text-[12px] font-semibold uppercase tracking-wide text-slate-500">Steps to reproduce (optional)</span>
          <textarea
            rows={4} value={form.reproduction} onChange={set("reproduction")}
            className="mt-2 w-full rounded-xl border border-slate-200 px-3 py-2.5 text-[14px] text-slate-900 outline-none focus:border-slate-400"
          />
        </label>
        <div className="sm:col-span-2">
          <button
            type="submit" disabled={busy}
            className="inline-flex items-center gap-2 rounded-xl bg-slate-900 px-4 py-2.5 text-[14px] font-semibold text-white transition hover:bg-slate-800 disabled:opacity-60"
          >
            {busy && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
            Submit report
          </button>
        </div>
      </form>

      {result && (
        <div className="mt-4 rounded-xl border border-emerald-200 bg-emerald-50 px-3.5 py-3 text-[13px] leading-relaxed text-emerald-900">
          <p>
            <strong className="font-semibold">Report {result.reference} received. </strong>
            We have emailed you a private link to follow it. Keep it — it is the only way to
            check status without an account.
          </p>
          {/* Shown once, here, because the emailed copy may not arrive. */}
          <p className="mt-2 break-all font-mono text-[12px]">
            {`${window.location.origin}/security/report/${result.reference}?t=${result.portal_token}`}
          </p>
        </div>
      )}
      {error && (
        <p className="mt-4 rounded-xl border border-rose-200 bg-rose-50 px-3.5 py-2.5 text-[13px] leading-relaxed text-rose-900">{error}</p>
      )}
    </section>
  );
}

// ── page ─────────────────────────────────────────────────────────────────────────────────

export default function Trust() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);

  const load = useCallback(async () => {
    try {
      const { data: payload } = await api.get("/trust");
      setData(payload);
      setFailed(false);
    } catch {
      setFailed(true);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    load();
  }, [load]);

  const open = (data?.advisories || []).filter((a) => a.status !== "closed");
  const closed = (data?.advisories || []).filter((a) => a.status === "closed");

  return (
    <main className="min-h-screen bg-slate-50">
      <section className="bg-[#050a14] text-white">
        <div className="mx-auto flex max-w-5xl flex-wrap items-center justify-between gap-4 px-6 py-6 lg:px-8">
          <Link to="/" aria-label="ZoikoStream home"><Logo height="h-7" /></Link>
          <div className="flex gap-2">
            <a href="#evidence" className="inline-flex items-center gap-2 rounded-xl border border-white/15 bg-white/[0.04] px-3.5 py-2 text-[13px] font-medium text-white/85 transition hover:border-white/30 hover:bg-white/[0.08]">
              <FileText className="h-4 w-4" aria-hidden="true" />
              Request documents
            </a>
            <a href="#report" className="inline-flex items-center gap-2 rounded-xl border border-white/15 bg-white/[0.04] px-3.5 py-2 text-[13px] font-medium text-white/85 transition hover:border-white/30 hover:bg-white/[0.08]">
              <Bug className="h-4 w-4" aria-hidden="true" />
              Report an issue
            </a>
          </div>
        </div>
        <div className="mx-auto max-w-5xl px-6 pb-12 pt-4 lg:px-8">
          <p className="text-[11px] font-semibold uppercase tracking-[0.2em] text-teal-300">
            ZoikoStream Trust Center
          </p>
          <h1 className="mt-3 flex items-start gap-3 text-[1.9rem] font-bold leading-tight tracking-tight sm:text-[2.4rem]">
            <ShieldCheck className="mt-1 h-7 w-7 shrink-0 text-white/80" aria-hidden="true" />
            Security, transparently.
          </h1>
          <p className="mt-3 max-w-xl text-[14px] leading-relaxed text-white/60">
            Published security advisories, the documentation we can share under an approved
            request, and a protected channel for reporting a vulnerability. Times are shown in
            your local timezone; hover any timestamp for UTC.
          </p>
        </div>
      </section>

      <div className="mx-auto max-w-5xl space-y-8 px-6 py-10 lg:px-8">
        {loading && (
          <p className="flex items-center gap-2 text-[14px] text-slate-500">
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            Loading…
          </p>
        )}
        {failed && (
          <p className="rounded-2xl border border-rose-200 bg-rose-50 px-5 py-4 text-[14px] leading-relaxed text-rose-900">
            We could not load the Trust Center just now. Please try again shortly — and if you
            are trying to report a security issue urgently, that form is below and will still
            submit.
          </p>
        )}

        {!loading && data && (
          <>
            <section>
              <h2 className="flex items-center gap-2 text-[13px] font-semibold uppercase tracking-wide text-slate-500">
                <AlertTriangle className="h-4 w-4" aria-hidden="true" />
                Open advisories
              </h2>
              <div className="mt-3 space-y-4">
                {open.map((a) => <Advisory key={a.reference} advisory={a} />)}
                {open.length === 0 && (
                  <p className="rounded-2xl border border-slate-200 bg-white px-5 py-8 text-center text-[14px] text-slate-500">
                    No open security advisories.
                  </p>
                )}
              </div>
            </section>

            <EvidenceRequest
              documents={data.documents || []}
              purposes={data.purposes || []}
              scopes={data.scopes || []}
            />

            <ReportVulnerability
              categories={data.vulnerability_categories}
              policy={data.disclosure_policy}
            />

            {closed.length > 0 && (
              <section>
                <h2 className="flex items-center gap-2 text-[13px] font-semibold uppercase tracking-wide text-slate-500">
                  <History className="h-4 w-4" aria-hidden="true" />
                  Closed advisories
                </h2>
                <div className="mt-3 space-y-4">
                  {closed.map((a) => <Advisory key={a.reference} advisory={a} />)}
                </div>
              </section>
            )}
          </>
        )}
      </div>
    </main>
  );
}
