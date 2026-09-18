import { useCallback, useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { FiArrowRight, FiCheckCircle, FiDownload, FiExternalLink, FiFileText, FiShield } from "react-icons/fi";
import api, { errMsg } from "../../api";
import { Logo } from "../../ui";

// /organization/privacy — the customer Privacy Center (PRV-001 -> PRV-004).
//
// Every privacy email the platform sends routes here: the received/verify/status links
// (PRV-001/002), the export download (PRV-003), the notice and subprocessor lists
// (PRV-004). Until now those emails linked to a page that did not exist.
//
// THE PAGE IS THE SAME SURFACE FOR EVERYONE. A requester frequently has no account —
// the service layer was written for that on purpose — so this page never assumes a
// session and never blocks on one. The reference is the credential for status, exactly
// as it is in the emails.
//
// WHAT IS DELIBERATELY NOT HERE: any way to read another request, any display of the
// request's own free-text details, and any operator action (approve, extend, deny,
// execute deletion). Those are staff-side on purpose — see routers/privacy.py's header.

const REQUEST_TYPES = [
  ["access", "Access to your personal data"],
  ["export", "A copy of your personal data"],
  ["deletion", "Deletion of your personal data"],
  ["correction", "Correction of your personal data"],
  ["restriction", "Restriction of processing"],
  ["objection", "Objection to processing"],
  ["other", "Other privacy request"],
];

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

// One shell for the whole center. Public and unauthenticated BY DESIGN — see the header
// comment — so it renders its own chrome instead of sitting in OrganizationLayout, whose
// shell requires a live session to draw anything at all.
function Shell({ title, lede, children }) {
  return (
    <main className="min-h-screen bg-slate-50">
      <section className="bg-[#050a14] text-white">
        <div className="mx-auto flex max-w-3xl items-center justify-between px-6 py-6 lg:px-8">
          <Link to="/" aria-label="ZoikoStream home"><Logo height="h-7" /></Link>
          <div className="flex items-center gap-2">
            <Link
              to="/privacy"
              className="rounded-xl border border-white/15 bg-white/[0.04] px-3.5 py-2 text-[13px] font-medium text-white/85 transition hover:border-white/30 hover:bg-white/[0.08]"
            >
              Privacy policy
            </Link>
            <Link
              to="/trust"
              className="rounded-xl border border-white/15 bg-white/[0.04] px-3.5 py-2 text-[13px] font-medium text-white/85 transition hover:border-white/30 hover:bg-white/[0.08]"
            >
              Trust Center
            </Link>
          </div>
        </div>
        <div className="mx-auto max-w-3xl px-6 pb-12 pt-2 lg:px-8">
          <p className="text-[11px] font-semibold uppercase tracking-[0.2em] text-teal-300">
            Privacy Center
          </p>
          <h1 className="mt-3 flex items-start gap-3 text-[1.9rem] font-bold leading-tight tracking-tight sm:text-[2.2rem]">
            <FiShield className="mt-1 h-6 w-6 shrink-0 text-white/80" aria-hidden="true" />
            {title}
          </h1>
          {lede && <p className="mt-3 max-w-xl text-[14px] leading-relaxed text-white/60">{lede}</p>}
        </div>
      </section>
      <div className="mx-auto max-w-3xl space-y-6 px-6 py-10 lg:px-8">{children}</div>
    </main>
  );
}

function Card({ children, className = "" }) {
  return (
    <section className={`rounded-2xl border border-slate-200 bg-white p-5 sm:p-6 ${className}`}>
      {children}
    </section>
  );
}

// ── the three states a requester can be sent back in ────────────────────────────────────

function Verify() {
  // The emailed link is /organization/privacy/requests/:id/verify?t=... — the token is
  // spent by POSTing it; nothing secret ever needs to be typed.
  const [params] = useSearchParams();
  const token = params.get("t");
  const [state, setState] = useState(token ? "working" : "idle");
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);

  const redeem = useCallback(async (value) => {
    setState("working");
    setError(null);
    try {
      const { data } = await api.post("/privacy/requests/verify", { token: value });
      setResult(data);
      setState("done");
    } catch (err) {
      setError(errMsg(err));
      setState("failed");
    }
  }, []);

  useEffect(() => {
    // The token arrives in the URL, not from user input, so the redemption is an effect
    // with an async body — the setState calls it makes are all inside the promise chain,
    // not synchronous in the effect body itself.
    if (!token) return undefined;
    let cancelled = false;
    (async () => {
      if (cancelled) return;
      await redeem(token);
    })();
    return () => { cancelled = true; };
  }, [token, redeem]);

  if (!token && state === "idle") {
    return (
      <Card>
        <h2 className="text-[17px] font-semibold text-slate-900">Verify a request</h2>
        <p className="mt-2 text-[14px] leading-relaxed text-slate-600">
          Open the verification link from your email. It confirms you control the address
          the request was made from — we ask this even when you are signed in, because
          export and deletion are disclosive or irreversible.
        </p>
      </Card>
    );
  }

  return (
    <Card>
      <h2 className="text-[17px] font-semibold text-slate-900">Verify a request</h2>
      {state === "working" && (
        <p className="mt-2 text-[14px] text-slate-500">Confirming your request…</p>
      )}
      {state === "done" && result && (
        <div className="mt-3 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-[14px] leading-relaxed text-emerald-900">
          <p className="flex items-center gap-2 font-semibold">
            <FiCheckCircle aria-hidden="true" /> Request {result.reference} verified.
          </p>
          <p className="mt-1">
            We can now act on it. You can follow its progress below or from any of the
            emails we send about it.
          </p>
        </div>
      )}
      {state === "failed" && (
        <div className="mt-3 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-[14px] leading-relaxed text-rose-900">
          {error || "This link is not valid or has expired."}
          <p className="mt-2 text-[13px] text-rose-700">
            Request a fresh one from the form above — a new verification link will be
            emailed to the same address.
          </p>
        </div>
      )}
    </Card>
  );
}

function RequestStatus() {
  const [reference, setReference] = useState("");
  const [busy, setBusy] = useState(false);
  const [found, setFound] = useState(null);
  const [error, setError] = useState(null);

  const lookup = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setFound(null);
    try {
      const { data } = await api.get(
        `/privacy/requests/${encodeURIComponent(reference.trim())}`);
      setFound(data);
    } catch (err) {
      setError(errMsg(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card>
      <h2 className="text-[17px] font-semibold text-slate-900">Check a request</h2>
      <p className="mt-2 text-[14px] leading-relaxed text-slate-600">
        Enter the reference from your acknowledgement email (for example
        PRV-2026-000123). The status shown is the same one your emails carry.
      </p>
      <form onSubmit={lookup} className="mt-4 flex flex-col gap-3 sm:flex-row">
        <input
          required
          value={reference}
          onChange={(e) => setReference(e.target.value)}
          placeholder="PRV-2026-000123"
          aria-label="Request reference"
          className="w-full rounded-xl border border-slate-200 px-3.5 py-2.5 text-[14px] text-slate-900 outline-none placeholder:text-slate-400 focus:border-slate-400"
        />
        <button
          type="submit" disabled={busy}
          className="shrink-0 rounded-xl bg-slate-900 px-4 py-2.5 text-[14px] font-semibold text-white transition hover:bg-slate-800 disabled:opacity-60"
        >
          {busy ? "Checking…" : "Check status"}
        </button>
      </form>

      {found && (
        <dl className="mt-4 grid gap-2 rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-[13px] sm:grid-cols-2">
          <div className="flex gap-2">
            <dt className="font-semibold text-slate-700">Reference:</dt>
            <dd className="font-mono text-slate-600">{found.reference}</dd>
          </div>
          <div className="flex gap-2">
            <dt className="font-semibold text-slate-700">Type:</dt>
            <dd className="text-slate-600">
              {REQUEST_TYPES.find(([v]) => v === found.request_type)?.[1] || found.request_type}
            </dd>
          </div>
          <div className="flex gap-2 sm:col-span-2">
            <dt className="font-semibold text-slate-700">Status:</dt>
            <dd className="text-slate-600">{found.status.replace(/_/g, " ")}</dd>
          </div>
          <div className="flex gap-2 sm:col-span-2">
            <dt className="font-semibold text-slate-700">Timing:</dt>
            <dd className="text-slate-600">{found.deadline_note}</dd>
          </div>
        </dl>
      )}
      {error && (
        <p className="mt-4 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-[13px] leading-relaxed text-rose-900">
          {error}
        </p>
      )}
    </Card>
  );
}

// ── intake ──────────────────────────────────────────────────────────────────────────────

function NewRequest() {
  const [form, setForm] = useState({ email: "", request_type: "export", details: "" });
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);

  const set = (key) => (e) => setForm((f) => ({ ...f, [key]: e.target.value }));

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const { data } = await api.post("/privacy/requests", {
        email: form.email, request_type: form.request_type,
        details: form.details.trim() || undefined,
      });
      setResult(data);
      setForm({ email: "", request_type: "export", details: "" });
    } catch (err) {
      setError(errMsg(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card>
      <h2 className="text-[17px] font-semibold text-slate-900">Make a request</h2>
      <p className="mt-2 text-[14px] leading-relaxed text-slate-600">
        Choose the right that matches what you are asking for. If you already have an
        open request of the same kind, we will show you that one instead of opening a
        second.
      </p>
      <form onSubmit={submit} className="mt-4 grid gap-4">
        <label className="block">
          <span className="text-[12px] font-semibold uppercase tracking-wide text-slate-500">
            Your email
          </span>
          <input
            type="email" required value={form.email} onChange={set("email")}
            placeholder="you@example.com"
            className="mt-2 w-full rounded-xl border border-slate-200 px-3.5 py-2.5 text-[14px] text-slate-900 outline-none placeholder:text-slate-400 focus:border-slate-400"
          />
        </label>
        <label className="block">
          <span className="text-[12px] font-semibold uppercase tracking-wide text-slate-500">
            What you are asking for
          </span>
          <select
            value={form.request_type} onChange={set("request_type")}
            className="mt-2 w-full rounded-xl border border-slate-200 px-3 py-2.5 text-[14px] text-slate-900 outline-none focus:border-slate-400"
          >
            {REQUEST_TYPES.map(([value, label]) => (
              <option key={value} value={value}>{label}</option>
            ))}
          </select>
        </label>
        <label className="block">
          <span className="text-[12px] font-semibold uppercase tracking-wide text-slate-500">
            Anything we should know (optional)
          </span>
          <textarea
            rows={3} value={form.details} onChange={set("details")}
            maxLength={4000}
            placeholder="For example, which part of your data the request concerns."
            className="mt-2 w-full rounded-xl border border-slate-200 px-3.5 py-2.5 text-[14px] text-slate-900 outline-none placeholder:text-slate-400 focus:border-slate-400"
          />
        </label>
        <div>
          <button
            type="submit" disabled={busy}
            className="inline-flex items-center gap-2 rounded-xl bg-slate-900 px-4 py-2.5 text-[14px] font-semibold text-white transition hover:bg-slate-800 disabled:opacity-60"
          >
            Submit request <FiArrowRight aria-hidden="true" />
          </button>
        </div>
      </form>

      {result && (
        <div className="mt-4 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-[14px] leading-relaxed text-emerald-900">
          <p>
            <strong className="font-semibold">Received — reference {result.reference}. </strong>
            We have emailed you an acknowledgement. Keep the reference: it is how you
            check status without an account.
          </p>
          {result.status === "verification_required" && (
            <p className="mt-2">
              Watch for a second email asking you to verify the request — export and
              deletion requests are actioned only after that step.
            </p>
          )}
        </div>
      )}
      {error && (
        <p className="mt-4 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-[13px] leading-relaxed text-rose-900">
          {error}
        </p>
      )}
    </Card>
  );
}

// ── export download (PRV-003) ───────────────────────────────────────────────────────────

function ExportDownload() {
  // The email lands here: /organization/privacy/exports/:exportId/download?t=...
  const { exportId } = useParams();
  const [params] = useSearchParams();
  const token = params.get("t");
  const [state, setState] = useState(token ? "working" : "idle");
  const [url, setUrl] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!token) return undefined;
    let cancelled = false;
    (async () => {
      try {
        // axios with a raw token in the query string; the download URL it returns is
        // short-lived and single-use, so fetch it as a blob and hand the object URL to
        // the anchor below. No other copy of the artifact exists in the page.
        const { data } = await api.get(
          `/privacy/exports/${encodeURIComponent(exportId)}/download`,
          { params: { t: token } });
        if (cancelled) return;
        // Two possible successes: a signed-URL payload (GCS) or the artifact body
        // itself (private-storage fallback). Handle both without assuming either.
        if (data && typeof data === "object" && data.download_url) {
          window.location.assign(data.download_url);
          setState("done");
        } else {
          const blob = new Blob([JSON.stringify(data, null, 2)],
                                { type: "application/json" });
          setUrl(URL.createObjectURL(blob));
          setState("done");
        }
      } catch (err) {
        if (!cancelled) {
          setError(errMsg(err));
          setState("failed");
        }
      }
    })();
    return () => { cancelled = true; };
  }, [exportId, token]);

  if (!token) {
    return (
      <Card>
        <h2 className="text-[17px] font-semibold text-slate-900">Download an export</h2>
        <p className="mt-2 text-[14px] leading-relaxed text-slate-600">
          Open the download link from your "Your export is ready" email. Links are
          single-use and expire after an hour — request a fresh export from the form
          above if yours has expired.
        </p>
      </Card>
    );
  }

  return (
    <Card>
      <h2 className="text-[17px] font-semibold text-slate-900">Download an export</h2>
      {state === "working" && (
        <p className="mt-2 text-[14px] text-slate-500">Preparing your download…</p>
      )}
      {state === "done" && (
        <div className="mt-3 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-[14px] leading-relaxed text-emerald-900">
          <p className="flex items-center gap-2 font-semibold">
            <FiCheckCircle aria-hidden="true" /> Your export is downloading.
          </p>
          {url && (
            <a
              href={url} download="zoikostream-privacy-export.json"
              className="mt-3 inline-flex items-center gap-2 rounded-xl bg-slate-900 px-4 py-2.5 text-[13px] font-semibold text-white transition hover:bg-slate-800"
            >
              <FiDownload aria-hidden="true" /> If it did not start, click here
            </a>
          )}
        </div>
      )}
      {state === "failed" && (
        <div className="mt-3 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-[14px] leading-relaxed text-rose-900">
          {error || "This link is not valid or has expired."}
        </div>
      )}
    </Card>
  );
}

// ── the published notice + subprocessors (PRV-004) ──────────────────────────────────────

function Notice() {
  const [notice, setNotice] = useState(null);
  const [processors, setProcessors] = useState(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [n, s] = await Promise.allSettled([
          api.get("/privacy/notice"), api.get("/privacy/subprocessors"),
        ]);
        if (cancelled) return;
        if (n.status === "fulfilled") setNotice(n.value.data);
        setProcessors(s.status === "fulfilled" ? s.value.data : []);
        setFailed(n.status === "rejected" && s.status === "rejected");
      } catch {
        if (!cancelled) setFailed(true);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  return (
    <Card>
      <h2 className="text-[17px] font-semibold text-slate-900">Privacy notice</h2>
      {failed && (
        <p className="mt-2 text-[14px] text-slate-500">
          The notice could not be loaded just now. The full text is also published at{" "}
          <Link to="/privacy" className="font-medium text-slate-700 underline">
            our privacy policy
          </Link>.
        </p>
      )}
      {!failed && !notice && (
        <p className="mt-2 text-[14px] leading-relaxed text-slate-600">
          No versioned notice has been published yet. The standing policy is published at{" "}
          <Link to="/privacy" className="font-medium text-slate-700 underline">
            our privacy policy
          </Link>.
        </p>
      )}
      {notice && (
        <div className="mt-3 space-y-2 text-[14px] leading-relaxed text-slate-700">
          <p>
            <span className="font-semibold">Version {notice.version}</span>
            {notice.effective_at && <> — effective {fmt(notice.effective_at)}</>}
          </p>
          {notice.change_summary && <p>{notice.change_summary}</p>}
          {notice.consent_required && (
            <p className="rounded-xl border border-amber-200 bg-amber-50 px-3.5 py-2.5 text-[13px] text-amber-900">
              This version asks for your consent: {notice.consent_purpose}. The decision
              is yours — accept and decline carry equal weight and neither is
              preselected. Record it when the notice asks you to, not from this summary.
            </p>
          )}
        </div>
      )}

      <h3 className="mt-6 text-[15px] font-semibold text-slate-900">Processors we use</h3>
      <ul className="mt-2 divide-y divide-slate-100 overflow-hidden rounded-xl border border-slate-200">
        {(processors || []).map((p) => (
          <li key={`${p.name}-${p.service}`} className="flex flex-wrap items-center justify-between gap-2 px-4 py-3">
            <div className="min-w-0">
              <p className="text-[14px] font-medium text-slate-800">{p.name}</p>
              <p className="text-[12px] text-slate-500">{p.processing_purpose}</p>
            </div>
            <span className="rounded-full border border-slate-200 bg-slate-50 px-2.5 py-1 text-[11px] font-semibold uppercase tracking-wide text-slate-600">
              {p.service}
            </span>
          </li>
        ))}
        {processors && processors.length === 0 && (
          <li className="px-4 py-6 text-center text-[13px] text-slate-500">
            No processors are published yet.
          </li>
        )}
      </ul>
      <p className="mt-3 flex items-center gap-1.5 text-[12px] text-slate-500">
        <FiFileText aria-hidden="true" />
        The authoritative versioned list; updated when a change requires notice.
        <FiExternalLink aria-hidden="true" className="hidden" />
      </p>
    </Card>
  );
}

// ── the page: which panel shows is decided by the URL, not by tabs ──────────────────────

export default function PrivacyCenter() {
  const { exportId } = useParams();
  const [params] = useSearchParams();
  const isVerify = params.get("t") !== null && window.location.pathname.endsWith("/verify");
  const isDownload = Boolean(exportId) && window.location.pathname.includes("/download");

  return (
    <Shell
      title={isDownload ? "Your privacy export" : "Your privacy, in your hands."}
      lede={
        isDownload
          ? "Single-use link, resolved once. If it has expired, request a fresh export below."
          : "Request a copy of your data, ask for a correction or deletion, and follow every request you have made — with or without an account."
      }
    >
      {isDownload && <ExportDownload key="download" />}
      {isVerify && <Verify key="verify" />}
      {!isVerify && !isDownload && (
        <>
          <NewRequest />
          <RequestStatus />
          <Notice />
        </>
      )}
    </Shell>
  );
}
