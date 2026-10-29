// client/src/pages/organization/Recordings.jsx
// Media & recordings (ORG-04) — the org-wide recording library. Route: /organization/recordings
// Rendered inside OrganizationLayout (sidebar + topbar). Backed by GET/DELETE
// /organization/recordings — only rows LiveKit egress actually captured (see
// crud.event.list_org_recordings) appear in the library; /organization/recordings/summary says
// what happened to every other attempt (running, or failed to capture) so an empty library is
// never mistaken for "nothing was attempted".
//
// Filters (search, state, category, sort) live in the URL, so a refresh or a shared link keeps
// them and the rail's "Replay-ready" / "Processing status" children are real destinations.
// There are no thumbnails: egress produces no poster frame, so each asset shows its category's
// colour rather than an image that does not exist.
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import {
  FiAlertTriangle, FiCheck, FiChevronRight, FiClock, FiCopy, FiDownload, FiEyeOff, FiFilm,
  FiFolder, FiInfo, FiLock, FiPlay, FiRefreshCw, FiSearch, FiSend, FiShare2, FiShield, FiTrash2,
} from "react-icons/fi";
import api, { errMsg } from "../../api";
import useApi from "../../hooks/useApi";
import { CONSOLE, consoleAction, cx, focusRing } from "../../ui/tokens";
import Button, { ConsoleButton } from "../../ui/Button";
import Badge from "../../ui/Badge";
import Dropdown from "../../ui/Dropdown";
import Modal from "../../ui/Modal";
import { Input, Label } from "../../ui/forms";
import { notify } from "../../ui/Toast";
import { fmtDate } from "../../data/events";
import { recordingState } from "../../data/recordingState";
import OrganizationPageHeader from "../../components/organization/OrganizationPageHeader";
import {
  Banner, DarkCard, Footnote, IconChip, KpiRow, KpiTile, Panel, Tag,
} from "../../components/organization/console/ConsoleUI";
import { ALL_CATEGORIES, categoryOptions, matchesCategory } from "./recordingCategories";

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
// Unknown (null) is a dash. A MEASURED zero is "0 B", never hidden behind the same dash:
// a zero-byte file is a capture that produced nothing, which is worth seeing. `!bytes` used
// to treat both as unknown.
const fmtBytes = (bytes) => {
  if (bytes == null) return "—";
  if (bytes === 0) return "0 B";
  if (bytes < 1024 ** 2) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  const gb = bytes / 1024 ** 3;
  return gb >= 1 ? `${gb.toFixed(1)} GB` : `${Math.round(bytes / 1024 ** 2)} MB`;
};
const fmtTotalBytes = (bytes) => {
  const gb = bytes / 1024 ** 3;
  if (gb >= 1000) return `${(gb / 1000).toFixed(2)} TB`;
  return gb >= 1 ? `${gb.toFixed(1)} GB` : fmtBytes(bytes);
};
// "26 Sep · 14:00" in UTC — the library column is labelled UTC, so it must not drift with the
// viewer's own zone.
const fmtUtc = (iso) => {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  const day = d.toLocaleString("en-GB", { day: "numeric", month: "short", timeZone: "UTC" });
  const time = d.toLocaleString("en-GB", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "UTC" });
  return `${day} · ${time}`;
};
const fmtAsOf = (iso) => {
  if (!iso) return null;
  const d = new Date(iso);
  return `${d.toLocaleString("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" })} · ${d.toLocaleString("en-GB", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "UTC" })} UTC`;
};

const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

// The four states the KPIs, the state filter and the rail children speak in. A row's own
// verdict comes from the server (recordingState); this only groups it.
const GROUP_OF = {
  ready: "ready",
  processing: "processing",
  in_progress: "processing",
  storage_unavailable: "attention",
  failed: "attention",
};
const STATE_FILTERS = [
  ["all", "All states"],
  ["ready", "Replay-ready"],
  ["processing", "Processing"],
  ["attention", "Needs attention"],
];
const STATE_TONE = {
  ready: "green",
  processing: "sky",
  in_progress: "amber",
  storage_unavailable: "rose",
  failed: "rose",
};
const SORTS = {
  "date-desc": (a, b) => (b.started_at || "").localeCompare(a.started_at || ""),
  "date-asc": (a, b) => (a.started_at || "").localeCompare(b.started_at || ""),
  "size-desc": (a, b) => (b.size_bytes || 0) - (a.size_bytes || 0),
  "name-asc": (a, b) => (a.title || "").localeCompare(b.title || ""),
};

const control = cx(
  "h-9 rounded-lg border px-3 text-[13px] outline-none focus:border-violet-400 focus:ring-2 focus:ring-violet-500/20",
  CONSOLE.control
);

// Viewer access, said per state: a ready file still plays only under its event's policy.
function viewerAccess(stateKey, validated) {
  if (stateKey === "ready") return [FiLock, validated ? "Event policy" : "Event policy · validation pending"];
  if (stateKey === "in_progress") return [FiClock, "Not yet ready"];
  return [FiEyeOff, "Not available"];
}

function IconAction({ icon: Icon, label, danger, disabled, onClick }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={label}
      aria-label={label}
      className={cx(
        "grid h-7 w-7 shrink-0 place-items-center rounded-lg border text-[13px] transition-colors disabled:cursor-not-allowed disabled:opacity-40",
        danger
          ? "border-slate-200 text-slate-500 enabled:hover:border-rose-300 enabled:hover:bg-rose-50 enabled:hover:text-rose-600 dark:border-white/[0.14] dark:text-neutral-300 dark:enabled:hover:bg-rose-500/10 dark:enabled:hover:text-rose-300"
          : "border-slate-200 text-slate-500 enabled:hover:bg-slate-50 enabled:hover:text-slate-800 dark:border-white/[0.14] dark:text-neutral-300 dark:enabled:hover:bg-white/[0.08] dark:enabled:hover:text-white",
        focusRing
      )}
    >
      <Icon aria-hidden="true" />
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

// Plays the recording's own signed URL. Not a new playback stack: the URL is produced by
// the same services/livekit.signed_url the rest of the product uses, and a <video> element
// is all a finished MP4 needs. The viewer portal (/events/:id/watch) stays the attendee
// surface for a live or public event; this is the operator's library.
function PlayerModal({ recording, onClose }) {
  if (!recording) return null;
  return (
    <Modal open onClose={onClose} title={recording.title || "Recording"} size="xl">
      {/* No <track>: egress produces no caption file, and adding an empty one would claim
          accessibility support that does not exist. */}
      <video
        src={recording.url}
        controls
        autoPlay
        className="aspect-video w-full rounded-lg bg-black"
        data-testid="recording-player"
      />
    </Modal>
  );
}

// A recently captured asset: its category colour, duration, state and the way into its
// event's Replay section. Play opens THIS recording's signed URL (see PlayerModal) — never
// the attendee watch page, which is keyed on the event and runs the viewer access path.
function PreviewCard({ r, onWatch }) {
  const state = recordingState(r);
  const ready = state?.key === "ready" && Boolean(r.url);
  const title = r.title || "Untitled event";
  return (
    <li className={cx("flex min-w-0 flex-col overflow-hidden rounded-xl border", CONSOLE.divider)}>
      <div className={cx("relative grid aspect-video place-items-center bg-gradient-to-br", thumbFor(r.category))}>
        {ready ? (
          <button
            type="button"
            onClick={() => onWatch(r)}
            aria-label={`Play ${title}`}
            className={cx("grid h-11 w-11 place-items-center rounded-full bg-white/15 text-white backdrop-blur transition hover:scale-105 hover:bg-white/25", focusRing)}
          >
            <FiPlay className="ml-0.5 text-lg" aria-hidden="true" />
          </button>
        ) : (
          <FiFilm className="text-2xl text-white/60" aria-hidden="true" />
        )}
        <span className="absolute bottom-2 right-2 rounded bg-black/60 px-1.5 py-0.5 text-[11px] font-medium tabular-nums text-white">
          {fmtDuration(r.duration_seconds)}
        </span>
      </div>
      <div className="flex flex-1 flex-col p-3.5">
        <p className={cx("truncate text-[13.5px] font-semibold", CONSOLE.heading)} title={title}>{title}</p>
        <p className={cx("mt-0.5 truncate text-[12px]", CONSOLE.faint)}>
          {r.started_at ? fmtDate(r.started_at) : "Date unknown"} · {ready ? "Event replay" : state?.label}
        </p>
        <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
          <Tag plain tone={STATE_TONE[state?.key] || "slate"}>{state?.label}</Tag>
          <Link
            to={`/organization/events/${r.event_id}/replay#recordings`}
            aria-label={`Open asset: ${title}`}
            className={cx("inline-flex items-center gap-0.5 whitespace-nowrap rounded text-[12px] font-semibold", CONSOLE.link, focusRing)}
          >
            Open asset <FiChevronRight aria-hidden="true" />
          </Link>
        </div>
      </div>
    </li>
  );
}

// One library row. Every action is offered only when it can work: Watch, Download and Share
// need a file in storage; Export and Delete are blocked by a legal hold. The reason a row is
// not playable is listed under Processing & exceptions rather than discovered by clicking.
function LibraryRow({ r, onWatch, onDownload, onShare, onDelete, onExport }) {
  const state = recordingState(r);
  const ready = state?.key === "ready" && Boolean(r.url);
  const title = r.title || "Untitled event";
  const [AccessIcon, access] = viewerAccess(state?.key, r.validation_status === "valid");
  return (
    <tr>
      <td className="py-3 pl-5 pr-3">
        <div className="flex min-w-0 items-center gap-3">
          <span aria-hidden="true" className={cx("grid h-8 w-12 shrink-0 place-items-center rounded-md bg-gradient-to-br text-white/70", thumbFor(r.category))}>
            <FiFilm />
          </span>
          <div className="min-w-0">
            <p className={cx("max-w-[12rem] truncate text-[13px] font-semibold", CONSOLE.heading)} title={title}>{title}</p>
            <p className={cx("truncate text-[12px]", CONSOLE.faint)}>
              {r.category?.trim() || "Uncategorized"} ·{" "}
              {/* A separate action from Watch Replay, and labelled as one. */}
              <Link to={`/organization/events/${r.event_id}`} className={cx("rounded font-medium", CONSOLE.link, focusRing)}>
                View event
              </Link>
            </p>
            {r.legal_hold && (
              <Tag tone="rose" className="mt-1"><FiLock aria-hidden="true" /> Legal hold</Tag>
            )}
          </div>
        </div>
      </td>
      <td className={cx("whitespace-nowrap px-3 py-3 text-[12.5px] tabular-nums", CONSOLE.body)}>{fmtUtc(r.started_at)}</td>
      <td className="whitespace-nowrap px-3 py-3 tabular-nums">
        <p className={cx("text-[12.5px] font-semibold", CONSOLE.heading)}>{fmtDuration(r.duration_seconds)}</p>
        <p className={cx("text-[12px]", CONSOLE.faint)}>{fmtBytes(r.size_bytes)}</p>
      </td>
      <td className="px-3 py-3">
        <Tag plain tone={STATE_TONE[state?.key] || "slate"}>{state?.label}</Tag>
        <p className={cx("mt-1 flex items-start gap-1 text-[11.5px]", CONSOLE.faint)}>
          <AccessIcon aria-hidden="true" className="mt-0.5 shrink-0" /> <span>{access}</span>
        </p>
      </td>
      <td className="py-3 pl-3 pr-5">
        <div className="flex items-center justify-end gap-0.5">
          <IconAction icon={FiPlay} label={`Watch Replay — ${title}`} disabled={!ready} onClick={() => onWatch(r)} />
          <IconAction icon={FiDownload} label={`Download ${title}`} disabled={!ready} onClick={() => onDownload(r)} />
          {/* Not for a recording with no playable file: the link would lead to nothing. */}
          <IconAction icon={FiShare2} label={`Share ${title}`} disabled={!ready} onClick={() => onShare(r)} />
          <IconAction icon={FiSend} label={`Export ${title}`} disabled={r.legal_hold} onClick={() => onExport(r)} />
          <IconAction icon={FiTrash2} label={`Delete ${title}`} danger disabled={r.legal_hold} onClick={() => onDelete(r)} />
        </div>
      </td>
    </tr>
  );
}

// What is finalising, running or failed — each with the server's own reason and the way to
// its event's recording history. A failed attempt has no file, so it is not a library row —
// but it must not vanish either.
function ExceptionsPanel({ rows, summary }) {
  const failed = summary?.failed_attempts || 0;
  const running = summary?.in_progress || 0;
  const count = rows.length + failed + running;
  const listed = rows.slice(0, 5);
  return (
    <Panel
      title="Processing & exceptions"
      subtitle="What is finalising, running or failed"
      tag={count > 0 ? String(count) : undefined}
      tagTone="amber"
    >
      {count === 0 ? (
        <p className={cx("rounded-xl border border-dashed px-4 py-5 text-center text-[12.5px]", CONSOLE.divider, CONSOLE.faint)}>
          Nothing is processing or failing.
        </p>
      ) : (
        <ul className="space-y-3">
          {summary?.latest_failure && (
            <li
              role="status"
              data-testid="recording-failure-notice"
              className="flex gap-3 rounded-xl border border-amber-200 bg-amber-50/80 p-3 text-amber-900 dark:border-amber-400/30 dark:bg-amber-500/[0.08] dark:text-amber-100"
            >
              <IconChip icon={FiAlertTriangle} tone="amber" size="sm" />
              <div className="min-w-0 text-[12.5px]">
                <p className="font-semibold">
                  {failed} recording attempt{failed === 1 ? "" : "s"} did not capture a file.
                </p>
                <p className="mt-0.5 break-words">
                  Latest: <span className="font-semibold">{summary.latest_failure.event_title || "Untitled event"}</span>
                  {summary.latest_failure.error ? ` — ${summary.latest_failure.error}` : ""}
                </p>
                <Link
                  to={`/organization/events/${summary.latest_failure.event_id}/replay`}
                  className={cx("mt-1 inline-flex items-center gap-0.5 rounded font-semibold underline", focusRing)}
                >
                  View the event's recording history <FiChevronRight aria-hidden="true" />
                </Link>
              </div>
            </li>
          )}
          {running > 0 && (
            <li className="flex gap-3">
              <IconChip icon={FiClock} tone="sky" size="sm" />
              <div className="min-w-0">
                <p className={cx("text-[13px] font-semibold", CONSOLE.heading)}>{plural(running, "recording")} running now</p>
                <p className={cx("text-[12px]", CONSOLE.faint)}>Each finishes when its broadcast ends.</p>
              </div>
            </li>
          )}
          {listed.map((r) => {
            const state = recordingState(r);
            const attention = GROUP_OF[state?.key] === "attention";
            return (
              <li key={r.id} className="flex gap-3">
                <IconChip icon={attention ? FiAlertTriangle : FiClock} tone={attention ? "amber" : "sky"} size="sm" />
                <div className="min-w-0">
                  <p className={cx("truncate text-[13px] font-semibold", CONSOLE.heading)}>{r.title || "Untitled event"}</p>
                  <p className={cx("line-clamp-2 text-[12px]", CONSOLE.faint)}>{state?.label} · {state?.detail}</p>
                  <Link
                    to={`/organization/events/${r.event_id}/replay#recordings`}
                    className={cx("mt-0.5 inline-flex items-center gap-0.5 rounded text-[12px] font-semibold", CONSOLE.link, focusRing)}
                  >
                    {attention ? "Review the event's recording" : "View processing status"} <FiChevronRight aria-hidden="true" />
                  </Link>
                </div>
              </li>
            );
          })}
          {rows.length > listed.length && (
            <li className={cx("text-[12px]", CONSOLE.faint)}>
              {plural(rows.length - listed.length, "more recording")} in the library below.
            </li>
          )}
        </ul>
      )}
      <Footnote>States come from the media service; completion is never inferred from elapsed time.</Footnote>
    </Panel>
  );
}

const GOVERNANCE = [
  [FiLock, "Playback links are signed and time-limited"],
  [FiSend, "Exports are single-recipient, expiring and audited"],
  [FiShield, "A legal hold blocks export and deletion"],
];

function GovernanceCard() {
  return (
    <DarkCard>
      <p className="flex items-center gap-2 text-[10.5px] font-bold uppercase tracking-[0.16em] text-violet-200">
        <FiShield aria-hidden="true" /> Replay governance
      </p>
      <h2 className="mt-3 text-[20px] font-bold leading-snug">Protect media from capture to replay.</h2>
      <p className="mt-2 text-[12.5px] leading-relaxed text-white/70">
        Recording access, export and deletion follow your organization&apos;s permissions and each event&apos;s viewer policy.
      </p>
      <ul className="mt-4 space-y-2 border-t border-white/10 pt-4 text-[12.5px] text-white/85">
        {GOVERNANCE.map(([Icon, text]) => (
          <li key={text} className="flex items-center gap-2.5"><Icon aria-hidden="true" className="shrink-0 text-white/60" /> {text}</li>
        ))}
      </ul>
      <p className="mt-4 text-[10.5px] font-bold uppercase tracking-[0.14em] text-white/50">Deletion requires an organization admin</p>
    </DarkCard>
  );
}

const LEGEND = [
  ["ready", "Ready", "File in storage; playback still follows event policy"],
  ["processing", "Processing", "Capture finished; file still finalising"],
  ["in_progress", "Recording", "Capture running; ends with the broadcast"],
  ["storage_unavailable", "Storage unavailable", "Capture ran, but no file is in storage"],
  ["failed", "Not captured", "No file was produced; see the event's history"],
];

function StateLegend() {
  return (
    <Panel title="Know each recording state" subtitle="What each state means here" action={<FiInfo aria-hidden="true" className={CONSOLE.faint} />}>
      {/* One grid for every row, so the tag column is as wide as its widest tag. */}
      <dl className="grid grid-cols-[max-content_minmax(0,1fr)] items-center gap-x-3 gap-y-2.5 text-[12.5px]">
        {LEGEND.map(([key, label, meaning]) => (
          <div key={key} className="contents">
            <dt><Tag plain tone={STATE_TONE[key]}>{label}</Tag></dt>
            <dd className={CONSOLE.muted}>{meaning}</dd>
          </div>
        ))}
      </dl>
    </Panel>
  );
}

const CRUMB = { all: "All recordings", ready: "Replay-ready", processing: "Processing status", attention: "Needs attention" };

export default function OrganizationRecordings() {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  // The library (captured recordings) plus what happened to every other attempt. The summary
  // is optional: an older API without it still renders the library exactly as before.
  const { data, loading, error, reload } = useApi(() =>
    Promise.all([
      api.get("/organization/recordings").then((r) => r.data),
      api.get("/organization/recordings/summary").then((r) => r.data).catch(() => null),
    ]).then(([list, summary]) => ({ list, summary, at: new Date().toISOString() }))
  );
  const list = Array.isArray(data?.list) ? data.list : NONE;
  const summary = data?.summary || null;
  const qParam = params.get("q") || "";
  const [prevQ, setPrevQ] = useState(qParam);
  const [query, setQueryText] = useState(qParam);
  const [exporting, setExporting] = useState(null);
  const [playing, setPlaying] = useState(null);

  if (prevQ !== qParam) {
    setPrevQ(qParam);
    setQueryText(qParam);
  }

  const resetView = useCallback(() => {
    setQueryText("");
    setParams({}, { replace: true });
    setExporting(null);
    setPlaying(null);
    reload();
    if (typeof window !== "undefined" && typeof window.scrollTo === "function") {
      try {
        window.scrollTo({ top: 0, behavior: "smooth" });
      } catch {
        // Unsupported in test environments
      }
    }
  }, [reload, setParams]);

  useEffect(() => {
    const onReset = (e) => {
      if (!e.detail?.path || e.detail.path === "/organization/recordings") {
        resetView();
      }
    };
    window.addEventListener("zoiko:route-reset", onReset);
    return () => window.removeEventListener("zoiko:route-reset", onReset);
  }, [resetView]);

  const stateParam = params.get("state");
  const stateFilter = STATE_FILTERS.some(([v]) => v === stateParam) ? stateParam : "all";
  const sort = SORTS[params.get("sort")] ? params.get("sort") : "date-desc";
  const category = params.get("category") || ALL_CATEGORIES;
  const setParam = (key, value, fallback) =>
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      if (!value || value === fallback) next.delete(key);
      else next.set(key, value);
      return next;
    }, { replace: true });
  const setQuery = (value) => {
    setQueryText(value);
    setParam("q", value.trim() ? value : "", "");
  };

  // The real categories of the recordings listed (their events' categories), each once. Empty
  // when there is nothing to filter by — no recordings yet, or none with a category — and then
  // the filter is not shown at all rather than as a dropdown whose only option is "All".
  const categories = useMemo(() => categoryOptions(list), [list]);
  // A selection that no longer exists (its last recording went away on a reload) is "All".
  const activeCategory = categories.some((c) => c.value === category) ? category : ALL_CATEGORIES;

  // Only rows that are actually a library recording count as replay-ready or toward storage.
  // A capture still finalising, or one whose object is missing from the bucket, is listed so
  // the operator can see it — but counting it as a recording would report something the org
  // does not have.
  const ready = useMemo(() => list.filter((r) => recordingState(r)?.key === "ready"), [list]);
  // Everything that is not a playable recording yet, said rather than folded into "0".
  const notReady = useMemo(() => {
    const by = {};
    for (const r of list) {
      const key = recordingState(r)?.key;
      if (key && key !== "ready") by[key] = (by[key] || 0) + 1;
    }
    return by;
  }, [list]);
  const exceptions = useMemo(() => list.filter((r) => GROUP_OF[recordingState(r)?.key] !== "ready"), [list]);

  // A captured recording whose size never arrived (the egress_ended webhook carries it, and
  // is the only place it exists) has size_bytes = null. Counting those as 0 would understate
  // the org's storage — an unknown is not a zero. The known total is shown with the
  // shortfall named.
  const storage = useMemo(() => {
    const known = ready.filter((r) => typeof r.size_bytes === "number");
    return {
      bytes: known.reduce((sum, r) => sum + r.size_bytes, 0),
      unknown: ready.length - known.length,
    };
  }, [ready]);

  const processingCount = (notReady.processing || 0) + (notReady.in_progress || 0) + (summary?.in_progress || 0);
  const processingHint = [
    notReady.processing ? `${notReady.processing} processing` : null,
    notReady.in_progress ? `${notReady.in_progress} recording` : null,
    summary?.in_progress ? `${summary.in_progress} recording now` : null,
  ].filter(Boolean).join(" · ") || "Nothing finalising";
  const attentionCount = (notReady.storage_unavailable || 0) + (notReady.failed || 0) + (summary?.failed_attempts || 0);
  const attentionHint = [
    notReady.storage_unavailable ? `${notReady.storage_unavailable} unavailable in storage` : null,
    notReady.failed ? `${notReady.failed} not captured` : null,
    summary?.failed_attempts ? `${plural(summary.failed_attempts, "attempt")} not captured` : null,
  ].filter(Boolean).join(" · ") || "No failed or missing files";
  const libraryTotal = summary?.library_total ?? list.length;
  const capped = summary && summary.library_total > list.length;

  const recent = useMemo(() => [...list].sort(SORTS["date-desc"]).slice(0, 3), [list]);
  const events = useMemo(() => {
    const seen = new Map();
    for (const r of list) if (!seen.has(r.event_id)) seen.set(r.event_id, r.title || "Untitled event");
    return [...seen].map(([value, label]) => ({ value, label }));
  }, [list]);

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    const out = list.filter(
      (r) =>
        matchesCategory(r, activeCategory) &&
        (stateFilter === "all" || GROUP_OF[recordingState(r)?.key] === stateFilter) &&
        (!q || (r.title || "").toLowerCase().includes(q) || (r.category || "").toLowerCase().includes(q))
    );
    return [...out].sort(SORTS[sort]);
  }, [list, query, activeCategory, stateFilter, sort]);

  const onDownload = (r) => {
    if (!r.url) return;
    window.open(r.url, "_blank", "noopener");
  };
  const onShare = (r) => {
    // The server's link (custom domain when active), not whatever origin this console is on.
    const url = r.public_watch_url || `${window.location.origin}/events/${r.event_id}/watch`;
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
  const pending = loading && !data;

  return (
    <div className="space-y-6">
      <OrganizationPageHeader
        breadcrumbs={[["Organization", "/organization/manage"], ["Media & Recordings", "/organization/recordings", { onNavigate: resetView }], [CRUMB[stateFilter]]]}
        eyebrow="Organization console / Media operations"
        title="Media & recordings"
        subtitle="One secure library for your event recordings, replay readiness and processing status."
        actions={
          <>
            {data?.at && (
              <span className={cx("inline-flex items-center gap-1.5 text-[12px]", CONSOLE.faint)}>
                <FiClock aria-hidden="true" /> As of {fmtAsOf(data.at)}
              </span>
            )}
            {events.length > 0 && (
              <Dropdown
                label="Select event"
                placeholder="Select event"
                value={null}
                options={events}
                align="right"
                width="w-64"
                onChange={(id) => navigate(`/organization/events/${id}/replay#recordings`)}
              />
            )}
            <ConsoleButton variant="secondary" size="sm" leftIcon={FiRefreshCw} onClick={reload} disabled={loading}>
              Refresh
            </ConsoleButton>
            <a
              href="#library"
              className={cx(consoleAction, "inline-flex h-9 items-center gap-2 rounded-lg px-3.5 text-[13px] font-semibold", focusRing)}
            >
              <FiFolder aria-hidden="true" /> Browse recordings
            </a>
          </>
        }
      />

      <KpiRow>
        <KpiTile
          icon={FiFilm}
          label="Recordings in scope"
          value={pending ? null : libraryTotal}
          caption={pending ? "Loading…" : capped ? `Newest ${list.length} listed below` : "Captured across your events"}
        />
        <KpiTile icon={FiCheck} tone="green" label="Replay-ready" value={pending ? null : ready.length} caption="Playable files in storage" />
        <KpiTile icon={FiClock} tone="sky" label="Processing" value={pending ? null : processingCount} caption={pending ? undefined : processingHint} />
        <KpiTile icon={FiAlertTriangle} tone="amber" label="Needs attention" value={pending ? null : attentionCount} caption={pending ? undefined : attentionHint} />
      </KpiRow>

      <Banner icon={FiShield} title="Replay-ready is not the same as publicly accessible." aside={<Tag tone="violet">Event-scoped access rules</Tag>}>
        Viewer playback follows each event&apos;s access policy. Download, export and deletion are separate actions, and a legal hold blocks export and deletion.
      </Banner>

      <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,1fr)_18rem] 2xl:grid-cols-[minmax(0,1fr)_24rem]">
        <div className="min-w-0 space-y-6">
          <Panel
            title="Recently captured"
            subtitle="The newest recordings in your library"
            action={list.length > 0 && (
              <a href="#library" className={cx("inline-flex items-center gap-1 rounded text-[12px] font-semibold", CONSOLE.link, focusRing)}>
                View library <FiChevronRight aria-hidden="true" />
              </a>
            )}
          >
            {pending ? (
              <p className={cx("py-6 text-center text-[12.5px]", CONSOLE.faint)}>Loading recordings…</p>
            ) : recent.length === 0 ? (
              <p className={cx("rounded-xl border border-dashed px-4 py-6 text-center text-[12.5px]", CONSOLE.divider, CONSOLE.faint)}>
                Nothing has been captured yet.
              </p>
            ) : (
              <ul className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                {recent.map((r) => <PreviewCard key={r.id} r={r} onWatch={setPlaying} />)}
              </ul>
            )}
          </Panel>

          <Panel
            id="library"
            title="Recording library"
            subtitle="Filter by name, media state, category and recorded date"
            flush
            action={
              <div role="group" aria-label="Storage used" className="text-right">
                <p className={cx("text-[11px] font-semibold uppercase tracking-wider", CONSOLE.muted)}>Storage used</p>
                {/* An em dash, not "0.0 GB", whenever there is no measured total: with no
                    completed recording there is nothing to add up, and with every size unknown
                    there is only an absence of one. */}
                <p className={cx("text-[15px] font-bold tabular-nums", CONSOLE.heading)}>
                  {ready.length === 0 || storage.unknown === ready.length ? "—" : fmtTotalBytes(storage.bytes)}
                </p>
                {(ready.length === 0 || storage.unknown > 0) && (
                  <p className={cx("text-[11px]", CONSOLE.faint)}>
                    {ready.length === 0
                      ? "No completed recordings"
                      : `Excludes ${plural(storage.unknown, "recording")} of unknown size`}
                  </p>
                )}
              </div>
            }
          >
            <div className="flex flex-wrap items-center gap-2 px-5 pb-4">
              <div className="relative min-w-[12rem] flex-1">
                <FiSearch aria-hidden="true" className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                <input
                  type="search"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="Search recordings..."
                  aria-label="Search recordings"
                  className={cx(control, "w-full pl-9")}
                />
              </div>
              <select
                value={stateFilter}
                onChange={(e) => setParam("state", e.target.value, "all")}
                aria-label="Filter by state"
                className={control}
              >
                {STATE_FILTERS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              </select>
              {/* Filter by category — only when the recordings listed have at least one. */}
              {categories.length > 0 && (
                <select
                  value={activeCategory}
                  onChange={(e) => setParam("category", e.target.value, ALL_CATEGORIES)}
                  aria-label="Filter by category"
                  className={control}
                >
                  <option value={ALL_CATEGORIES}>All categories</option>
                  {categories.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
                </select>
              )}
              <select
                value={sort}
                onChange={(e) => setParam("sort", e.target.value, "date-desc")}
                aria-label="Sort recordings"
                className={control}
              >
                <option value="date-desc">Newest first</option>
                <option value="date-asc">Oldest first</option>
                <option value="size-desc">Largest size</option>
                <option value="name-asc">Name (A–Z)</option>
              </select>
            </div>

            {error ? (
              <p className="border-t border-slate-100 px-5 py-12 text-center text-[13px] text-rose-600 dark:border-white/[0.08] dark:text-rose-300">
                Couldn&apos;t load recordings. Try refreshing the page.
              </p>
            ) : pending ? (
              <p className={cx("border-t px-5 py-12 text-center text-[13px]", CONSOLE.divider, CONSOLE.faint)}>Loading recordings…</p>
            ) : shown.length === 0 ? (
              <p className={cx("border-t px-5 py-12 text-center text-[13px]", CONSOLE.divider, CONSOLE.faint)}>
                {list.length === 0
                  ? "No recordings yet. A recording is made only when the host presses Record during the broadcast — enabling recording on an event does not start one automatically."
                  : "No recordings match your filters."}
              </p>
            ) : (
              <div className="overflow-x-auto border-t border-slate-100 dark:border-white/[0.08]">
                <table className="w-full min-w-[640px] text-left">
                  <thead className={cx("whitespace-nowrap bg-slate-50/70 text-[11px] uppercase tracking-wider dark:bg-white/[0.03]", CONSOLE.muted)}>
                    <tr>
                      <th scope="col" className="py-2.5 pl-5 pr-3 font-semibold">Recording / event</th>
                      <th scope="col" className="px-3 py-2.5 font-semibold">Captured (UTC)</th>
                      <th scope="col" className="px-3 py-2.5 font-semibold">Duration</th>
                      <th scope="col" className="px-3 py-2.5 font-semibold">Media state · access</th>
                      <th scope="col" className="py-2.5 pl-3 pr-5 text-right font-semibold">Actions</th>
                    </tr>
                  </thead>
                  <tbody className={cx("divide-y", CONSOLE.divideY)}>
                    {shown.map((r) => (
                      <LibraryRow key={r.id} r={r} onWatch={setPlaying} onDownload={onDownload} onShare={onShare} onDelete={onDelete} onExport={onExport} />
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {capped && (
              <p className={cx("border-t px-5 py-2.5 text-[12px]", CONSOLE.divider, CONSOLE.faint)} data-testid="recording-list-cap">
                Showing the newest {list.length} of {summary.library_total} recordings.
              </p>
            )}
          </Panel>
        </div>

        <aside className="min-w-0 space-y-6" aria-label="Processing and governance">
          <ExceptionsPanel rows={exceptions} summary={summary} />
          <GovernanceCard />
          <StateLegend />
        </aside>
      </div>

      {exporting && <ExportModal recording={exporting} onClose={() => setExporting(null)} />}
      <PlayerModal recording={playing} onClose={() => setPlaying(null)} />
    </div>
  );
}
