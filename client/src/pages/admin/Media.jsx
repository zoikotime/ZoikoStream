import { useMemo, useState } from "react";
import { FiFilm, FiPlay, FiRefreshCw, FiSearch } from "react-icons/fi";
import {
  Badge, Button, DataTable, DetailField, KpiCard, Panel, CONSOLE, cx, type,
} from "../../components/admin";
import ConsoleScreen from "../../components/admin/ConsoleScreen";
import Drawer from "../../ui/Drawer";
import api, { errMsg } from "../../api";
import useApi from "../../hooks/useApi";
import { notify } from "../../ui/Toast";

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

function RecordingDrawer({ recording, open, onClose }) {
  const [playing, setPlaying] = useState(false);
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
      <div className="grid grid-cols-2 gap-3">
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
      </div>

      {recording.error && (
        <div className="mt-4 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2.5 text-[12px] text-rose-700 dark:border-rose-500/25 dark:bg-rose-500/10 dark:text-rose-300">
          {recording.error}
        </div>
      )}

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
  const recordings = data || [];

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
