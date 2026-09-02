// client/src/pages/admin/EventDetail.jsx
// Cross-org event detail for the Super Admin console. Route: /admin/live-events/:eventId.
// Backed by GET/DELETE /admin/events/{id} (services/admin.py event_detail, routers/admin.py
// delete_event) — the org-scoped /events/{id} 404s for a super admin on any event outside
// their own platform org, so this reads through the admin-only, cross-org endpoints instead.
import { useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import toast from "react-hot-toast";
import {
  FiArrowLeft, FiCalendar, FiClock, FiEye, FiTrash2, FiUser, FiVideo, FiRadio,
} from "react-icons/fi";
import { Badge, Button, Panel } from "../../components/admin";
import api, { errMsg } from "../../api";
import useApi from "../../hooks/useApi";
import useInterval from "../../hooks/useInterval";
import { PageSpinner } from "../../ui/Spinner";
import { fmtDateTime } from "../../data/events";
import ConfirmDialog from "../../ui/ConfirmDialog";
import EventCommerceAdmin from "./EventCommerceAdmin";

const STATUS_TONE = { draft: "neutral", scheduled: "info", published: "info", live: "success", ended: "neutral", cancelled: "danger" };
const BROADCAST_STATUS_TONE = { live: "success", paused: "warning", ended: "neutral", preview: "info" };

// No WebSocket on this page either — same staleness bug as LiveEvents.jsx, worse here since
// an admin lands on this exact page precisely to check whether one specific event is still
// live. Same fix, same interval.
const POLL_MS = 15000;

function Meta({ icon: Icon, label, children }) {
  return (
    <div className="flex items-start gap-3">
      <span className="mt-0.5 grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400">
        <Icon />
      </span>
      <div className="min-w-0">
        <p className="text-[11px] font-medium uppercase tracking-wider text-slate-400">{label}</p>
        <div className="mt-0.5 text-sm font-medium text-slate-800 dark:text-slate-100">{children}</div>
      </div>
    </div>
  );
}

const initials = (name) => (name || "?").split(" ").map((w) => w[0]).join("").slice(0, 2).toUpperCase();

function PeopleList({ title, people }) {
  return (
    <Panel title={`${title} (${people.length})`}>
      {people.length === 0 ? (
        <p className="px-1 py-3 text-sm text-slate-400 dark:text-slate-500">None assigned.</p>
      ) : (
        <div className="space-y-2">
          {people.map((u) => (
            <div key={u.id} className="flex items-center gap-3 rounded-xl border border-slate-200 bg-white p-3 dark:border-slate-800 dark:bg-slate-900/50">
              <span className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-violet-100 text-xs font-semibold text-violet-700 dark:bg-violet-500/15 dark:text-violet-300">
                {initials(u.name)}
              </span>
              <div className="min-w-0">
                <p className="truncate text-sm font-medium text-slate-800 dark:text-slate-100">{u.name}</p>
                <p className="truncate text-xs text-slate-500 dark:text-slate-400">{u.email}</p>
              </div>
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}

export default function AdminEventDetail() {
  const { eventId } = useParams();
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);

  const { data: ev, loading, error, reload } = useApi(() =>
    api.get(`/admin/events/${eventId}`).then((r) => r.data)
  );
  useInterval(reload, POLL_MS);

  const back = (
    <button
      onClick={() => navigate("/admin/live-events")}
      className="inline-flex items-center gap-1.5 rounded text-sm font-medium text-slate-500 hover:text-slate-800 dark:text-slate-400 dark:hover:text-slate-200"
    >
      <FiArrowLeft /> Back to Live Operations
    </button>
  );

  if (loading) return <div className="space-y-4">{back}<PageSpinner label="Loading event…" /></div>;
  if (error) {
    return (
      <div className="space-y-4">
        {back}
        <div className="rounded-xl border border-rose-200 bg-rose-50 px-5 py-4 text-sm text-rose-700 dark:border-rose-500/20 dark:bg-rose-500/10 dark:text-rose-300">
          Couldn't load this event. {errMsg(error)}
        </div>
      </div>
    );
  }

  const isLive = ev.status === "live" || ev.broadcast?.status === "live" || ev.broadcast?.status === "paused";

  const remove = async () => {
    setBusy(true);
    try {
      await api.delete(`/admin/events/${eventId}`);
      toast.success(isLive ? "Broadcast force-ended and event deleted" : "Event deleted");
      navigate("/admin/live-events");
    } catch (e) {
      toast.error(errMsg(e));
      setBusy(false);
      setConfirmOpen(false);
    }
  };

  return (
    <div className="space-y-6">
      {back}

      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="text-2xl font-bold tracking-tight text-slate-900 dark:text-white">{ev.title || "Untitled event"}</h1>
            <Badge status={STATUS_TONE[ev.status] || "neutral"} dot={ev.status === "live"}>{ev.status}</Badge>
          </div>
          {ev.organization && (
            <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">{ev.organization.name}</p>
          )}
        </div>
        <Button variant="danger" size="sm" leftIcon={FiTrash2} loading={busy} onClick={() => setConfirmOpen(true)}>
          {isLive ? "Force-end & Delete" : "Delete"}
        </Button>
      </div>

      <ConfirmDialog
        open={confirmOpen}
        onClose={() => setConfirmOpen(false)}
        onConfirm={remove}
        busy={busy}
        title={isLive ? "Force-end this broadcast?" : "Delete this event?"}
        confirmLabel={isLive ? "Force-end & delete" : "Delete"}
        body={
          isLive ? (
            <>
              <strong className="font-semibold text-slate-800 dark:text-slate-100">{ev.title}</strong>{" "}
              is currently live. Deleting it will force-end the broadcast — stop recording and
              disconnect every viewer — and then delete the event. This cannot be undone.
            </>
          ) : (
            <>
              <strong className="font-semibold text-slate-800 dark:text-slate-100">{ev.title}</strong>{" "}
              will be permanently deleted. This cannot be undone.
            </>
          )
        }
      />

      <Panel title="Overview">
        <div className="grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-4">
          <Meta icon={FiCalendar} label="Starts">{fmtDateTime(ev.start_time)}</Meta>
          <Meta icon={FiClock} label="Ends">{ev.end_time ? fmtDateTime(ev.end_time) : "—"}</Meta>
          <Meta icon={FiEye} label="Visibility">{ev.visibility || "—"}</Meta>
          <Meta icon={FiUser} label="Category">{ev.category || "—"}</Meta>
        </div>
        {ev.description && (
          <p className="mt-5 border-t border-slate-100 pt-4 text-sm text-slate-600 dark:border-slate-800 dark:text-slate-300">{ev.description}</p>
        )}
      </Panel>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Panel title="Broadcast">
          {ev.broadcast ? (
            <div className="space-y-3">
              <div className="flex items-center gap-2">
                <FiRadio className="text-slate-400" />
                <Badge status={BROADCAST_STATUS_TONE[ev.broadcast.status] || "neutral"}>{ev.broadcast.status}</Badge>
              </div>
              <Meta icon={FiClock} label="Started">{ev.broadcast.started_at ? fmtDateTime(ev.broadcast.started_at) : "—"}</Meta>
              <Meta icon={FiClock} label="Ended">{ev.broadcast.ended_at ? fmtDateTime(ev.broadcast.ended_at) : "—"}</Meta>
              <Meta icon={FiUser} label="Peak viewers">{ev.broadcast.peak_viewers ?? 0}</Meta>
            </div>
          ) : (
            <p className="px-1 py-3 text-sm text-slate-400 dark:text-slate-500">No broadcast has run for this event yet.</p>
          )}
        </Panel>

        <Panel title="Recording">
          {ev.recording ? (
            <div className="space-y-3">
              <div className="flex items-center gap-2">
                <FiVideo className="text-slate-400" />
                <Badge status={ev.recording.enforced ? "success" : "warning"}>{ev.recording.status}</Badge>
              </div>
              <Meta icon={FiEye} label="Captured">{ev.recording.enforced ? "Yes" : "No — LiveKit egress unavailable"}</Meta>
              <Meta icon={FiVideo} label="Size">
                {ev.recording.size_bytes ? `${(ev.recording.size_bytes / (1024 ** 2)).toFixed(1)} MB` : "—"}
              </Meta>
            </div>
          ) : (
            <p className="px-1 py-3 text-sm text-slate-400 dark:text-slate-500">No recording for this event.</p>
          )}
        </Panel>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <PeopleList title="Hosts" people={ev.hosts} />
        <PeopleList title="Speakers" people={ev.speakers} />
      </div>

      <EventCommerceAdmin eventId={eventId} />
    </div>
  );
}
