import { useEffect, useMemo, useState } from "react";
import { FiFilm, FiPlay, FiRefreshCw, FiSearch, FiCheckCircle, FiUpload } from "react-icons/fi";
import {
  Badge, Button, DataTable, DetailField, KpiCard, Panel, CONSOLE, cx, type,
} from "../../components/admin";
import ConsoleScreen from "../../components/admin/ConsoleScreen";
import Drawer from "../../ui/Drawer";
import api, { errMsg } from "../../api";
import useApi from "../../hooks/useApi";
import { notify } from "../../ui/Toast";

// Stable identity for the "nothing loaded yet" case. The useMemo hooks below take this list
// as a dependency, and a fresh `[]` literal on every render would defeat every one of them
// (permanently, for a response that simply omits the field). It is never mutated.
const NONE = [];

// Media — cross-org recordings. Real GET /admin/recordings: every real LiveRecording row
// (services/admin.py's list_recordings), across every organization, nothing fabricated.
//
// Narrower than a full Media Asset Service (asset lifecycle, transcode job queue,
// preservation/legal-hold state, provenance chain) — those subsystems don't exist in this
// build, and the earlier version of this page said so on its face rather than inventing
// numbers for them. What's real is real: what got recorded, whether it actually landed in
// storage, and — if it did — playing it back with the same signed-URL path the audience
// watch page uses (services/livekit.py signed_url, existence-checked first).

const STATUSES = ["recording", "paused", "stopped", "failed"];
const STATUS_TONE = { recording: "danger", paused: "warning", stopped: "neutral", failed: "danger" };

// Dual-recording validation (services/validation.py) + the replay publish gate
// (routers/events.py::watch_event's ReplayEntitlement check).
const VALIDATION_TONE = { valid: "success", degraded: "warning", failed: "danger" };
const PUBLISH_TONE = {
  not_available: "neutral", ready_for_review: "warning", published: "success",
  withheld: "danger", expired: "neutral", deleted_preserved: "neutral",
};
const PUBLISH_LABEL = {
  not_available: "Not available", ready_for_review: "Ready for review", published: "Published",
  withheld: "Withheld", expired: "Expired", deleted_preserved: "Deleted (preserved)",
};

// publish_state="published" only flips the switch -- publish_replay queues the watermark
// burn without waiting for it (services/delivery.py's ticker, a real recording can run
// hours), so a viewer isn't actually served anything until watermark_status is "ready" too.
const WATERMARK_TONE = { not_applicable: "neutral", pending: "warning", ready: "success", failed: "danger" };
const WATERMARK_LABEL = {
  not_applicable: "Not started", pending: "Preparing…", ready: "Ready", failed: "Failed",
};

function fmtBytes(n) {
  if (!n) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let v = n, i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i += 1; }
  return `${v.toFixed(1)} ${units[i]}`;
}

function fmtDuration(startedAt, stoppedAt) {
  if (!startedAt) return "—";
  const start = new Date(startedAt).getTime();
  const end = stoppedAt ? new Date(stoppedAt).getTime() : Date.now();
  const mins = Math.max(0, Math.round((end - start) / 60000));
  if (mins < 60) return `${mins}m`;
  return `${Math.floor(mins / 60)}h ${mins % 60}m`;
}

function useRecordingsData(status, orgId) {
  return useApi(() =>
    api
      .get("/admin/recordings", { params: { status: status === "all" ? undefined : status, org_id: orgId === "all" ? undefined : orgId } })
      .then((r) => r.data)
  );
}

// services/validation.py's real ffprobe-based comparison for a dual-recording pair —
// evidence is written identically to BOTH rows of a pair, so recording.validation_evidence
// alone (when role is set) already has the primary+secondary breakdown; no second fetch.
function ValidationPanel({ recording }) {
  if (!recording.role) return null; // single-path event — nothing was ever compared

  const status = recording.validation_status;
  const ev = recording.validation_evidence;

  return (
    <div className="mt-4 rounded-lg border border-slate-200 p-3 dark:border-white/10">
      <div className="flex items-center justify-between">
        <p className={cx("text-[11px] font-semibold uppercase tracking-wide", CONSOLE.faint)}>Dual-recording validation</p>
        {status ? (
          <Badge tone={VALIDATION_TONE[status] || "neutral"} dot>{status}</Badge>
        ) : (
          <Badge tone="neutral" dot>Awaiting the other path</Badge>
        )}
      </div>
      {ev ? (
        <dl className="mt-2.5 space-y-1.5 text-[12px]">
          {ev.duration_delta_seconds != null && (
            <div className="flex justify-between">
              <dt className={CONSOLE.faint}>Duration delta</dt>
              <dd className={type.mono}>{ev.duration_delta_seconds}s</dd>
            </div>
          )}
          {["primary", "secondary"].map((label) => {
            const side = ev[label];
            if (!side) return null;
            return (
              <div key={label} className="flex justify-between">
                <dt className={cx(CONSOLE.faint, "capitalize")}>{label}</dt>
                <dd className={type.mono}>
                  {side.error
                    ? side.error
                    : `${side.duration_seconds ?? "—"}s · ${side.has_video ? "video ✓" : "no video"} · ${side.has_audio ? "audio ✓" : "no audio"}`}
                </dd>
              </div>
            );
          })}
          <p className={cx("pt-1.5 text-[11px]", CONSOLE.faint)}>
            Not checked: gap/black-frame detection, caption QA.
          </p>
        </dl>
      ) : (
        <p className={cx("mt-2 text-[12px]", CONSOLE.faint)}>
          Waiting for both recording paths to finish before comparing.
        </p>
      )}
    </div>
  );
}

function PublishPanel({ loading, entitlement, publishing, onPublish, retrying, onRetryWatermark }) {
  const state = entitlement?.publish_state || "not_available";
  const wm = entitlement?.watermark_status || "not_applicable";
  return (
    <div className="mt-4 rounded-lg border border-slate-200 p-3 dark:border-white/10">
      <div className="flex items-center justify-between">
        <p className={cx("text-[11px] font-semibold uppercase tracking-wide", CONSOLE.faint)}>Audience replay</p>
        {loading ? (
          <span className={cx("text-[11px]", CONSOLE.faint)}>Loading…</span>
        ) : (
          <Badge tone={PUBLISH_TONE[state] || "neutral"} dot>{PUBLISH_LABEL[state] || state}</Badge>
        )}
      </div>
      <p className={cx("mt-1.5 text-[12px] leading-relaxed", CONSOLE.faint)}>
        {state === "published"
          ? "Viewers with access to this event can watch the replay."
          : "Never automatic — a viewer sees no replay until this is explicitly published."}
      </p>
      {!loading && entitlement && state !== "published" && (
        <Button
          className="mt-3"
          size="sm"
          leftIcon={FiUpload}
          loading={publishing}
          disabled={publishing}
          onClick={onPublish}
        >
          Publish replay
        </Button>
      )}
      {!loading && !entitlement && (
        <p className={cx("mt-2 text-[11px]", CONSOLE.faint)}>
          No replay entitlement yet — nothing has finished processing for this event.
        </p>
      )}
      {/* Watermark burn — separate from publish_state on purpose (see WATERMARK_TONE
          comment). Only shown once there's actually a burn to report on. */}
      {state === "published" && (
        <div className="mt-2.5 flex items-center justify-between">
          <span className={cx("text-[11px]", CONSOLE.faint)}>Watermarked copy</span>
          <Badge tone={WATERMARK_TONE[wm] || "neutral"} dot>{WATERMARK_LABEL[wm] || wm}</Badge>
        </div>
      )}
      {state === "published" && wm === "ready" && (
        <p className="mt-2 inline-flex items-center gap-1 text-[11px] text-emerald-600 dark:text-emerald-400">
          <FiCheckCircle /> Live to the audience
        </p>
      )}
      {state === "published" && wm === "pending" && (
        <p className={cx("mt-2 text-[11px]", CONSOLE.faint)}>
          Publishing is confirmed, but viewers won't see the replay until the watermark burn finishes — this can take a while for a long recording.
        </p>
      )}
      {state === "published" && wm === "failed" && (
        <>
          <p className="mt-2 text-[11px] text-rose-600 dark:text-rose-400">
            {entitlement.watermark_error || "The watermark burn failed."} Viewers see no replay until this is retried.
          </p>
          <Button className="mt-2" size="sm" leftIcon={FiRefreshCw} loading={retrying} disabled={retrying} onClick={onRetryWatermark}>
            Retry watermark
          </Button>
        </>
      )}
    </div>
  );
}

function RecordingDrawer({ recording, open, onClose }) {
  const [playing, setPlaying] = useState(false);
  const [entitlement, setEntitlement] = useState(null);
  const [loadingEntitlement, setLoadingEntitlement] = useState(true);
  const [publishing, setPublishing] = useState(false);
  const [retrying, setRetrying] = useState(false);
  // Diffed during render, not in the effect below (same pattern Credentials.jsx's
  // CreateKeyModal uses for its own `wasOpen` reset) — resetting loading here, rather
  // than as a synchronous setState at the top of the effect body, is what keeps the
  // fetch effect itself lint-clean (react-hooks/set-state-in-effect only objects to a
  // DIRECT setState in the effect body; the .then/.catch/.finally calls below are fine).
  const [fetchedFor, setFetchedFor] = useState(null);
  if (recording && recording.event_id !== fetchedFor) {
    setFetchedFor(recording.event_id);
    setLoadingEntitlement(true);
  }

  // Fetched per event, not per recording — the audience ReplayEntitlement is one row for
  // the whole event, same one routers/events.py::watch_event reads to gate replay.
  useEffect(() => {
    if (!recording) return undefined; // drawer closes right after (see the early return below);
    // stale entitlement state is harmless since it won't render until reopened with a real row.
    let cancelled = false;
    api
      .get(`/commercial/events/${recording.event_id}/replay-entitlements`)
      .then(({ data }) => {
        if (cancelled) return;
        setEntitlement((data || []).find((e) => e.scope === "audience") || null);
      })
      .catch(() => { if (!cancelled) setEntitlement(null); })
      .finally(() => { if (!cancelled) setLoadingEntitlement(false); });
    return () => { cancelled = true; };
  }, [recording]);

  const publish = async () => {
    if (!entitlement) return;
    setPublishing(true);
    try {
      const { data } = await api.post(`/commercial/replay-entitlements/${entitlement.id}/publish`);
      setEntitlement(data);
      notify.success("Replay published");
    } catch (e) {
      notify.error(errMsg(e, "Couldn't publish this replay"));
    } finally {
      setPublishing(false);
    }
  };

  const retryWatermark = async () => {
    if (!entitlement) return;
    setRetrying(true);
    try {
      const { data } = await api.post(`/commercial/replay-entitlements/${entitlement.id}/retry-watermark`);
      setEntitlement(data);
      notify.success("Watermark burn re-queued");
    } catch (e) {
      notify.error(errMsg(e, "Couldn't retry the watermark burn"));
    } finally {
      setRetrying(false);
    }
  };

  if (!recording) return <Drawer open={open} onClose={onClose} title="Recording" />;

  const play = async () => {
    setPlaying(true);
    try {
      const { data } = await api.get(`/admin/recordings/${recording.id}/playback-url`);
      window.open(data.url, "_blank", "noopener,noreferrer");
    } catch (e) {
      notify.error(errMsg(e, "No playable file for this recording"));
    } finally {
      setPlaying(false);
    }
  };

  return (
    <Drawer open={open} onClose={onClose} title={recording.event_title || "Untitled event"} width="w-[28rem] max-w-[90vw]">
      {/* DetailField's own label/value grid reads the sm: breakpoint off the viewport, not
          this ~450px drawer panel — an outer grid-cols-2 squeezes each field too narrow and
          wraps the value character-by-character. Stack instead, per DetailField's own doc
          comment on how a drawer should use it. */}
      <dl className={cx("divide-y", CONSOLE.divider)}>
        <DetailField label="Organization" value={recording.organization || "—"} />
        <DetailField label="Status" value={<Badge tone={STATUS_TONE[recording.status]}>{recording.status}</Badge>} />
        <DetailField label="Quality" value={recording.quality || "—"} />
        <DetailField label="Size" value={fmtBytes(recording.size_bytes)} />
        <DetailField label="Started" value={recording.started_at ? new Date(recording.started_at).toLocaleString() : "—"} />
        <DetailField label="Duration" value={fmtDuration(recording.started_at, recording.stopped_at)} />
        <DetailField
          label="LiveKit egress"
          value={recording.enforced ? <Badge tone="success">Enforced</Badge> : <Badge tone="warning">Not enforced</Badge>}
        />
        {recording.role && <DetailField label="Recording path" value={recording.role} />}
      </dl>

      {recording.error && (
        <div className="mt-4 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2.5 text-[12px] text-rose-700 dark:border-rose-500/25 dark:bg-rose-500/10 dark:text-rose-300">
          {recording.error}
        </div>
      )}

      <ValidationPanel recording={recording} />

      <PublishPanel
        loading={loadingEntitlement}
        entitlement={entitlement}
        publishing={publishing}
        onPublish={publish}
        retrying={retrying}
        onRetryWatermark={retryWatermark}
      />

      <Button
        className="mt-5"
        size="sm"
        leftIcon={FiPlay}
        loading={playing}
        disabled={playing || !recording.has_file_reference}
        onClick={play}
      >
        {recording.has_file_reference ? "Play recording" : "No file captured"}
      </Button>
      {recording.has_file_reference && (
        <p className={cx("mt-2 text-[11px]", CONSOLE.faint)}>
          Existence is checked against storage at click time — a reference existing here doesn't guarantee the file is still there.
        </p>
      )}
    </Drawer>
  );
}

export default function Media() {
  const [status, setStatus] = useState("all");
  const [q, setQ] = useState("");
  const [selected, setSelected] = useState(null);

  const { data, loading, error, reload } = useRecordingsData(status, "all");
  const recordings = data || NONE;

  const counts = useMemo(() => ({
    total: recordings.length,
    failed: recordings.filter((r) => r.status === "failed").length,
    playable: recordings.filter((r) => r.has_file_reference).length,
  }), [recordings]);

  const rows = useMemo(() => {
    const query = q.trim().toLowerCase();
    return recordings.filter((r) => {
      if (query && !`${r.event_title || ""} ${r.organization || ""}`.toLowerCase().includes(query)) return false;
      return true;
    });
  }, [recordings, q]);

  const columns = [
    { key: "event_title", header: "Event", render: (r) => <p className={cx("text-[13px] font-semibold", CONSOLE.heading)}>{r.event_title || "Untitled event"}</p> },
    { key: "organization", header: "Organization", render: (r) => r.organization || "—" },
    { key: "status", header: "Status", render: (r) => <Badge tone={STATUS_TONE[r.status]} dot>{r.status}</Badge> },
    { key: "quality", header: "Quality", render: (r) => r.quality || "—" },
    { key: "started_at", header: "Started", align: "right", render: (r) => (r.started_at ? new Date(r.started_at).toLocaleString() : "—") },
    { key: "duration", header: "Duration", align: "right", render: (r) => fmtDuration(r.started_at, r.stopped_at) },
    { key: "size", header: "Size", align: "right", render: (r) => <span className={type.mono}>{fmtBytes(r.size_bytes)}</span> },
    { key: "action", header: "Action", align: "right", render: (r) => (
      <Button variant="secondary" size="sm" onClick={() => setSelected(r)} aria-label="Open recording">Open</Button>
    ) },
  ];

  return (
    <ConsoleScreen
      title="Media"
      subtitle="Cross-organization recordings — real GET /admin/recordings, every LiveRecording row this platform has actually captured."
      loading={loading}
      error={error}
      hasData={Boolean(data)}
      endpoint="/admin/recordings"
      onRetry={reload}
      actions={<Button variant="secondary" leftIcon={FiRefreshCw} onClick={reload}>Refresh</Button>}
    >
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <KpiCard label="Recordings" value={counts.total} />
        <KpiCard label="Failed" value={counts.failed} tone={counts.failed ? "text-rose-600 dark:text-rose-400" : undefined} pressed={status === "failed"} onClick={() => setStatus(status === "failed" ? "all" : "failed")} />
        <KpiCard label="Playable" value={counts.playable} />
      </div>

      <Panel title="Recordings" flush>
        <div className="flex flex-wrap items-center gap-3 px-4 py-3">
          <div className="relative min-w-[220px] flex-1">
            <FiSearch className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" aria-hidden="true" />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search event or organization…" aria-label="Search recordings" className={CONSOLE.search} />
          </div>
          <select value={status} onChange={(e) => setStatus(e.target.value)} className={CONSOLE.select} aria-label="Filter by status">
            <option value="all">All statuses</option>
            {STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        </div>
        <div className={cx("border-t", CONSOLE.divider)} />
        <DataTable
          columns={columns}
          rows={rows}
          rowKey={(r) => r.id}
          onRowClick={setSelected}
          pageSize={10}
          minWidth={1000}
          empty={{
            icon: FiFilm,
            title: "No recordings match these filters",
            description: "Nothing recorded yet, or nothing satisfies the active filters.",
          }}
        />
      </Panel>

      <RecordingDrawer recording={selected} open={Boolean(selected)} onClose={() => setSelected(null)} />
    </ConsoleScreen>
  );
}
