import { useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import {
  FiArrowLeft, FiCalendar, FiClock, FiEye, FiUsers, FiMic,
  FiUploadCloud, FiLink, FiTrash2, FiVideo, FiBarChart2, FiUserCheck, FiUserPlus, FiX,
} from "react-icons/fi";
import api, { errMsg } from "../../api";
import useApi from "../../hooks/useApi";
import { notify } from "../../ui/Toast";
import { PageSpinner } from "../../ui/Spinner";
import OrganizationErrorState from "../../components/organization/OrganizationErrorState";
import OrganizationEmptyState from "../../components/organization/OrganizationEmptyState";
import SectionCard from "../../components/admin/SectionCard";
import StatCard from "../../components/admin/StatCard";
import { ConsoleButton as Button } from "../../ui/Button";
import Badge from "../../ui/Badge";
import { cx, focusRing } from "../../ui/tokens";
import { statusMeta, visLabel, fmtDateTime, fmtDuration } from "../../data/events";
import AssignPeopleModal, { ROLE_PATH } from "./AssignPeopleModal";

const TABS = ["Overview", "Hosts", "Moderators", "Speakers", "Registration", "Recording", "Analytics", "Settings"];

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

function PeoplePanel({ people, role, onManage, onRemove }) {
  if (!people.length)
    return (
      <OrganizationEmptyState
        icon={FiUserCheck}
        title={`No ${role.toLowerCase()}s assigned`}
        description={`Assign ${role.toLowerCase()}s to this event from your organization's members.`}
        action={
          <Button size="sm" leftIcon={FiUserPlus} onClick={onManage}>
            Add {role}
          </Button>
        }
      />
    );
  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <Button variant="secondary" size="sm" leftIcon={FiUserPlus} onClick={onManage}>
          Manage {role}s
        </Button>
      </div>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {people.map((u) => (
          <div key={u.id} className="flex items-center gap-3 rounded-xl border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900/50">
            <span className="grid h-11 w-11 shrink-0 place-items-center rounded-full bg-violet-100 text-sm font-semibold text-violet-700 dark:bg-violet-500/15 dark:text-violet-300">
              {initials(u.full_name)}
            </span>
            <div className="min-w-0 flex-1">
              <p className="truncate font-medium text-slate-800 dark:text-slate-100">{u.full_name}</p>
              <p className="truncate text-xs text-slate-500 dark:text-slate-400">{u.email}</p>
            </div>
            <button
              type="button"
              onClick={() => onRemove(u.id)}
              aria-label={`Remove ${u.full_name}`}
              className="shrink-0 rounded-lg p-1.5 text-slate-400 transition hover:bg-rose-50 hover:text-rose-600 dark:hover:bg-rose-500/10 dark:hover:text-rose-400"
            >
              <FiX />
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function EventDetails() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [tab, setTab] = useState("Overview");
  const [busy, setBusy] = useState(false);
  const [manageRole, setManageRole] = useState(null); // "Host" | "Moderator" | "Speaker" | null

  const { data, loading, error, reload } = useApi(() =>
    Promise.all([
      api.get(`/events/${id}`).then((r) => r.data),
      api.get(`/events/${id}/hosts`).then((r) => r.data),
      api.get(`/events/${id}/moderators`).then((r) => r.data),
      api.get(`/events/${id}/speakers`).then((r) => r.data),
    ]).then(([event, hosts, moderators, speakers]) => ({ event, hosts, moderators, speakers }))
  );

  const back = (
    <button
      onClick={() => navigate("/organization/events")}
      className={cx("inline-flex items-center gap-1.5 rounded text-sm font-medium text-slate-500 hover:text-slate-800 dark:text-slate-400 dark:hover:text-slate-200", focusRing)}
    >
      <FiArrowLeft /> Back to Events
    </button>
  );

  if (loading) return <div className="space-y-4">{back}<PageSpinner label="Loading event…" /></div>;
  if (error) return <div className="space-y-4">{back}<OrganizationErrorState error={error} onRetry={reload} title="Couldn't load this event" /></div>;

  const { event, hosts, moderators, speakers } = data;
  const st = statusMeta(event.status);

  const copyLink = () => {
    navigator.clipboard?.writeText(`${window.location.origin}/e/${event.slug || event.id}`);
    notify.success("Event link copied");
  };

  const setStatus = async (status) => {
    setBusy(true);
    try {
      await api.patch(`/events/${event.id}`, { status });
      notify.success(status === "published" ? "Event published" : "Event updated");
      reload();
    } catch (e) {
      notify.error(errMsg(e));
    } finally {
      setBusy(false);
    }
  };

  const del = async () => {
    if (!window.confirm(`Delete "${event.title || "this event"}"? This cannot be undone.`)) return;
    setBusy(true);
    try {
      await api.delete(`/events/${event.id}`);
      notify.success("Event deleted");
      navigate("/organization/events");
    } catch (e) {
      notify.error(errMsg(e));
      setBusy(false);
    }
  };

  const canPublish = ["draft", "scheduled"].includes(event.status);

  const ROLE_LIST = { Host: hosts, Moderator: moderators, Speaker: speakers };

  const removeFromRole = async (role, userId) => {
    const remaining = ROLE_LIST[role].filter((u) => u.id !== userId).map((u) => u.id);
    try {
      await api.patch(`/events/${event.id}/${ROLE_PATH[role]}`, { user_ids: remaining });
      notify.success(`${role} removed`);
      reload();
    } catch (e) {
      notify.error(errMsg(e));
    }
  };

  return (
    <div className="space-y-6">
      {back}

      {/* Banner */}
      <div className="relative overflow-hidden rounded-2xl bg-gradient-to-br from-violet-600 to-slate-900 p-6 sm:p-8">
        <div className="absolute inset-0 bg-gradient-to-tr from-slate-900/70 via-slate-900/30 to-transparent" />
        <div className="relative flex flex-col gap-2">
          <Badge tone={st.tone} dot={st.pulse} className="w-fit">{st.label}</Badge>
          <h1 className="text-2xl font-bold tracking-tight text-white sm:text-3xl">{event.title || "Untitled event"}</h1>
          {(event.short_description || event.description) && (
            <p className="max-w-2xl text-sm text-white/80">{event.short_description || event.description}</p>
          )}
        </div>
      </div>

      {/* Meta + quick actions */}
      <div className="rounded-xl border border-slate-200 bg-white p-5 dark:border-slate-800 dark:bg-slate-900/50">
        <div className="grid grid-cols-2 gap-5 lg:grid-cols-4">
          <Meta icon={FiCalendar} label="Starts">{fmtDateTime(event.start_time)}</Meta>
          <Meta icon={FiClock} label="Duration">{fmtDuration(event.duration_minutes)}</Meta>
          <Meta icon={FiEye} label="Visibility">{visLabel(event.visibility)}</Meta>
          <Meta icon={FiUsers} label="Registration">{event.registration_required ? `Required${event.registration_limit ? ` · limit ${event.registration_limit}` : ""}` : "Open"}</Meta>
        </div>
        <div className="mt-6 flex flex-wrap gap-2 border-t border-slate-100 pt-5 dark:border-slate-800">
          {canPublish && (
            <Button variant="primary" size="sm" leftIcon={FiUploadCloud} loading={busy} onClick={() => setStatus("published")}>Publish</Button>
          )}
          <Button variant="secondary" size="sm" leftIcon={FiLink} onClick={copyLink}>Copy Link</Button>
          <Button variant="danger" size="sm" leftIcon={FiTrash2} disabled={busy} onClick={del}>Delete</Button>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 overflow-x-auto border-b border-slate-200 dark:border-slate-800">
        {TABS.map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={cx(
              "-mb-px whitespace-nowrap border-b-2 px-4 py-2.5 text-sm font-medium transition",
              tab === t
                ? "border-violet-500 text-violet-600 dark:text-violet-400"
                : "border-transparent text-slate-500 hover:text-slate-800 dark:text-slate-400 dark:hover:text-slate-200"
            )}
          >
            {t}
          </button>
        ))}
      </div>

      {tab === "Overview" && (
        <div className="space-y-6">
          <div className="grid grid-cols-2 gap-4 xl:grid-cols-4">
            <StatCard label="Hosts" value={hosts.length} />
            <StatCard label="Moderators" value={moderators.length} />
            <StatCard label="Speakers" value={speakers.length} />
            <StatCard label="Duration (min)" value={event.duration_minutes ?? 0} />
          </div>
          <SectionCard title="About this event" icon={FiVideo}>
            <p className="text-sm leading-relaxed text-slate-600 dark:text-slate-300">
              {event.description || "No description provided."}
            </p>
            <div className="mt-4 flex flex-wrap gap-2 text-xs">
              {event.category && <span className="rounded-full bg-slate-100 px-2.5 py-1 font-medium text-slate-600 dark:bg-slate-800 dark:text-slate-300">{event.category}</span>}
              {event.timezone && <span className="rounded-full bg-slate-100 px-2.5 py-1 font-medium text-slate-600 dark:bg-slate-800 dark:text-slate-300">{event.timezone}</span>}
              {(event.tags || []).map((t) => (
                <span key={t} className="rounded-full bg-slate-100 px-2.5 py-1 font-medium text-slate-600 dark:bg-slate-800 dark:text-slate-300">#{t}</span>
              ))}
            </div>
          </SectionCard>
        </div>
      )}

      {tab === "Hosts" && (
        <PeoplePanel people={hosts} role="Host" onManage={() => setManageRole("Host")} onRemove={(uid) => removeFromRole("Host", uid)} />
      )}
      {tab === "Moderators" && (
        <PeoplePanel people={moderators} role="Moderator" onManage={() => setManageRole("Moderator")} onRemove={(uid) => removeFromRole("Moderator", uid)} />
      )}
      {tab === "Speakers" && (
        <PeoplePanel people={speakers} role="Speaker" onManage={() => setManageRole("Speaker")} onRemove={(uid) => removeFromRole("Speaker", uid)} />
      )}

      {tab === "Registration" && (
        <div className="rounded-xl border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900/50">
          <OrganizationEmptyState
            icon={FiUsers}
            title="No registrations yet"
            description={event.registration_required ? "Registered attendees will appear here." : "Registration isn't required for this event."}
          />
        </div>
      )}

      {tab === "Recording" && (
        <div className="rounded-xl border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900/50">
          <OrganizationEmptyState
            icon={FiVideo}
            title="No recording available"
            description={event.recording_enabled ? "The recording will appear here after the event ends." : "Recording is disabled for this event."}
          />
        </div>
      )}

      {tab === "Analytics" && (
        <div className="rounded-xl border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900/50">
          <OrganizationEmptyState
            icon={FiBarChart2}
            title="Analytics not available yet"
            description="Viewer and engagement metrics appear here once the event has run."
          />
        </div>
      )}

      {tab === "Settings" && (
        <SectionCard title="Event Settings" icon={FiMic}>
          <dl className="divide-y divide-slate-100 text-sm dark:divide-slate-800">
            {[
              ["Status", st.label],
              ["Category", event.category || "—"],
              ["Visibility", visLabel(event.visibility)],
              ["Registration", event.registration_required ? "Required" : "Open"],
              ["Timezone", event.timezone || "—"],
              ["Chat", event.chat_enabled ? "On" : "Off"],
              ["Q&A", event.qa_enabled ? "On" : "Off"],
              ["Recording", event.recording_enabled ? "On" : "Off"],
            ].map(([k, v]) => (
              <div key={k} className="flex justify-between py-3">
                <dt className="text-slate-500 dark:text-slate-400">{k}</dt>
                <dd className="font-medium text-slate-800 dark:text-slate-100">{v}</dd>
              </div>
            ))}
          </dl>
          <div className="mt-4 border-t border-slate-100 pt-4 dark:border-slate-800">
            <Button variant="danger" size="sm" leftIcon={FiTrash2} disabled={busy} onClick={del}>Delete Event</Button>
          </div>
        </SectionCard>
      )}

      {manageRole && (
        <AssignPeopleModal
          open
          onClose={() => setManageRole(null)}
          eventId={event.id}
          role={manageRole}
          assigned={ROLE_LIST[manageRole]}
          onSaved={reload}
        />
      )}
    </div>
  );
}
