import { useCallback, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import {
  FiArrowLeft, FiCalendar, FiClock, FiEye, FiUsers, FiMic, FiUploadCloud, FiTrash2,
  FiVideo, FiBarChart2, FiKey, FiEdit2, FiCopy, FiArchive, FiSlash, FiRadio, FiFilm,
  FiMessageSquare, FiHelpCircle, FiPieChart, FiShield, FiSettings, FiGlobe, FiMonitor,
  FiPlay, FiPause, FiList, FiAlertCircle,
} from "react-icons/fi";
import api from "../../api";
import useApi from "../../hooks/useApi";
import useMutation from "../../hooks/useMutation";
import { useAuth } from "../../auth/AuthContext";
import { PageSpinner } from "../../ui/Spinner";
import OrganizationErrorState from "../../components/organization/OrganizationErrorState";
import EmptyState from "../../components/organization/OrganizationEmptyState";
import EventStatusBadge from "../../components/organization/EventStatusBadge";
import EventTeamPanel from "../../components/organization/EventTeamPanel";
import EventAccessLinks from "../../components/organization/EventAccessLinks";
import SectionCard from "../../components/admin/SectionCard";
import StatCard from "../../components/admin/StatCard";
import { ConsoleButton as Button } from "../../ui/Button";
import Badge from "../../ui/Badge";
import ConfirmDialog from "../../ui/ConfirmDialog";
import { cx, focusRing } from "../../ui/tokens";
import {
  TEAM_ROLES, VISIBILITY_HELP, fmtDateTime, fmtDuration, isOnAir, visLabel,
} from "../../data/events";
import EventFormModal from "./EventFormModal";

// The event control panel. Route: /organization/events/:id
//
// Every tab is either backed by a real endpoint or says plainly what is missing and where it
// will come from. A tab that renders invented numbers is worse than one that renders an
// explained empty state — that rule is why the Chat/Polls/Q&A/Analytics tabs point at the
// live consoles instead of drawing charts from nothing.

const TABS = [
  { key: "overview", label: "Overview", icon: FiVideo },
  { key: "schedule", label: "Schedule", icon: FiCalendar },
  { key: "team", label: "Team", icon: FiUsers },
  { key: "broadcast", label: "Broadcast", icon: FiRadio },
  { key: "registration", label: "Registration", icon: FiList },
  { key: "access", label: "Viewer Access", icon: FiKey },
  { key: "recording", label: "Recording", icon: FiFilm },
  { key: "replay", label: "Replay", icon: FiPlay },
  { key: "analytics", label: "Analytics", icon: FiBarChart2 },
  { key: "chat", label: "Chat", icon: FiMessageSquare },
  { key: "polls", label: "Polls", icon: FiPieChart },
  { key: "qa", label: "Q&A", icon: FiHelpCircle },
  { key: "audit", label: "Audit Logs", icon: FiShield },
  { key: "settings", label: "Settings", icon: FiSettings },
];

/** Lifecycle actions, each mapped to the transition the API will accept. `from` mirrors
 *  crud.status_transition_error so a button is only offered when it can succeed — the UI and
 *  the server agree on the state machine instead of the UI guessing and getting a 400. */
const LIFECYCLE = [
  { status: "published", label: "Publish", icon: FiUploadCloud, from: ["draft"], primary: true },
  { status: "scheduled", label: "Schedule", icon: FiCalendar, from: ["draft", "published"] },
  { status: "draft", label: "Unpublish", icon: FiSlash, from: ["published", "scheduled"] },
  { status: "ended", label: "End event", icon: FiPause, from: ["live", "paused"], danger: true },
  { status: "cancelled", label: "Cancel", icon: FiSlash, from: ["draft", "published", "scheduled", "live", "paused"], danger: true },
  { status: "archived", label: "Archive", icon: FiArchive, from: ["ended", "cancelled", "draft", "published", "scheduled"] },
];

function Meta({ icon: Icon, label, children }) {
  return (
    <div className="flex items-start gap-3">
      <span
        className="mt-0.5 grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400"
        aria-hidden="true"
      >
        <Icon />
      </span>
      <div className="min-w-0">
        <p className="text-[11px] font-medium uppercase tracking-wider text-slate-400">{label}</p>
        <div className="mt-0.5 text-sm font-medium text-slate-800 dark:text-slate-100">{children}</div>
      </div>
    </div>
  );
}

function Rows({ items }) {
  return (
    <dl className="divide-y divide-slate-100 text-sm dark:divide-slate-800">
      {items.map(([k, v, hint]) => (
        <div key={k} className="flex items-start justify-between gap-4 py-3">
          <dt className="shrink-0 text-slate-500 dark:text-slate-400">{k}</dt>
          <dd className="min-w-0 text-right">
            <span className="font-medium text-slate-800 dark:text-slate-100">{v}</span>
            {hint && <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">{hint}</p>}
          </dd>
        </div>
      ))}
    </dl>
  );
}

const onOff = (v) => (v ? "On" : "Off");

/** Points at the live console that owns a runtime surface, rather than half-rebuilding it
 *  here. The consoles are socket-driven and already exist; duplicating their panels on a
 *  REST page would show stale data next to a live one. */
function ConsoleLink({ icon, title, description, to, disabled, disabledNote }) {
  return (
    <EmptyState
      icon={icon}
      title={title}
      description={disabled ? disabledNote : description}
      action={
        !disabled && (
          <Button size="sm" href={to} leftIcon={FiRadio}>
            Open live console
          </Button>
        )
      }
    />
  );
}

export default function EventDetails() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { user } = useAuth();
  const canManage = ["org_admin", "super_admin"].includes(user?.role);

  const [tab, setTab] = useState("overview");
  const [editOpen, setEditOpen] = useState(false);
  const [confirm, setConfirm] = useState(null); // { kind:"status"|"delete", status?, label? }

  // Three reads, in parallel, once. The team arrives as ONE payload covering all six roles
  // (GET /events/{id}/team) — this page used to fire six requests for that panel.
  const { data, loading, error, reload } = useApi(() =>
    Promise.all([
      api.get(`/events/${id}`).then((r) => r.data),
      api.get(`/events/${id}/team`).then((r) => r.data),
      api.get("/organization/users", { params: { page_size: 100, status: "active" } }).then((r) => r.data.items),
    ]).then(([event, team, members]) => ({ event, team, members }))
  );

  // Team mutations return the full team, so the panel updates from the response without a
  // refetch. Kept in local state layered over the fetched value.
  const [teamOverride, setTeamOverride] = useState(null);
  const team = teamOverride || data?.team;
  const onTeamChange = useCallback((next) => next && setTeamOverride(next), []);

  const mutate = useMutation({ onDone: reload });

  const setStatus = async (status) => {
    const ok = await mutate.run(() => api.patch(`/events/${id}`, { status }), {
      success: `Event ${status === "draft" ? "moved back to draft" : status}`,
    });
    if (ok) setConfirm(null);
  };

  const duplicate = () =>
    mutate.run(() => api.post(`/events/${id}/duplicate`, { copy_team: true }), {
      success: (res) => `Duplicated as "${res.data.title}"`,
      onDone: (res) => navigate(`/organization/events/${res.data.id}`),
    });

  const del = async () => {
    const ok = await mutate.run(() => api.delete(`/events/${id}`), {
      success: "Event deleted",
      onDone: () => navigate("/organization/events"),
    });
    if (ok) setConfirm(null);
  };

  const clearPassword = () =>
    mutate.run(() => api.patch(`/events/${id}`, { access_password: "" }), {
      success: "Passphrase removed",
    });

  const back = (
    <button
      onClick={() => navigate("/organization/events")}
      className={cx(
        "inline-flex items-center gap-1.5 rounded text-sm font-medium text-slate-500 hover:text-slate-800",
        "dark:text-slate-400 dark:hover:text-slate-200",
        focusRing
      )}
    >
      <FiArrowLeft aria-hidden="true" /> Back to Events
    </button>
  );

  if (loading) return <div className="space-y-4">{back}<PageSpinner label="Loading event…" /></div>;
  if (error) {
    return (
      <div className="space-y-4">
        {back}
        <OrganizationErrorState error={error} onRetry={reload} title="Couldn't load this event" />
      </div>
    );
  }

  const { event, members } = data;
  const available = LIFECYCLE.filter((a) => a.from.includes(event.status));
  const teamTotal = TEAM_ROLES.reduce((n, r) => n + (team?.[r.key]?.length || 0), 0);
  const hostCount = team?.host?.length || 0;

  // Readiness: the same gates services/ops.py applies for the Command Center, restated for
  // the organizer. Only real checks — each one reads a field of this event.
  const gates = [
    { label: "Title set", ok: !!event.title },
    { label: "Start time scheduled", ok: !!event.start_time },
    { label: "Host assigned", ok: hostCount > 0 },
    { label: "Moderator assigned", ok: (team?.moderator?.length || 0) > 0 },
    { label: "Recording enabled", ok: event.recording_enabled },
  ];
  const failing = gates.filter((g) => !g.ok);

  return (
    <div className="space-y-6">
      {back}

      {/* Banner: identity + status + the lifecycle actions that are legal right now. */}
      <div className="relative overflow-hidden rounded-2xl bg-gradient-to-br from-violet-600 to-slate-900 p-6 sm:p-8">
        {event.banner_image && (
          <img
            src={event.banner_image}
            alt=""
            className="absolute inset-0 h-full w-full object-cover opacity-30"
          />
        )}
        <div className="absolute inset-0 bg-gradient-to-tr from-slate-900/70 via-slate-900/30 to-transparent" />
        <div className="relative flex flex-col gap-3">
          <div className="flex flex-wrap items-center gap-2">
            <EventStatusBadge status={event.status} />
            <Badge tone="neutral">{visLabel(event.visibility)}</Badge>
            {event.password_protected && <Badge tone="success">Passphrase</Badge>}
            {event.category && <Badge tone="neutral">{event.category}</Badge>}
          </div>
          <h1 className="text-2xl font-bold tracking-tight text-white sm:text-3xl">
            {event.title || "Untitled event"}
          </h1>
          {(event.short_description || event.description) && (
            <p className="max-w-2xl text-sm text-white/80">
              {event.short_description || event.description}
            </p>
          )}
        </div>
      </div>

      {/* Meta + actions */}
      <div className="rounded-xl border border-slate-200 bg-white p-5 dark:border-slate-800 dark:bg-slate-900/50">
        <div className="grid grid-cols-2 gap-5 lg:grid-cols-4">
          <Meta icon={FiCalendar} label="Starts">{fmtDateTime(event.start_time)}</Meta>
          <Meta icon={FiClock} label="Duration">{fmtDuration(event.duration_minutes)}</Meta>
          <Meta icon={FiEye} label="Visibility">{visLabel(event.visibility)}</Meta>
          <Meta icon={FiUsers} label="Team">{teamTotal} assigned</Meta>
        </div>

        {canManage && (
          <div className="mt-6 flex flex-wrap gap-2 border-t border-slate-100 pt-5 dark:border-slate-800">
            {available.map(({ status, label, icon, primary, danger }) => (
              <Button
                key={status}
                variant={primary ? "primary" : danger ? "ghost" : "secondary"}
                size="sm"
                leftIcon={icon}
                disabled={mutate.busy}
                className={danger ? "text-rose-500 hover:bg-rose-50 hover:text-rose-600 dark:hover:bg-rose-500/10" : ""}
                // Publishing and scheduling are reversible, so they act immediately.
                // Ending, cancelling and archiving are not — those confirm first.
                onClick={() =>
                  danger || status === "archived"
                    ? setConfirm({ kind: "status", status, label })
                    : setStatus(status)
                }
              >
                {label}
              </Button>
            ))}
            <span className="mx-1 hidden w-px self-stretch bg-slate-200 sm:block dark:bg-slate-800" aria-hidden="true" />
            <Button variant="secondary" size="sm" leftIcon={FiEdit2} onClick={() => setEditOpen(true)}>
              Edit
            </Button>
            <Button variant="secondary" size="sm" leftIcon={FiCopy} disabled={mutate.busy} onClick={duplicate}>
              Duplicate
            </Button>
            {isOnAir(event.status) && (
              <Button variant="secondary" size="sm" leftIcon={FiRadio} href={`/events/${event.id}/watch`}>
                Watch
              </Button>
            )}
            <Button
              variant="ghost"
              size="sm"
              leftIcon={FiTrash2}
              disabled={mutate.busy}
              className="text-rose-500 hover:bg-rose-50 hover:text-rose-600 dark:hover:bg-rose-500/10"
              onClick={() => setConfirm({ kind: "delete" })}
            >
              Delete
            </Button>
          </div>
        )}
      </div>

      {/* Tabs. A real tablist: arrow keys move between tabs, and the panel is labelled by
          its tab, which is what makes 14 tabs navigable without a mouse. */}
      <div
        role="tablist"
        aria-label="Event sections"
        className="flex gap-1 overflow-x-auto border-b border-slate-200 dark:border-slate-800"
        onKeyDown={(e) => {
          const dir = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
          if (!dir) return;
          e.preventDefault();
          const i = TABS.findIndex((t) => t.key === tab);
          setTab(TABS[(i + dir + TABS.length) % TABS.length].key);
        }}
      >
        {TABS.map(({ key, label, icon: Icon }) => {
          const active = tab === key;
          return (
            <button
              key={key}
              role="tab"
              id={`tab-${key}`}
              aria-selected={active}
              aria-controls={`panel-${key}`}
              tabIndex={active ? 0 : -1}
              onClick={() => setTab(key)}
              className={cx(
                "-mb-px inline-flex items-center gap-1.5 whitespace-nowrap border-b-2 px-3.5 py-2.5 text-sm font-medium transition",
                focusRing,
                active
                  ? "border-violet-500 text-violet-600 dark:text-violet-400"
                  : "border-transparent text-slate-500 hover:text-slate-800 dark:text-slate-400 dark:hover:text-slate-200"
              )}
            >
              <Icon className="text-base" aria-hidden="true" />
              {label}
            </button>
          );
        })}
      </div>

      <div role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`} tabIndex={-1}>
        {tab === "overview" && (
          <div className="space-y-6">
            <div className="grid grid-cols-2 gap-4 xl:grid-cols-4">
              <StatCard label="Hosts" value={hostCount} />
              <StatCard label="Moderators" value={team?.moderator?.length || 0} />
              <StatCard label="Speakers" value={team?.speaker?.length || 0} />
              <StatCard label="Duration (min)" value={event.duration_minutes ?? 0} />
            </div>

            {/* Readiness — actionable, not decorative: each unmet gate names what to do. */}
            <SectionCard
              title="Readiness"
              subtitle={failing.length ? `${failing.length} item${failing.length === 1 ? "" : "s"} outstanding` : "Ready to go live"}
              icon={failing.length ? FiAlertCircle : FiRadio}
              accent={failing.length ? "amber" : "violet"}
            >
              <ul className="space-y-2 text-sm">
                {gates.map((g) => (
                  <li key={g.label} className="flex items-center gap-2">
                    <Badge tone={g.ok ? "success" : "warning"} size="sm">{g.ok ? "OK" : "Todo"}</Badge>
                    <span className={g.ok ? "text-slate-600 dark:text-slate-300" : "font-medium text-slate-800 dark:text-slate-100"}>
                      {g.label}
                    </span>
                  </li>
                ))}
              </ul>
              {hostCount === 0 && (
                <p className="mt-4 text-sm text-amber-600 dark:text-amber-400">
                  Without an assigned host, nobody can open the broadcast console for this event.{" "}
                  <button onClick={() => setTab("team")} className={cx("font-semibold underline", focusRing)}>
                    Assign a host
                  </button>
                </p>
              )}
            </SectionCard>

            <SectionCard title="About this event" icon={FiVideo}>
              <p className="text-sm leading-relaxed text-slate-600 dark:text-slate-300">
                {event.description || "No description provided."}
              </p>
              <div className="mt-4 flex flex-wrap gap-2 text-xs">
                {event.timezone && (
                  <span className="rounded-full bg-slate-100 px-2.5 py-1 font-medium text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                    {event.timezone}
                  </span>
                )}
                {event.location && (
                  <span className="rounded-full bg-slate-100 px-2.5 py-1 font-medium text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                    {event.location}
                  </span>
                )}
                {(event.tags || []).map((t) => (
                  <span key={t} className="rounded-full bg-slate-100 px-2.5 py-1 font-medium text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                    #{t}
                  </span>
                ))}
              </div>
            </SectionCard>
          </div>
        )}

        {tab === "schedule" && (
          <SectionCard title="Schedule" icon={FiCalendar} action={
            canManage && <Button size="sm" variant="secondary" leftIcon={FiEdit2} onClick={() => setEditOpen(true)}>Edit</Button>
          }>
            <Rows
              items={[
                ["Status", <EventStatusBadge key="s" status={event.status} size="sm" />],
                ["Starts", fmtDateTime(event.start_time)],
                ["Ends", fmtDateTime(event.end_time)],
                ["Duration", fmtDuration(event.duration_minutes)],
                ["Time zone", event.timezone || "—",
                  "Displayed times use your browser's zone; this is the zone the organizer set."],
                ["Auto-end", onOff(event.auto_end_event)],
                ["Created", fmtDateTime(event.created_at)],
                ["Last updated", fmtDateTime(event.updated_at)],
                ["Created by", event.created_by_name || "—"],
              ]}
            />
          </SectionCard>
        )}

        {tab === "team" && (
          <EventTeamPanel
            eventId={event.id}
            event={event}
            team={team}
            members={members}
            canManage={canManage}
            onTeamChange={onTeamChange}
          />
        )}

        {tab === "broadcast" && (
          <div className="space-y-4">
            <SectionCard title="Broadcast configuration" icon={FiRadio}>
              <Rows
                items={[
                  ["Stream quality", event.stream_quality || "1080p", "The encoder target; a host can override it per broadcast."],
                  ["LiveKit room", <code key="r" className="font-mono text-xs">event_{event.id}</code>,
                    "One room per event. Created on preview or go-live, never before."],
                  ["Waiting room", onOff(event.waiting_room_enabled)],
                  ["Screen sharing", onOff(event.allow_screen_share)],
                  ["Raise hand", onOff(event.raise_hand_enabled)],
                  ["Auto-start recording", onOff(event.auto_start_recording)],
                  ["Live captions", onOff(event.captions_enabled),
                    "Advertised to attendees. The caption pipeline is not built on this deployment."],
                  ["Live translation", onOff(event.translation_enabled),
                    "Advertised to attendees. The translation pipeline is not built on this deployment."],
                  ["Max concurrent participants", event.max_participants ?? "No limit",
                    event.max_participants ? "Stored as intent — admission control is not enforced yet." : null],
                ]}
              />
            </SectionCard>

            <SectionCard title="Go live" subtitle="The broadcast console is the host's surface" icon={FiMonitor}>
              {hostCount === 0 ? (
                <EmptyState
                  icon={FiUsers}
                  title="No host assigned"
                  description="Assign a host on the Team tab. Broadcast control requires a per-event host assignment — an organization host role is not enough."
                  action={<Button size="sm" onClick={() => setTab("team")}>Go to Team</Button>}
                />
              ) : (
                <ConsoleLink
                  icon={FiRadio}
                  title={isOnAir(event.status) ? "This event is on air" : "Ready for the host"}
                  description={
                    isOnAir(event.status)
                      ? "The host console carries the live controls, stage and health."
                      : "The host opens the studio, checks camera and mic, then goes live. Publishing the event first is what makes go-live legal."
                  }
                  to={`/host/dashboard?event=${event.id}`}
                />
              )}
            </SectionCard>
          </div>
        )}

        {tab === "registration" && (
          <div className="space-y-4">
            <SectionCard title="Registration settings" icon={FiList} action={
              canManage && <Button size="sm" variant="secondary" leftIcon={FiEdit2} onClick={() => setEditOpen(true)}>Edit</Button>
            }>
              <Rows
                items={[
                  ["Registration", event.registration_required ? "Required" : "Open"],
                  ["Limit", event.registration_limit ?? "No limit"],
                  ["Max concurrent participants", event.max_participants ?? "No limit"],
                ]}
              />
            </SectionCard>

            <SectionCard title="Registered attendees" icon={FiUsers} padding="none">
              <EmptyState
                icon={FiUsers}
                title="Attendee registration is not collected yet"
                description={
                  event.registration_required
                    ? "This event is marked as requiring registration, and that setting is stored — but this platform has no registrations table, so nothing is captured and entry is not blocked. The attendee-facing registration flow is the next module."
                    : "Registration is not required for this event, so there is nothing to collect."
                }
              />
            </SectionCard>
          </div>
        )}

        {tab === "access" && <EventAccessLinks event={event} canManage={canManage} />}

        {tab === "recording" && (
          <div className="space-y-4">
            <SectionCard title="Recording settings" icon={FiFilm} action={
              canManage && <Button size="sm" variant="secondary" leftIcon={FiEdit2} onClick={() => setEditOpen(true)}>Edit</Button>
            }>
              <Rows
                items={[
                  ["Recording", onOff(event.recording_enabled)],
                  ["Auto-start", onOff(event.auto_start_recording)],
                  ["Quality", event.stream_quality || "1080p"],
                  ["Captured file", event.has_recording ? "At least one recording exists" : "None yet"],
                ]}
              />
            </SectionCard>
            <SectionCard title="Recordings" icon={FiFilm} padding="none">
              <EmptyState
                icon={FiFilm}
                title={event.recording_enabled ? "No downloadable recording yet" : "Recording is disabled"}
                description={
                  event.recording_enabled
                    ? "Recording rows are written when the host starts a capture, and the host console shows them live. There is no storage bucket configured for LiveKit egress on this deployment, so no downloadable file is produced yet."
                    : "Turn recording on in the event settings before the event starts."
                }
              />
            </SectionCard>
          </div>
        )}

        {tab === "replay" && (
          <SectionCard title="Replay" icon={FiPlay} padding="none">
            <EmptyState
              icon={FiPlay}
              title={
                !event.replay_enabled
                  ? "Replay is switched off"
                  : event.has_replay
                    ? "Replay will appear here"
                    : "No replay available yet"
              }
              description={
                !event.replay_enabled
                  ? "Enable “Publish replay afterwards” in the event settings to offer this event on demand once it ends."
                  : "Replay is enabled, so attendees are told the event will be available afterwards. Serving it needs recording storage and a playback endpoint, neither of which is configured on this deployment."
              }
              action={
                canManage && !event.replay_enabled ? (
                  <Button size="sm" variant="secondary" leftIcon={FiEdit2} onClick={() => setEditOpen(true)}>
                    Enable replay
                  </Button>
                ) : null
              }
            />
          </SectionCard>
        )}

        {tab === "analytics" && (
          <SectionCard title="Analytics" icon={FiBarChart2} padding="none">
            {isOnAir(event.status) ? (
              <ConsoleLink
                icon={FiBarChart2}
                title="Live analytics are in the host console"
                description="Viewer count, peak audience, retention, engagement and device mix are sampled every 15 seconds and streamed to the console over the event socket. They are shown there rather than duplicated here, where they would be stale the moment the page rendered."
                to={`/host/dashboard?event=${event.id}`}
              />
            ) : (
              <EmptyState
                icon={FiBarChart2}
                title="No analytics for this event yet"
                description="Analytics snapshots are written while an event is on air. Once this event has run, its samples become the retention graph in the host console. A per-event historical report is not exposed through this page yet."
              />
            )}
          </SectionCard>
        )}

        {["chat", "polls", "qa"].includes(tab) && (
          <SectionCard
            title={{ chat: "Chat", polls: "Polls", qa: "Q&A" }[tab]}
            icon={{ chat: FiMessageSquare, polls: FiPieChart, qa: FiHelpCircle }[tab]}
            padding="none"
          >
            {!event[{ chat: "chat_enabled", polls: "polls_enabled", qa: "qa_enabled" }[tab]] ? (
              <EmptyState
                icon={FiSlash}
                title={`${{ chat: "Chat", polls: "Polls", qa: "Q&A" }[tab]} is disabled for this event`}
                description="Turn it on in the event settings if the audience should be able to take part."
                action={
                  canManage && (
                    <Button size="sm" variant="secondary" leftIcon={FiEdit2} onClick={() => setEditOpen(true)}>
                      Edit settings
                    </Button>
                  )
                }
              />
            ) : (
              <ConsoleLink
                icon={{ chat: FiMessageSquare, polls: FiPieChart, qa: FiHelpCircle }[tab]}
                title={`${{ chat: "Chat", polls: "Polls", qa: "Q&A" }[tab]} is moderated live`}
                description="Moderation happens over the event socket in the moderator console — approve, pin, delete, launch and route in one round trip. This tab links there instead of showing a snapshot that would be out of date immediately."
                to={`/moderator/dashboard?event=${event.id}`}
                disabled={!isOnAir(event.status)}
                disabledNote={`Enabled for this event. The moderator console opens once the event is on air; ${(team?.moderator?.length || 0) === 0 ? "assign a moderator on the Team tab first." : "the assigned moderators can open it then."}`}
              />
            )}
          </SectionCard>
        )}

        {tab === "audit" && (
          <SectionCard title="Audit logs" icon={FiShield} padding="none">
            <EmptyState
              icon={FiShield}
              title="Audit records are platform-scoped"
              description="Every mutation on this event — status changes, team assignments, viewer links, and every moderator action during the broadcast — is written to the platform audit log with the actor, the target and a metadata blob. That log is readable by the platform super admin; an organization-scoped view of it is not exposed yet."
            />
          </SectionCard>
        )}

        {tab === "settings" && (
          <div className="space-y-4">
            <SectionCard
              title="Event settings"
              icon={FiMic}
              action={canManage && <Button size="sm" variant="secondary" leftIcon={FiEdit2} onClick={() => setEditOpen(true)}>Edit</Button>}
            >
              <Rows
                items={[
                  ["Slug", event.slug ? `/${event.slug}` : "—"],
                  ["Category", event.category || "—"],
                  ["Language", event.language || "—"],
                  ["Location", event.location || "—"],
                  ["Chat", onOff(event.chat_enabled)],
                  ["Q&A", onOff(event.qa_enabled)],
                  ["Polls", onOff(event.polls_enabled)],
                  ["Recording", onOff(event.recording_enabled)],
                  ["Replay", onOff(event.replay_enabled)],
                ]}
              />
            </SectionCard>

            <SectionCard title="Access control" icon={FiGlobe}>
              <Rows
                items={[
                  ["Visibility", visLabel(event.visibility), VISIBILITY_HELP[event.visibility]],
                  [
                    "Passphrase",
                    event.password_protected ? "Set" : "Not set",
                    event.password_protected
                      ? "Attendees must enter it before playback starts. Your organization is exempt."
                      : null,
                  ],
                  ["Active viewer links", event.access_link_count ?? 0],
                ]}
              />
              {canManage && event.password_protected && (
                <div className="mt-4 border-t border-slate-100 pt-4 dark:border-slate-800">
                  <Button variant="secondary" size="sm" leftIcon={FiKey} disabled={mutate.busy} onClick={clearPassword}>
                    Remove passphrase
                  </Button>
                </div>
              )}
            </SectionCard>

            {canManage && (
              <SectionCard title="Danger zone" icon={FiTrash2} accent="rose">
                <p className="text-sm text-slate-600 dark:text-slate-300">
                  Deleting removes this event from your list. Its recordings, chat log and audit
                  trail are retained for compliance.
                </p>
                <div className="mt-4 flex flex-wrap gap-2">
                  <Button
                    variant="danger"
                    size="sm"
                    leftIcon={FiTrash2}
                    disabled={mutate.busy}
                    onClick={() => setConfirm({ kind: "delete" })}
                  >
                    Delete event
                  </Button>
                  <Button variant="secondary" size="sm" leftIcon={FiCopy} disabled={mutate.busy} onClick={duplicate}>
                    Duplicate instead
                  </Button>
                </div>
              </SectionCard>
            )}
          </div>
        )}
      </div>

      <EventFormModal
        open={editOpen}
        event={event}
        onClose={() => setEditOpen(false)}
        onSaved={() => {
          setTeamOverride(null);
          reload();
        }}
      />

      <ConfirmDialog
        open={!!confirm}
        onClose={() => setConfirm(null)}
        onConfirm={() => (confirm.kind === "delete" ? del() : setStatus(confirm.status))}
        busy={mutate.busy}
        title={confirm?.kind === "delete" ? "Delete this event?" : `${confirm?.label} this event?`}
        confirmLabel={confirm?.kind === "delete" ? "Delete" : confirm?.label}
        body={
          confirm?.kind === "delete" ? (
            <>
              <strong className="font-semibold text-slate-800 dark:text-slate-100">
                {event.title || "This event"}
              </strong>{" "}
              will be removed from your events list. Recordings, chat history and audit records
              are kept.
            </>
          ) : confirm?.status === "ended" ? (
            <>
              This ends the broadcast for everyone watching. The recording stops first so the
              file is not left rolling, and the event moves to <strong>Ended</strong>.
            </>
          ) : confirm?.status === "cancelled" ? (
            <>
              Attendees who open the event link will see it as cancelled. This does not delete
              anything, and the event can still be archived afterwards.
            </>
          ) : (
            <>
              Archiving hides this event from the active list. It cannot be archived while it is
              on air.
            </>
          )
        }
      />
    </div>
  );
}
