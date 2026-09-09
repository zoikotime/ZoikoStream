import { useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import api, { errMsg } from "../api";
import { Bug, CircleCheck, Clock, Loader2, ShieldCheck } from "lucide-react";
import { Logo } from "../ui";

// SecurityReport — the researcher portal (ZST-EC-001 TRU-003).
//
// A researcher usually has no account here, so this page is reached with a purpose-bound
// handle: /security/report/:reference?t=<token>. That handle grants exactly one thing —
// read of the SAFE view of their own report — and nothing else anywhere in the platform.
//
// ── WHAT IS DELIBERATELY NOT HERE ────────────────────────────────────────────────────────
// The backend serves `vuln_disclosure.public_projection()`, which has no reporter identity,
// no reproduction detail, no evidence reference and no internal analysis. This page renders
// what it is given and asks for nothing more, so there is no field through which any of that
// could appear — not even for the reporter's own report.
//
// It also promises nothing the platform has not recorded: no bounty, no credit, no
// remediation date. `coordinated_disclosure_recorded` is read from the API rather than
// assumed, so with no coordination policy configured the page says so plainly.

const STAGE_TONE = {
  received: "border-slate-200 bg-slate-50 text-slate-700",
  acknowledged: "border-sky-200 bg-sky-50 text-sky-800",
  triaged: "border-sky-200 bg-sky-50 text-sky-800",
  validating: "border-amber-200 bg-amber-50 text-amber-800",
  coordinating: "border-amber-200 bg-amber-50 text-amber-800",
  remediated: "border-emerald-200 bg-emerald-50 text-emerald-700",
  closed: "border-slate-200 bg-slate-100 text-slate-700",
};

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

function Stamp({ iso }) {
  if (!iso) return null;
  return <time dateTime={iso} title={utc(iso)}>{fmt(iso)}</time>;
}

export default function SecurityReport() {
  const { reference } = useParams();
  const [params] = useSearchParams();
  const token = params.get("t");
  const [report, setReport] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!token) {
      // Nothing to fetch, so the page has to say so - it has no other way to render.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setError("This link is missing its access code. Please use the link from our email.");
      setLoading(false);
      return undefined;
    }
    let cancelled = false;
    (async () => {
      try {
        const { data } = await api.get(
          `/trust/security/reports/${encodeURIComponent(reference)}?t=${encodeURIComponent(token)}`);
        if (!cancelled) setReport(data);
      } catch (err) {
        if (!cancelled) {
          setError(errMsg(err, "This link is not valid, or it has expired."));
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
    // Runs once for the handle present on arrival.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const history = [...(report?.history || [])].sort((a, b) => b.version - a.version);

  return (
    <main className="min-h-screen bg-slate-50">
      <section className="bg-[#050a14] text-white">
        <div className="mx-auto flex max-w-3xl items-center justify-between gap-4 px-6 py-6">
          <Link to="/" aria-label="ZoikoStream home"><Logo height="h-7" /></Link>
          <Link
            to="/trust"
            className="inline-flex items-center gap-2 rounded-xl border border-white/15 bg-white/[0.04] px-3.5 py-2 text-[13px] font-medium text-white/85 transition hover:border-white/30 hover:bg-white/[0.08]"
          >
            <ShieldCheck className="h-4 w-4" aria-hidden="true" />
            Trust Center
          </Link>
        </div>
        <div className="mx-auto max-w-3xl px-6 pb-10 pt-2">
          <p className="text-[11px] font-semibold uppercase tracking-[0.2em] text-teal-300">
            Security report
          </p>
          <h1 className="mt-3 flex items-start gap-3 text-[1.7rem] font-bold leading-tight tracking-tight sm:text-[2.1rem]">
            <Bug className="mt-1 h-6 w-6 shrink-0 text-white/80" aria-hidden="true" />
            {reference}
          </h1>
          <p className="mt-3 max-w-xl text-[14px] leading-relaxed text-white/60">
            The current status of the issue you reported. This link is private to you — please
            do not share it.
          </p>
        </div>
      </section>

      <div className="mx-auto max-w-3xl space-y-6 px-6 py-10">
        {loading && (
          <p className="flex items-center gap-2 text-[14px] text-slate-500">
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            Loading…
          </p>
        )}

        {error && (
          <div className="rounded-2xl border border-rose-200 bg-rose-50 px-5 py-4 text-[14px] leading-relaxed text-rose-900">
            <p>{error}</p>
            <p className="mt-2">
              If you need to reach us about this report, use the{" "}
              <Link to="/trust" className="font-semibold underline">Trust Center</Link>.
            </p>
          </div>
        )}

        {report && (
          <>
            <section className="rounded-2xl border border-slate-200 bg-white p-5 sm:p-6">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <h2 className="text-[16px] font-semibold text-slate-900">{report.title}</h2>
                  <p className="mt-1 text-[13px] text-slate-500">
                    {(report.category || "").replace(/_/g, " ")}
                  </p>
                </div>
                <span className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-semibold uppercase tracking-wide ${STAGE_TONE[report.status] || STAGE_TONE.received}`}>
                  {(report.status || "").replace(/_/g, " ")}
                </span>
              </div>

              <dl className="mt-4 space-y-2 text-[13px]">
                <div className="flex gap-3">
                  <dt className="w-40 shrink-0 font-semibold uppercase tracking-wide text-slate-500">Received</dt>
                  <dd className="text-slate-700"><Stamp iso={report.received_at} /></dd>
                </div>
                {report.acknowledged_at && (
                  <div className="flex gap-3">
                    <dt className="w-40 shrink-0 font-semibold uppercase tracking-wide text-slate-500">Acknowledged</dt>
                    <dd className="text-slate-700"><Stamp iso={report.acknowledged_at} /></dd>
                  </div>
                )}
                {report.remediated_at && (
                  <div className="flex gap-3">
                    <dt className="w-40 shrink-0 font-semibold uppercase tracking-wide text-slate-500">Remediated</dt>
                    <dd className="text-emerald-700"><Stamp iso={report.remediated_at} /></dd>
                  </div>
                )}
                {report.closed_at && (
                  <div className="flex gap-3">
                    <dt className="w-40 shrink-0 font-semibold uppercase tracking-wide text-slate-500">Closed</dt>
                    <dd className="text-slate-700"><Stamp iso={report.closed_at} /></dd>
                  </div>
                )}
                {report.resolution && (
                  <div className="flex gap-3">
                    <dt className="w-40 shrink-0 font-semibold uppercase tracking-wide text-slate-500">Outcome</dt>
                    <dd className="text-slate-700">{report.resolution.replace(/_/g, " ")}</dd>
                  </div>
                )}
                <div className="flex gap-3">
                  <dt className="w-40 shrink-0 font-semibold uppercase tracking-wide text-slate-500">
                    Disclosure timeline
                  </dt>
                  {/* Read from the API. With no coordination agreement recorded, the page
                      says so rather than implying an embargo or a date. */}
                  <dd className="text-slate-700">
                    {report.coordinated_disclosure_recorded
                      ? "Agreed with you directly — see the updates below."
                      : "No disclosure timeline has been set for this report."}
                  </dd>
                </div>
              </dl>
            </section>

            <section>
              <h2 className="flex items-center gap-2 text-[13px] font-semibold uppercase tracking-wide text-slate-500">
                <Clock className="h-4 w-4" aria-hidden="true" />
                Updates
              </h2>
              <ol className="mt-3 space-y-3 border-l border-slate-200 pl-4">
                {history.map((u) => (
                  <li key={u.version} className="relative">
                    <span aria-hidden="true" className="absolute -left-[21px] top-1.5 h-2 w-2 rounded-full bg-slate-300" />
                    <div className="flex flex-wrap items-center gap-2 text-[12px]">
                      <span className="font-semibold uppercase tracking-wide text-slate-500">
                        {(u.stage || "").replace(/_/g, " ")}
                      </span>
                      <span className="text-slate-400"><Stamp iso={u.published_at} /></span>
                    </div>
                    <p className="mt-1 whitespace-pre-line text-[13px] leading-relaxed text-slate-700">
                      {u.body}
                    </p>
                  </li>
                ))}
                {history.length === 0 && (
                  <li className="text-[13px] text-slate-500">
                    No updates yet. Our security team reviews every report.
                  </li>
                )}
              </ol>
            </section>

            <p className="rounded-2xl border border-slate-200 bg-white px-5 py-4 text-[13px] leading-relaxed text-slate-600">
              <CircleCheck className="mr-1.5 inline h-4 w-4 text-slate-400" aria-hidden="true" />
              Please keep the details of your report confidential while we investigate, and
              avoid accessing, changing or storing other people&rsquo;s data. Never send us
              passwords, API keys or private keys — we do not need them.
            </p>
          </>
        )}
      </div>
    </main>
  );
}
