import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { FiDownload, FiAlertCircle, FiClock, FiUsers, FiActivity, FiFilm, FiLoader } from "react-icons/fi";
import Card from "../ui/Card";
import Spinner from "../ui/Spinner";
import Button from "../ui/Button";
import { notify } from "../ui/Toast";
import api, { errMsg } from "../api";
import useInterval from "../hooks/useInterval";

// Controlled customer export / post-event report — reached from the email
// services/delivery.py and services/report.py send (BRD LE-AC-18). The recipient is
// never a platform user: no auth, no org-console chrome, not indexable.
export default function CustomerDelivery() {
  const { token } = useParams();
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [downloading, setDownloading] = useState(false);

  const fetchDelivery = () => {
    api
      .get(`/deliveries/${token}`)
      .then(({ data: d }) => setData(d))
      .catch((e) => setError(errMsg(e, "This link is invalid, expired, or has been revoked.")))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    fetchDelivery();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  // While the watermark is still processing (services/delivery.py's background ticker,
  // BRD "policy watermark" LE-AC-12), re-check every 15s so the page flips to the
  // download button on its own — the recipient shouldn't have to know to refresh.
  useInterval(fetchDelivery, 15000, data?.kind === "export" && data?.watermark_status === "pending");

  // Not indexable — a leaked link showing up in search results defeats the point of a
  // controlled, single-recipient delivery. Same approach as EventWatch.jsx's own noindex
  // tag (no SSR here, so a robots meta tag is the SPA-native equivalent).
  useEffect(() => {
    const meta = document.createElement("meta");
    meta.name = "robots";
    meta.content = "noindex, nofollow";
    document.head.appendChild(meta);
    return () => meta.remove();
  }, []);

  const download = async () => {
    setDownloading(true);
    try {
      const { data: d } = await api.post(`/deliveries/${token}/download`);
      window.location.href = d.download_url;
    } catch (e) {
      notify.error(errMsg(e));
    } finally {
      setDownloading(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-50 px-4 py-12 dark:bg-slate-950">
      <div className="w-full max-w-lg">
        {loading ? (
          <div className="flex justify-center py-16"><Spinner /></div>
        ) : error ? (
          <Card className="p-8 text-center">
            <FiAlertCircle className="mx-auto mb-3 text-3xl text-rose-500" />
            <h1 className="text-lg font-semibold text-slate-900 dark:text-white">Link unavailable</h1>
            <p className="mt-1.5 text-sm text-slate-500 dark:text-slate-400">{error}</p>
          </Card>
        ) : data.kind === "export" && data.watermark_status === "failed" ? (
          <Card className="p-8 text-center">
            <FiAlertCircle className="mx-auto mb-3 text-3xl text-rose-500" />
            <h1 className="text-lg font-semibold text-slate-900 dark:text-white">Couldn't prepare this recording</h1>
            <p className="mt-1.5 text-sm text-slate-500 dark:text-slate-400">
              Something went wrong preparing {data.event_title}. Contact the organizer for a new link.
            </p>
          </Card>
        ) : data.kind === "export" && data.watermark_status === "pending" ? (
          <Card className="p-8 text-center">
            <FiLoader className="mx-auto mb-3 animate-spin text-3xl text-slate-400" />
            <h1 className="text-lg font-semibold text-slate-900 dark:text-white">Preparing your download…</h1>
            <p className="mt-1.5 text-sm text-slate-500 dark:text-slate-400">
              {data.event_title} is being watermarked for you — this usually takes a few minutes.
              This page will update automatically.
            </p>
          </Card>
        ) : data.kind === "export" ? (
          <Card className="p-8 text-center">
            <FiFilm className="mx-auto mb-3 text-3xl text-emerald-500" />
            <h1 className="text-lg font-semibold text-slate-900 dark:text-white">Your recording is ready</h1>
            <p className="mt-1.5 text-sm text-slate-500 dark:text-slate-400">{data.event_title}</p>
            {data.expires_at && (
              <p className="mt-3 inline-flex items-center gap-1.5 text-xs text-slate-400 dark:text-slate-500">
                <FiClock /> Available until {new Date(data.expires_at).toLocaleDateString()}
              </p>
            )}
            <Button className="mt-6 w-full" onClick={download} disabled={downloading}>
              <FiDownload /> {downloading ? "Preparing…" : "Download recording"}
            </Button>
          </Card>
        ) : (
          <EventReportView data={data} />
        )}
      </div>
    </div>
  );
}

function Stat({ label, value }) {
  return (
    <div className="rounded-lg border border-slate-200 px-3 py-2.5 dark:border-slate-800">
      <p className="text-[11px] uppercase tracking-wide text-slate-400 dark:text-slate-500">{label}</p>
      <p className="mt-0.5 text-base font-semibold text-slate-800 dark:text-slate-100">{value ?? "—"}</p>
    </div>
  );
}

function EventReportView({ data }) {
  const r = data.report || {};
  const audience = r.audience || {};
  const operations = r.operations || {};
  return (
    <Card className="p-6">
      <h1 className="text-lg font-semibold text-slate-900 dark:text-white">{data.event_title}</h1>
      <p className="mt-0.5 text-xs text-slate-400 dark:text-slate-500">Event report</p>

      <h2 className="mt-5 flex items-center gap-1.5 text-sm font-semibold text-slate-700 dark:text-slate-200">
        <FiUsers /> Audience
      </h2>
      <div className="mt-2 grid grid-cols-2 gap-2.5">
        <Stat label="Peak viewers" value={audience.peak_viewers} />
        <Stat label="Watch hours" value={audience.watch_hours} />
        <Stat label="Registrations" value={audience.total_registrations} />
        <Stat label="Show rate" value={audience.show_rate != null ? `${audience.show_rate}%` : null} />
      </div>

      <h2 className="mt-5 flex items-center gap-1.5 text-sm font-semibold text-slate-700 dark:text-slate-200">
        <FiActivity /> Operations
      </h2>
      <div className="mt-2 space-y-1.5 text-sm text-slate-600 dark:text-slate-300">
        <p>Host: {operations.hosts?.length ? operations.hosts.join(", ") : "—"}</p>
        {/* Only rendered when the report actually names moderators. The role is retired, so
            no event run after the retirement can have any — but a report generated for an
            event that DID is a compliance record of who held console access, and dropping the
            line would under-report it. Conditional rather than "—": a row that is empty for
            every current event is noise on a customer-facing page. */}
        {operations.moderators?.length ? (
          <p>Moderators: {operations.moderators.join(", ")}</p>
        ) : null}
      </div>

      {(r.recordings || []).length > 0 && (
        <>
          <h2 className="mt-5 flex items-center gap-1.5 text-sm font-semibold text-slate-700 dark:text-slate-200">
            <FiFilm /> Recordings
          </h2>
          <ul className="mt-2 space-y-1 text-sm text-slate-600 dark:text-slate-300">
            {r.recordings.map((rec, i) => (
              <li key={i}>{rec.role || "recording"} — {rec.status}{rec.legal_hold ? " (legal hold)" : ""}</li>
            ))}
          </ul>
        </>
      )}

      {r.meta?.generated_at && (
        <p className="mt-6 border-t border-slate-100 pt-3 text-[11px] text-slate-400 dark:border-slate-800 dark:text-slate-500">
          Generated {new Date(r.meta.generated_at).toLocaleString()}
        </p>
      )}
    </Card>
  );
}
