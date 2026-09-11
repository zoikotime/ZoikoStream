import { useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import {
  FiArrowLeft, FiCalendar, FiClock, FiEye, FiUsers, FiMic, FiMail,
  FiUploadCloud, FiLink, FiTrash2, FiVideo, FiBarChart2, FiUserCheck, FiUserPlus, FiX, FiStar,
  FiShield, FiPhoneOff, FiSend, FiFileText, FiPlus, FiCopy,
} from "react-icons/fi";
import api, { errMsg } from "../../api";
import useApi from "../../hooks/useApi";
import { tzShort } from "../../data/timezones";
import { notify } from "../../ui/Toast";
import { PageSpinner } from "../../ui/Spinner";
import OrganizationErrorState from "../../components/organization/OrganizationErrorState";
import OrganizationEmptyState from "../../components/organization/OrganizationEmptyState";
import SectionCard from "../../components/admin/SectionCard";
import StatCard from "../../components/admin/StatCard";
import { ConsoleButton as Button } from "../../ui/Button";
import Badge from "../../ui/Badge";
import Modal from "../../ui/Modal";
import { Input, Label } from "../../ui/forms";
import { cx, focusRing } from "../../ui/tokens";
import { statusMeta, visLabel, fmtDateTime, fmtDuration } from "../../data/events";
import AssignPeopleModal from "./AssignPeopleModal";
import { ROLE_PATH } from "./roleConfig";
import ContributorInviteModal from "./ContributorInviteModal";
import InviteViewersModal from "./InviteViewersModal";
import EventCommercial from "../../components/organization/EventCommercial";

// No "Moderators" tab: the role is retired and GET/PATCH /events/{id}/moderators no longer
// exist, so the tab could neither load nor save. Hosts now hold the audience-management
// capabilities it used to represent.
const TABS = ["Overview", "Hosts", "Speakers", "Registration", "Feedback", "Billing", "Recording", "Reports", "Analytics", "Settings"];

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

function PeoplePanel({ people, role, onManage, onRemove, onInvite }) {
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
            {onInvite && (
              <button
                type="button"
                onClick={() => onInvite(u)}
                aria-label={`Invite ${u.full_name} to the backstage`}
                title="Invite to backstage"
                className="shrink-0 rounded-lg p-1.5 text-slate-400 transition hover:bg-violet-50 hover:text-violet-600 dark:hover:bg-violet-500/10 dark:hover:text-violet-400"
              >
                <FiSend />
              </button>
            )}
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

// Post-event report (BRD §18.2) — a generated audience + operations snapshot, released
// to the customer/family contact via the same controlled-delivery mechanism as recording
// export (services/report.py, services/delivery.py). Self-contained (own fetch), same
// posture as EventCommercial above: only takes `event`.
function ReleaseReportModal({ report, onClose }) {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [expires, setExpires] = useState("14");
  const [sending, setSending] = useState(false);
  const [created, setCreated] = useState(null);

  const send = async () => {
    setSending(true);
    try {
      const { data } = await api.post(`/organization/reports/${report.id}/release`, {
        recipient_name: name.trim(), recipient_email: email.trim(),
        expires_in_days: expires ? Number(expires) : null,
      });
      setCreated(data);
    } catch (e) {
      notify.error(errMsg(e));
    } finally {
      setSending(false);
    }
  };

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(created.delivery_url);
      notify.success("Report link copied");
    } catch {
      notify.error("Couldn't copy — select the link and copy it manually");
    }
  };

  return (
    <Modal
      open={!!report}
      onClose={onClose}
      title={created ? "Report released" : `Release report v${report.version}`}
      size="md"
      footer={
        created ? (
          <>
            <Button variant="secondary" size="sm" onClick={onClose}>Done</Button>
            <Button size="sm" leftIcon={FiCopy} onClick={copy}>Copy link</Button>
          </>
        ) : (
          <>
            <Button variant="secondary" size="sm" onClick={onClose} disabled={sending}>Cancel</Button>
            <Button size="sm" leftIcon={FiSend} disabled={!name.trim() || !email.trim() || sending} loading={sending} onClick={send}>
              Release
            </Button>
          </>
        )
      }
    >
      {created ? (
        <div className="space-y-3 text-sm text-slate-600 dark:text-slate-300">
          <p>
            An email was sent to <strong className="text-slate-800 dark:text-slate-100">{created.recipient_email}</strong>{" "}
            with this link. It won't be shown again after you close this dialog.
          </p>
          <code className="block break-all rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-xs dark:border-slate-700 dark:bg-white/[0.03]">
            {created.delivery_url}
          </code>
        </div>
      ) : (
        <div className="space-y-4">
          <div>
            <Label variant="console" htmlFor="rep-name">Recipient name</Label>
            <Input variant="console" id="rep-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="Family contact name" />
          </div>
          <div>
            <Label variant="console" htmlFor="rep-email">Recipient email</Label>
            <Input variant="console" id="rep-email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="name@example.com" />
          </div>
          <div>
            <Label variant="console" htmlFor="rep-days">Expires in (days)</Label>
            <Input variant="console" id="rep-days" type="number" min="1" max="365" value={expires} onChange={(e) => setExpires(e.target.value)} />
          </div>
        </div>
      )}
    </Modal>
  );
}

function ReportsPanel({ event }) {
  const { data, loading, error, reload } = useApi(() =>
    api.get(`/organization/events/${event.id}/reports`).then((r) => r.data)
  );
  const reports = data || [];
  const [generating, setGenerating] = useState(false);
  const [releasing, setReleasing] = useState(null);

  const generate = async () => {
    setGenerating(true);
    try {
      await api.post(`/organization/events/${event.id}/reports`);
      notify.success("Report generated");
      reload();
    } catch (e) {
      notify.error(errMsg(e));
    } finally {
      setGenerating(false);
    }
  };

  return (
    <SectionCard
      title="Reports"
      subtitle="Generated audience + operations snapshots — released to the event's customer contact."
      icon={FiFileText}
      action={
        <Button size="sm" leftIcon={FiPlus} loading={generating} onClick={generate}>
          Generate report
        </Button>
      }
    >
      {error ? (
        <OrganizationErrorState error={error} onRetry={reload} title="Couldn't load reports" />
      ) : !loading && reports.length === 0 ? (
        <OrganizationEmptyState
          icon={FiFileText}
          title="No reports yet"
          description="Generate a report once the event has run to see its audience and operations summary."
        />
      ) : (
        <ul className="divide-y divide-slate-100 dark:divide-slate-800">
          {reports.map((r) => (
            <li key={r.id} className="flex items-center justify-between gap-3 py-3">
              <div className="min-w-0">
                <p className="font-medium text-slate-800 dark:text-slate-100">Version {r.version}</p>
                <p className="text-xs text-slate-500 dark:text-slate-400">
                  Generated {fmtDateTime(r.created_at)}
                  {r.released_at ? ` · Released ${fmtDateTime(r.released_at)}` : ""}
                </p>
              </div>
              <div className="flex shrink-0 items-center gap-2">
                {r.released_at && <Badge tone="success" dot>Released</Badge>}
                <Button variant="secondary" size="sm" leftIcon={FiSend} onClick={() => setReleasing(r)}>
                  Release
                </Button>
              </div>
            </li>
          ))}
        </ul>
      )}
      {releasing && <ReleaseReportModal report={releasing} onClose={() => { setReleasing(null); reload(); }} />}
    </SectionCard>
  );
}

export default function EventDetails() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [tab, setTab] = useState("Overview");
  const [busy, setBusy] = useState(false);
  const [manageRole, setManageRole] = useState(null); // "Host" | "Speaker" | null
  const [inviteOpen, setInviteOpen] = useState(false);
  const [inviteSpeaker, setInviteSpeaker] = useState(null); // speaker user object, or null

  const { data, loading, error, reload } = useApi(() =>
    Promise.all([
      api.get(`/events/${id}`).then((r) => r.data),
      api.get(`/events/${id}/hosts`).then((r) => r.data),
      api.get(`/events/${id}/speakers`).then((r) => r.data),
      api.get(`/events/${id}/registrations`).then((r) => r.data),
      // Feedback is a viewer-only signal (the host console no longer collects its own —
      // see pages/host/Dashboard.jsx and services/moderation._feedback_submit), and this
      // is where the organization reads what its audience thought of the event, with the
      // same average-rating rollup the host's own console shows (components/host/
      // HostPanel's Feedback tab).
      api.get(`/events/${id}/feedback`, { params: { role: "viewer" } }).then((r) => r.data),
    ]).then(([event, hosts, speakers, viewers, feedback]) => ({
      event, hosts, speakers, viewers, feedback,
    }))
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

  const { event, hosts, speakers, viewers, feedback } = data;
  const st = statusMeta(event.status);

  const copyLink = () => {
    // /e/:id is a separate, fully-mocked marketing page (fake demo data only) — the real,
    // backend-wired viewer page is /events/:eventId/watch.
    navigator.clipboard?.writeText(`${window.location.origin}/events/${event.id}/watch`);
    notify.success("Event link copied");
  };

  const STATUS_TOAST = {
    published: "Event published",
    ready_to_arm: "Marked ready to arm",
    armed: "Event armed",
  };

  const setStatus = async (status) => {
    setBusy(true);
    try {
      await api.patch(`/events/${event.id}`, { status });
      notify.success(STATUS_TOAST[status] || "Event updated");
      reload();
    } catch (e) {
      // For "armed" specifically, the backend's 400 message already carries the
      // non-waivable readiness gate's actual blocker list (crud.event.status_transition_error)
      // — surfacing it as-is is the operator's "authoritative blocker list", not a generic error.
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

  // The real teardown (stops recording, closes the LiveKit room, notifies every connected
  // viewer/host immediately) — never a raw status PATCH, which would leave the room running
  // and every already-connected viewer stuck on a stale "live" view. Exists specifically as a
  // guaranteed way out of "live" from the org dashboard, not just from the host console.
  const endEvent = async () => {
    if (!window.confirm(`End "${event.title || "this event"}" now? Viewers will be disconnected immediately.`)) return;
    setBusy(true);
    try {
      await api.post(`/events/${event.id}/end`);
      notify.success("Event ended");
      reload();
    } catch (e) {
      notify.error(errMsg(e));
    } finally {
      setBusy(false);
    }
  };

  const canPublish = ["draft", "scheduled"].includes(event.status);
  const canEnd = ["live", "degraded"].includes(event.status);

  // Progressive arm step: one button whose label/target advances the event through the
  // optional v1.1 canonical pre-live chain (published/scheduled/rehearsal -> ready_to_arm ->
  // armed), mirroring how the Publish button above already advances draft -> published.
  // Nothing shows once armed (or beyond) — the banner badge above already reads "Armed".
  const ARM_STEP = {
    published: { label: "Mark Ready to Arm", next: "ready_to_arm" },
    scheduled: { label: "Mark Ready to Arm", next: "ready_to_arm" },
    rehearsal: { label: "Mark Ready to Arm", next: "ready_to_arm" },
    ready_to_arm: { label: "Arm Event", next: "armed" },
  };
  const armStep = ARM_STEP[event.status];

  const ROLE_LIST = { Host: hosts, Speaker: speakers };

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
          {armStep && (
            <Button variant="secondary" size="sm" leftIcon={FiShield} loading={busy} onClick={() => setStatus(armStep.next)}>
              {armStep.label}
            </Button>
          )}
          {canEnd && (
            <Button variant="danger" size="sm" leftIcon={FiPhoneOff} loading={busy} onClick={endEvent}>End Event</Button>
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
          <div className="grid grid-cols-2 gap-4 xl:grid-cols-3">
            <StatCard label="Hosts" value={hosts.length} />
            <StatCard label="Speakers" value={speakers.length} />
            <StatCard label="Duration (min)" value={event.duration_minutes ?? 0} />
          </div>
          <SectionCard title="About this event" icon={FiVideo}>
            <p className="text-sm leading-relaxed text-slate-600 dark:text-slate-300">
              {event.description || "No description provided."}
            </p>
            <div className="mt-4 flex flex-wrap gap-2 text-xs">
              {event.category && <span className="rounded-full bg-slate-100 px-2.5 py-1 font-medium text-slate-600 dark:bg-slate-800 dark:text-slate-300">{event.category}</span>}
              {/* Abbreviation + live offset, matching how the scheduler labels it. `title`
                  keeps the IANA identifier reachable — it is the unambiguous value. */}
              {event.timezone && <span title={event.timezone} className="rounded-full bg-slate-100 px-2.5 py-1 font-medium text-slate-600 dark:bg-slate-800 dark:text-slate-300">{tzShort(event.timezone)}</span>}
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
      {tab === "Speakers" && (
        <PeoplePanel
          people={speakers} role="Speaker"
          onManage={() => setManageRole("Speaker")}
          onRemove={(uid) => removeFromRole("Speaker", uid)}
          onInvite={setInviteSpeaker}
        />
      )}

      {tab === "Registration" && (
        <div className="space-y-4">
          <div className="flex justify-end">
            <Button size="sm" leftIcon={FiMail} onClick={() => setInviteOpen(true)}>
              Invite viewers
            </Button>
          </div>
          {viewers.length === 0 ? (
            <div className="rounded-xl border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900/50">
              <OrganizationEmptyState
                icon={FiUsers}
                title="No viewers yet"
                description={
                  event.visibility === "private"
                    ? "Invite people by email to let them into this private event."
                    : event.registration_required
                      ? "Registered attendees will appear here."
                      : "Invite people by email, or share the event link — registration isn't required."
                }
                action={<Button size="sm" leftIcon={FiMail} onClick={() => setInviteOpen(true)}>Invite viewers</Button>}
              />
            </div>
          ) : (
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {viewers.map((v) => (
                <div key={v.id} className="flex items-center gap-3 rounded-xl border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900/50">
                  <span className="grid h-11 w-11 shrink-0 place-items-center rounded-full bg-violet-100 text-sm font-semibold text-violet-700 dark:bg-violet-500/15 dark:text-violet-300">
                    {initials(v.name)}
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="truncate font-medium text-slate-800 dark:text-slate-100">{v.name}</p>
                    <p className="truncate text-xs text-slate-500 dark:text-slate-400">{v.email}</p>
                  </div>
                  {v.invited_by && <Badge status="info" size="sm">Invited</Badge>}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {tab === "Feedback" && (
        <div className="space-y-4">
          {feedback.length === 0 ? (
            <div className="rounded-xl border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900/50">
              <OrganizationEmptyState
                icon={FiStar}
                title="No viewer feedback yet"
                description="Ratings and comments viewers leave when they exit this event will show up here."
              />
            </div>
          ) : (
            <>
              {(() => {
                const rated = feedback.filter((f) => f.rating != null);
                const avg = rated.length ? rated.reduce((s, f) => s + f.rating, 0) / rated.length : null;
                return avg != null ? (
                  <div className="flex items-center gap-4 rounded-xl border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900/50">
                    <FiStar className="h-6 w-6 shrink-0 fill-amber-400 text-amber-400" aria-hidden="true" />
                    <div>
                      <p className="text-lg font-semibold text-slate-800 dark:text-slate-100">{avg.toFixed(1)} / 5</p>
                      <p className="text-xs text-slate-500 dark:text-slate-400">
                        Average across {feedback.length} submission{feedback.length === 1 ? "" : "s"}
                      </p>
                    </div>
                  </div>
                ) : null;
              })()}
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
                {feedback.map((f) => (
                  <div key={f.id} className="space-y-2 rounded-xl border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900/50">
                    <div className="flex items-center justify-between gap-2">
                      <div className="flex items-center gap-0.5" aria-label={f.rating ? `${f.rating} out of 5 stars` : "No rating"}>
                        {f.rating
                          ? [1, 2, 3, 4, 5].map((n) => (
                              <FiStar
                                key={n}
                                aria-hidden="true"
                                className={cx("h-3.5 w-3.5", n <= f.rating ? "fill-amber-400 text-amber-400" : "text-slate-300 dark:text-slate-700")}
                              />
                            ))
                          : <span className="text-xs text-slate-400 dark:text-slate-500">No rating</span>}
                      </div>
                      <span className="shrink-0 text-[11px] text-slate-400 dark:text-slate-500">
                        {f.created_at ? new Date(f.created_at).toLocaleString() : ""}
                      </span>
                    </div>
                    {f.name && <p className="text-xs font-medium text-slate-500 dark:text-slate-400">{f.name}</p>}
                    {f.comment && <p className="text-sm text-slate-600 dark:text-slate-300">{f.comment}</p>}
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
      )}

      {tab === "Billing" && <EventCommercial event={event} />}

      {tab === "Recording" && (
        <div className="rounded-xl border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900/50">
          <OrganizationEmptyState
            icon={FiVideo}
            title="No recording available"
            description={event.recording_enabled ? "The recording will appear here after the event ends." : "Recording is disabled for this event."}
          />
        </div>
      )}

      {tab === "Reports" && <ReportsPanel event={event} />}

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
              ["Timezone", tzShort(event.timezone) || "—"],
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
            {/* Advanced: publishing from something other than the browser.
                Live Inputs is event-scoped and only matters to someone running an external
                encoder, so it belongs here — beside the event it serves — rather than in the
                global sidebar, where every organizer running a browser broadcast had to
                scroll past it. The link hands this event over so the input is created against
                it without hunting through a list. */}
            <div className="mt-4 border-t border-slate-100 pt-4 dark:border-slate-800">
              <p className="text-xs font-semibold uppercase tracking-wide text-slate-400 dark:text-slate-500">
                Advanced
              </p>
              <div className="mt-2 flex flex-wrap items-center justify-between gap-3">
                <div className="min-w-0">
                  <p className="text-sm font-medium text-slate-800 dark:text-slate-100">External input</p>
                  <p className="text-xs text-slate-500 dark:text-slate-400">
                    RTMP / WHIP — connect OBS, vMix or another encoder instead of publishing from
                    the browser.
                  </p>
                </div>
                <Button variant="secondary" size="sm" href={`/organization/live-inputs?event=${event.id}`}>
                  Set up external input
                </Button>
              </div>
            </div>

          <div className="mt-4 border-t border-slate-100 pt-4 dark:border-slate-800">
            <Button variant="danger" size="sm" leftIcon={FiTrash2} disabled={busy} onClick={del}>Delete Event</Button>
          </div>
        </SectionCard>
      )}

      {manageRole && (
        <AssignPeopleModal
          // Keyed by role so switching Host -> Speaker remounts with a fresh selection
          // instead of carrying the previous tab's checkboxes across.
          key={manageRole}
          open
          onClose={() => setManageRole(null)}
          eventId={event.id}
          role={manageRole}
          assigned={ROLE_LIST[manageRole]}
          onSaved={reload}
        />
      )}

      <InviteViewersModal
        open={inviteOpen}
        onClose={() => setInviteOpen(false)}
        eventId={event.id}
        eventVisibility={event.visibility}
        onInvited={reload}
      />

      <ContributorInviteModal
        open={!!inviteSpeaker}
        onClose={() => setInviteSpeaker(null)}
        eventId={event.id}
        speaker={inviteSpeaker}
        onInvited={reload}
      />
    </div>
  );
}
