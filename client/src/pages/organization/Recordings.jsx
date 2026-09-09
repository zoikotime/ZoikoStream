// client/src/pages/organization/Recordings.jsx
// Recordings — manage all recorded live events. Route: /organization/recordings
// Rendered inside OrganizationLayout (sidebar + topbar). Backed by GET/DELETE
// /organization/recordings — only rows LiveKit egress actually captured (see
// crud.event.list_org_recordings) ever appear here, so every card is a real, playable file.
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  FiSearch, FiFilter, FiChevronDown, FiPlay,
  FiHardDrive, FiDownload, FiShare2, FiTrash2, FiSend, FiCopy,
  FiFilm, FiDatabase, FiLock, FiClock,
} from "react-icons/fi";
import api, { errMsg } from "../../api";
import useApi from "../../hooks/useApi";
import { cx } from "../../ui/tokens";
import Card from "../../ui/Card";
import Button from "../../ui/Button";
import StatsCard from "../../ui/StatsCard";
import Badge from "../../ui/Badge";
import Modal from "../../ui/Modal";
import { Input, Label } from "../../ui/forms";
import { notify } from "../../ui/Toast";
import { fmtDate } from "../../data/events";

// Stable identity for the "nothing loaded yet" case. The useMemo hooks below take this list
// as a dependency, and a fresh `[]` literal on every render would defeat every one of them
// (permanently, for a response that simply omits the field). It is never mutated.
const NONE = [];

// Literal gradient per accent — Tailwind JIT can't compile interpolated names.
// (Same convention as the watch-page thumbnails.)
const THUMB = [
  "from-violet-600 to-slate-900",
  "from-emerald-600 to-slate-900",
  "from-blue-600 to-slate-900",
  "from-amber-500 to-slate-900",
  "from-indigo-600 to-slate-900",
  "from-rose-600 to-slate-900",
];
// Stable per-category color without a backend field — same category always lands on the
// same gradient across reloads.
const thumbFor = (category) => {
  const s = category || "";
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
  return THUMB[h % THUMB.length];
};

const fmtDuration = (secs) => {
  if (secs == null) return "—";
  const h = Math.floor(secs / 3600);
  const m = Math.floor((secs % 3600) / 60);
  const s = Math.round(secs % 60);
  return h > 0 ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`;
};
const fmtBytes = (bytes) => {
  if (!bytes) return "—";
  const gb = bytes / 1024 ** 3;
  return gb >= 1 ? `${gb.toFixed(1)} GB` : `${Math.round(bytes / 1024 ** 2)} MB`;
};
const fmtTotalBytes = (bytes) => {
  const gb = bytes / 1024 ** 3;
  return gb >= 1000 ? `${(gb / 1000).toFixed(2)} TB` : `${gb.toFixed(1)} GB`;
};

const control =
  "rounded-xl border border-slate-200 bg-white px-3.5 py-2 text-sm text-slate-700 shadow-sm outline-none focus:border-emerald-400 focus:ring-2 focus:ring-emerald-500/20 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200";

function ActionBtn({ icon: Icon, label, danger, disabled, onClick }) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      title={label}
      aria-label={label}
      className={cx(
        "inline-flex flex-1 items-center justify-center gap-1.5 rounded-lg border px-2 py-1.5 text-xs font-medium transition disabled:cursor-not-allowed disabled:opacity-40",
        danger
          ? "border-slate-200 text-slate-600 hover:border-rose-300 hover:bg-rose-50 hover:text-rose-600 dark:border-slate-700 dark:text-slate-300 dark:hover:border-rose-500/40 dark:hover:bg-rose-500/10 dark:hover:text-rose-400"
          : "border-slate-200 text-slate-600 hover:bg-slate-50 hover:text-slate-800 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800 dark:hover:text-slate-100"
      )}
    >
      <Icon className="text-sm" /> {label}
    </button>
  );
}

function exportStatus(d) {
  if (d.revoked_at) return { tone: "neutral", label: "Revoked" };
  if (d.expires_at && new Date(d.expires_at) < new Date()) return { tone: "warning", label: "Expired" };
  // Watermarking (BRD "policy watermark", LE-AC-12) runs on a background ticker — see
  // that state before falling through to "Active", so a still-processing export doesn't
  // read as already deliverable.
  if (d.watermark_status === "failed") return { tone: "danger", label: "Failed" };
  if (d.watermark_status === "pending") return { tone: "warning", label: "Preparing…" };
  return { tone: "success", label: "Active" };
}

// Controlled customer export — an authorized, audited, expiring, single-recipient
// delivery of the validated replay (BRD LE-AC-18). Opens on a history list (prior
// exports + revoke), with "New export" leading into the recipient form and then the
// one-time /deliveries/{token} link — same reveal-once posture as an API key.
function ExportModal({ recording, onClose }) {
  const { data, loading, reload } = useApi(() =>
    api.get(`/organization/recordings/${recording.id}/exports`).then((r) => r.data)
  );
  const history = data || [];
  const [view, setView] = useState("list"); // list | form | created
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [expires, setExpires] = useState("14");
  const [sending, setSending] = useState(false);
  const [created, setCreated] = useState(null);

  const send = async () => {
    setSending(true);
    try {
      const { data: d } = await api.post(`/organization/recordings/${recording.id}/exports`, {
        recipient_name: name.trim(), recipient_email: email.trim(),
        expires_in_days: expires ? Number(expires) : null,
      });
      setCreated(d);
      setView("created");
    } catch (e) {
      notify.error(errMsg(e));
    } finally {
      setSending(false);
    }
  };

  const revoke = async (d) => {
    if (!window.confirm(`Revoke the export sent to ${d.recipient_email}?`)) return;
    try {
      await api.delete(`/organization/recordings/${recording.id}/exports/${d.id}`);
      notify.success("Export revoked");
      reload();
    } catch (e) {
      notify.error(errMsg(e));
    }
  };

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(created.delivery_url);
      notify.success("Export link copied");
    } catch {
      notify.error("Couldn't copy — select the link and copy it manually");
    }
  };

  return (
    <Modal
      open={!!recording}
      onClose={onClose}
      title={view === "created" ? "Export sent" : view === "form" ? "Export recording" : "Recording exports"}
      size="md"
      footer={
        view === "created" ? (
          <>
            <Button size="sm" variant="secondary" onClick={onClose}>Done</Button>
            <Button size="sm" onClick={copy}><FiCopy /> Copy link</Button>
          </>
        ) : view === "form" ? (
          <>
            <Button size="sm" variant="secondary" onClick={() => setView("list")} disabled={sending}>Back</Button>
            <Button size="sm" onClick={send} disabled={!name.trim() || !email.trim() || sending}>
              <FiSend /> {sending ? "Sending…" : "Send export"}
            </Button>
          </>
        ) : (
          <>
            <Button size="sm" variant="secondary" onClick={onClose}>Close</Button>
            <Button size="sm" onClick={() => { setName(""); setEmail(""); setExpires("14"); setView("form"); }}>
              <FiSend /> New export
            </Button>
          </>
        )
      }
    >
      {view === "created" ? (
        <div className="space-y-3 text-sm text-slate-600 dark:text-slate-300">
          <p>
            An email was sent to <strong className="text-slate-800 dark:text-slate-100">{created.recipient_email}</strong>{" "}
            with this link. It won't be shown again after you close this dialog.
          </p>
          <p className="text-xs text-slate-500 dark:text-slate-400">
            The link works now, but the download itself won't be ready until the watermarked copy finishes
            processing — usually within a few minutes.
          </p>
          <code className="block break-all rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-xs dark:border-slate-700 dark:bg-white/[0.03]">
            {created.delivery_url}
          </code>
        </div>
      ) : view === "form" ? (
        <div className="space-y-4">
          <p className="text-sm text-slate-500 dark:text-slate-400">
            Generates an authorized, expiring, single-recipient download link — never a public link.
            The recipient is emailed automatically.
          </p>
          <div>
            <Label htmlFor="exp-name">Recipient name</Label>
            <Input id="exp-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="Family contact name" />
          </div>
          <div>
            <Label htmlFor="exp-email">Recipient email</Label>
            <Input id="exp-email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="name@example.com" />
          </div>
          <div>
            <Label htmlFor="exp-days">Expires in (days)</Label>
            <Input id="exp-days" type="number" min="1" max="365" value={expires} onChange={(e) => setExpires(e.target.value)} />
          </div>
        </div>
      ) : loading ? (
        <p className="text-sm text-slate-400">Loading…</p>
      ) : history.length === 0 ? (
        <p className="text-sm text-slate-500 dark:text-slate-400">
          No exports sent yet for this recording.
        </p>
      ) : (
        <ul className="divide-y divide-slate-100 dark:divide-slate-800">
          {history.map((d) => {
            const s = exportStatus(d);
            return (
              <li key={d.id} className="flex items-center justify-between gap-3 py-2.5 text-sm">
                <div className="min-w-0">
                  <p className="truncate font-medium text-slate-800 dark:text-slate-100">{d.recipient_name}</p>
                  <p className="truncate text-xs text-slate-500 dark:text-slate-400">{d.recipient_email}</p>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <Badge tone={s.tone} size="sm" dot>{s.label}</Badge>
                  {(s.label === "Active" || s.label === "Preparing…") && (
                    <button
                      type="button"
                      onClick={() => revoke(d)}
                      aria-label={`Revoke export to ${d.recipient_email}`}
                      title="Revoke"
                      className="rounded-md p-1.5 text-slate-400 transition hover:bg-rose-50 hover:text-rose-600 dark:hover:bg-rose-500/10 dark:hover:text-rose-400"
                    >
                      <FiTrash2 className="text-sm" />
                    </button>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </Modal>
  );
}

function RecordingCard({ r, onDownload, onShare, onDelete, onExport }) {
  const watchHref = `/events/${r.event_id}/watch`;
  return (
    <Card padding="none" hover className="flex flex-col overflow-hidden">
      {/* Thumbnail */}
      <div className={cx("group relative grid aspect-video place-items-center bg-gradient-to-br", thumbFor(r.category))}>
        <Link
          to={watchHref}
          aria-label={`Watch ${r.title || "recording"}`}
          className="grid h-12 w-12 place-items-center rounded-full bg-white/15 text-white backdrop-blur transition hover:scale-110 hover:bg-white/25"
        >
          <FiPlay className="ml-0.5 text-xl" />
        </Link>
        <span className="absolute bottom-2 right-2 rounded bg-black/60 px-1.5 py-0.5 text-[11px] font-medium text-white">
          {fmtDuration(r.duration_seconds)}
        </span>
        {r.category && (
          <span className="absolute left-2 top-2 rounded bg-black/40 px-1.5 py-0.5 text-[11px] font-medium text-white backdrop-blur">
            {r.category}
          </span>
        )}
      </div>

      {/* Body */}
      <div className="flex flex-1 flex-col p-4">
        <p className="truncate font-semibold text-slate-800 dark:text-slate-100" title={r.title || "Untitled event"}>
          {r.title || "Untitled event"}
        </p>
        <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">
          {r.started_at ? fmtDate(r.started_at) : "—"}
        </p>

        <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-slate-500 dark:text-slate-400">
          <span className="inline-flex items-center gap-1"><FiHardDrive /> {fmtBytes(r.size_bytes)}</span>
          {r.legal_hold && (
            <Badge tone="danger" size="sm"><FiLock className="text-[10px]" /> Legal hold</Badge>
          )}
          {r.validation_status === "valid" ? (
            <Badge tone="success" size="sm">Validated</Badge>
          ) : (
            <Badge tone="warning" size="sm"><FiClock className="text-[10px]" /> Validation pending</Badge>
          )}
        </div>

        {/* Actions */}
        <div className="mt-4 space-y-2 border-t border-slate-100 pt-3 dark:border-slate-800">
          <Button size="sm" href={watchHref} className="w-full">
            <FiPlay className="text-base" /> Watch Replay
          </Button>
          <div className="flex items-center gap-1.5">
            <ActionBtn icon={FiDownload} label="Download" disabled={!r.url} onClick={() => onDownload(r)} />
            <ActionBtn icon={FiShare2} label="Share" onClick={() => onShare(r)} />
          </div>
          <div className="flex items-center gap-1.5">
            <ActionBtn
              icon={FiSend} label="Export" disabled={r.legal_hold}
              onClick={() => onExport(r)}
            />
            <ActionBtn icon={FiTrash2} label="Delete" danger disabled={r.legal_hold} onClick={() => onDelete(r)} />
          </div>
        </div>
      </div>
    </Card>
  );
}

export default function OrganizationRecordings() {
  const { data, loading, error, reload } = useApi(() =>
    api.get("/organization/recordings").then((r) => r.data)
  );
  const list = data || NONE;
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("All");
  const [sort, setSort] = useState("date-desc");
  const [exporting, setExporting] = useState(null);

  const categories = useMemo(
    () => ["All", ...new Set(list.map((r) => r.category).filter(Boolean))],
    [list]
  );

  const totalBytes = useMemo(() => list.reduce((s, r) => s + (r.size_bytes || 0), 0), [list]);

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    const out = list.filter(
      (r) => (category === "All" || r.category === category) && (!q || (r.title || "").toLowerCase().includes(q))
    );
    const sorters = {
      "date-desc": (a, b) => (b.started_at || "").localeCompare(a.started_at || ""),
      "date-asc": (a, b) => (a.started_at || "").localeCompare(b.started_at || ""),
      "size-desc": (a, b) => (b.size_bytes || 0) - (a.size_bytes || 0),
      "name-asc": (a, b) => (a.title || "").localeCompare(b.title || ""),
    };
    return [...out].sort(sorters[sort]);
  }, [list, query, category, sort]);

  const onDownload = (r) => {
    if (!r.url) return;
    window.open(r.url, "_blank", "noopener");
  };
  const onShare = (r) => {
    const url = `${window.location.origin}/events/${r.event_id}/watch`;
    navigator.clipboard?.writeText(url);
    notify.success("Replay link copied to clipboard");
  };
  const onDelete = async (r) => {
    if (!window.confirm(`Delete the recording “${r.title || "this recording"}”? This can't be undone.`)) return;
    try {
      await api.delete(`/organization/recordings/${r.id}`);
      notify.success("Recording deleted");
      reload();
    } catch (e) {
      notify.error(errMsg(e));
    }
  };
  const onExport = (r) => setExporting(r);

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-slate-900 dark:text-white">Recordings</h1>
          <p className="text-sm text-slate-500 dark:text-slate-400">Manage all recorded live events for your organization</p>
        </div>

        <div className="flex flex-wrap items-center gap-2.5">
          <div className="relative">
            <FiSearch className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
            <input
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search recordings..."
              className={cx(control, "w-full pl-9 sm:w-56")}
            />
          </div>

          {/* Filter by category */}
          <div className="relative">
            <FiFilter className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
            <select
              value={category}
              onChange={(e) => setCategory(e.target.value)}
              aria-label="Filter by category"
              className={cx(control, "appearance-none pl-9 pr-8")}
            >
              {categories.map((c) => (
                <option key={c} value={c}>{c === "All" ? "All categories" : c}</option>
              ))}
            </select>
            <FiChevronDown className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
          </div>

          {/* Sort */}
          <div className="relative">
            <select
              value={sort}
              onChange={(e) => setSort(e.target.value)}
              aria-label="Sort recordings"
              className={cx(control, "appearance-none pr-8")}
            >
              <option value="date-desc">Newest first</option>
              <option value="date-asc">Oldest first</option>
              <option value="size-desc">Largest size</option>
              <option value="name-asc">Name (A–Z)</option>
            </select>
            <FiChevronDown className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
          </div>
        </div>
      </div>

      {/* Statistics cards — only what's real: no view/watch-time tracking exists yet */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <StatsCard title="Total Recordings" value={list.length} icon={FiFilm} accent="emerald" />
        <StatsCard title="Total Storage" value={fmtTotalBytes(totalBytes)} icon={FiDatabase} accent="blue" />
      </div>

      {/* Recording cards */}
      {error ? (
        <Card className="py-16 text-center">
          <p className="text-sm text-rose-500">Couldn't load recordings. Try refreshing the page.</p>
        </Card>
      ) : !loading && shown.length === 0 ? (
        <Card className="py-16 text-center">
          <p className="text-sm text-slate-400">
            {list.length === 0
              ? "No recordings yet — they'll show up here once a host records a live event."
              : "No recordings match your filters."}
          </p>
        </Card>
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {shown.map((r) => (
            <RecordingCard key={r.id} r={r} onDownload={onDownload} onShare={onShare} onDelete={onDelete} onExport={onExport} />
          ))}
        </div>
      )}

      {exporting && <ExportModal recording={exporting} onClose={() => setExporting(null)} />}
    </div>
  );
}
