import { useRef, useState } from "react";
import {
  FiAlertTriangle, FiCalendar, FiCheck, FiClipboard, FiCpu, FiEdit2, FiFileText, FiLock,
  FiShield, FiUsers, FiVideo,
} from "react-icons/fi";
import api, { errMsg } from "../../../api";
import useApi from "../../../hooks/useApi";
import { notify } from "../../../ui/Toast";
import OrganizationErrorState from "../../../components/organization/OrganizationErrorState";
import EventCommercial from "../../../components/organization/EventCommercial";
import TimezonePicker from "../../../components/organization/TimezonePicker";
import { ConsoleButton as Button } from "../../../ui/Button";
import Badge from "../../../ui/Badge";
import { Input, Label } from "../../../ui/forms";
import { CONSOLE, cx } from "../../../ui/tokens";
import { fmtDuration, visLabel } from "../../../data/events";
import { isValidTimezone, toZonedISO } from "../../../data/timezones";
import { Banner, Footnote, KpiRow, KpiTile, Panel, PanelLink, Tag } from "../../../components/organization/console/ConsoleUI";
import AssignPeopleModal from "../AssignPeopleModal";
import ContributorInviteModal from "../ContributorInviteModal";
import { ROLE_PATH } from "../roleConfig";
import EventSectionHeader from "./EventSectionHeader";
import PeoplePanel from "./PeoplePanel";
import Freshness from "../../../components/organization/Freshness";
import { useEventWorkspace, useHashScroll } from "./workspace";
import { dateBlock, fmtUtc, fmtZoned, fmtZonedShort, zonedParts } from "../../../data/eventTime";

// EVT-02 Preparation (ZST-UX-NAV-001 §6; EVT-02 frame): whether the event may go live and why
// not, the readiness checklist, the schedule, sources & ingest and who owns the handoff —
// then the planning checklist, rehearsals, the editable details, the crew and the commercial
// order. Every figure is read from the server; the only writes are the edit form and the crew
// assignment, through the existing PATCH endpoints the server authorizes.

const humanize = (s) => (s || "").replace(/[_.-]+/g, " ").replace(/^\w/, (c) => c.toUpperCase());

function Checklist() {
  const { readiness, readinessError, readinessLoading, reloadReadiness } = useEventWorkspace();
  if (readinessError) {
    return <OrganizationErrorState error={readinessError} onRetry={reloadReadiness} title="Readiness couldn't be checked" />;
  }
  if (readinessLoading || !readiness) return <p className={cx("text-sm", CONSOLE.faint)}>Checking…</p>;
  const gates = readiness.gates;
  const passed = gates.filter((g) => g.passed).length;
  const open = gates.length - passed;
  return (
    <div className="space-y-3">
      <div className={cx(CONSOLE.inset, "flex flex-wrap items-center gap-4 px-4 py-3")}>
        <p className={cx("text-[14px] font-semibold", CONSOLE.heading)}>
          {passed} of {gates.length} verified{" "}
          <span className={cx("text-[12px] font-normal", CONSOLE.faint)}>
            {open ? `${open} check${open === 1 ? "" : "s"} remain unresolved` : "every check passes"}
          </span>
        </p>
        <div className="ml-auto h-2 w-48 overflow-hidden rounded-full bg-slate-200 dark:bg-white/10" aria-hidden="true">
          <div className="h-full rounded-full bg-gradient-to-r from-violet-600 to-fuchsia-500" style={{ width: `${gates.length ? (100 * passed) / gates.length : 0}%` }} />
        </div>
      </div>
      <ul className={cx("divide-y", CONSOLE.divideY)}>
        {gates.map((g) => (
          <li key={g.key} className="flex items-center justify-between gap-3 py-2.5">
            <span className="flex min-w-0 items-center gap-3">
              <span aria-hidden="true" className={cx(
                "grid h-6 w-6 shrink-0 place-items-center rounded-md text-[12px]",
                g.passed ? "bg-emerald-50 text-emerald-600 dark:bg-emerald-500/15 dark:text-emerald-300"
                  : g.required ? "bg-rose-50 text-rose-600 dark:bg-rose-500/15 dark:text-rose-300"
                    : "bg-amber-50 text-amber-600 dark:bg-amber-500/15 dark:text-amber-300"
              )}>
                {g.passed ? <FiCheck /> : <FiAlertTriangle />}
              </span>
              <span className={cx("truncate text-[13.5px] font-medium", CONSOLE.heading)}>{g.label}</span>
            </span>
            <span className="flex shrink-0 items-center gap-3">
              <span className={cx("hidden text-[11.5px] sm:inline", CONSOLE.faint)}>{g.required ? "Required" : "Recommended"}</span>
              <Tag tone={g.passed ? "green" : g.required ? "rose" : "amber"}>
                {g.passed ? "Passed" : g.required ? "Action required" : "Open"}
              </Tag>
            </span>
          </li>
        ))}
      </ul>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className={cx("text-[12px]", CONSOLE.faint)}>
          Required for this event&rsquo;s impact class ({humanize(readiness.impact)}). Arming is refused on the server while a required check fails.
        </p>
        <Freshness asOf={readiness.evaluated_at} staleAfter={300} onRefresh={reloadReadiness} />
      </div>
    </div>
  );
}

function Schedule({ event, rehearsals }) {
  const block = dateBlock(event.start_time, event.timezone);
  const big = event.start_time ? zonedParts(event.start_time, event.timezone).time : null;
  const milestones = [
    ...(rehearsals || []).filter((r) => r.scheduled_at).map((r) => ({ at: r.scheduled_at, title: "Rehearsal", detail: humanize(r.status) })),
    ...(event.start_time ? [{ at: event.start_time, title: "Scheduled programme start", detail: "Subject to the readiness gate" }] : []),
    ...(event.end_time ? [{ at: event.end_time, title: "Scheduled end", detail: fmtZonedShort(event.end_time, event.timezone) }] : []),
  ].sort((a, b) => new Date(a.at) - new Date(b.at));
  return (
    <Panel title="Schedule & production milestones" subtitle="Event-local time with its UTC equivalent" action={<PanelLink to="#details">Edit schedule</PanelLink>}>
      {block ? (
        <div className="flex items-center gap-5 rounded-xl bg-gradient-to-r from-[#141c45] to-[#2a1b55] px-5 py-4 text-white">
          <div>
            <p className="text-[32px] font-bold leading-none">{big}</p>
            <p className="mt-1 text-[11px] text-white/70">{event.timezone || "Local time"}</p>
          </div>
          <div className="border-l border-white/20 pl-5">
            <p className="text-[15px] font-semibold">{block.date}</p>
            <p className="text-[12px] text-white/70">{block.utc}</p>
          </div>
        </div>
      ) : (
        <p className={cx(CONSOLE.inset, "px-4 py-3 text-sm", CONSOLE.muted)}>No start time is set. Add one in Details &amp; schedule.</p>
      )}
      {milestones.length > 0 && (
        <ol className={cx("mt-3 divide-y", CONSOLE.divideY)}>
          {milestones.map((m) => (
            <li key={`${m.title}-${m.at}`} className="grid grid-cols-[4rem_1fr] items-start gap-3 py-2.5">
              <span className={cx("text-[12.5px] font-semibold", CONSOLE.muted)}>{zonedParts(m.at, event.timezone).time}</span>
              <span className="flex gap-2.5">
                <span aria-hidden="true" className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-violet-500" />
                <span>
                  <span className={cx("block text-[13px] font-semibold", CONSOLE.heading)}>{m.title}</span>
                  <span className={cx("block text-[11.5px]", CONSOLE.faint)}>
                    {new Date(m.at).toLocaleDateString("en-GB", { day: "numeric", month: "short" })} · {m.detail}
                  </span>
                </span>
              </span>
            </li>
          ))}
        </ol>
      )}
    </Panel>
  );
}

function Sources({ eventId, live }) {
  const inputs = live.data?.inputs || [];
  const card = (title, icon, input, empty) => {
    const Icon = icon;
    return (
      <div className={cx(CONSOLE.inset, "p-4")}>
        <p className={cx("flex items-center gap-2 text-[13px] font-semibold", CONSOLE.heading)}>
          <Icon aria-hidden="true" /> {title}
        </p>
        {input ? (
          <>
            <p className={cx("mt-2 flex items-center gap-1.5 text-[12px] font-semibold",
              input.provisioning_failed ? "text-rose-600 dark:text-rose-300" : input.signal_state === "healthy" ? "text-emerald-600 dark:text-emerald-300" : "text-amber-600 dark:text-amber-300")}>
              <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-current" />
              {input.provisioning_failed ? "Not provisioned" : `${humanize(input.state)} · signal ${input.signal_state}`}
            </p>
            <p className={cx("text-[12px]", CONSOLE.faint)}>{input.title} · {input.input_type.toUpperCase()}</p>
          </>
        ) : (
          <p className={cx("mt-2 text-[12px]", CONSOLE.faint)}>{empty}</p>
        )}
      </div>
    );
  };
  return (
    <Panel
      id="sources"
      title="Sources & ingest preparation"
      subtitle="Safe configuration summary · no encoder keys or destination secrets"
      action={<PanelLink to={`/organization/live-inputs?event=${eventId}`}>Set up external input</PanelLink>}
    >
      {live.error && !live.data ? (
        <OrganizationErrorState error={live.error} onRetry={live.reload} title="Couldn't read this event's inputs" />
      ) : !live.data ? (
        <p className={cx("text-sm", CONSOLE.faint)}>Loading…</p>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2">
          {card("Primary contribution source", FiVideo, inputs[0], "Browser broadcast from the Producer Console — no external encoder")}
          {card("Backup source path", FiShield, inputs[1], "No backup input recorded")}
        </div>
      )}
      <Footnote><FiLock aria-hidden="true" className="mr-1 inline" />Stream keys and endpoints are protected: they are shown only in the input&rsquo;s own detail sheet, on request.</Footnote>
    </Panel>
  );
}

function Handoff({ team, readiness, rehearsals }) {
  const hosts = team.data?.hosts || [];
  const lastRehearsal = (rehearsals || [])[0];
  const tile = (icon, label, value, tone) => {
    const Icon = icon;
    return (
      <div className={cx(CONSOLE.inset, "p-3.5")}>
        <p className={cx("flex items-center gap-1.5 text-[11.5px]", CONSOLE.faint)}><Icon aria-hidden="true" /> {label}</p>
        <p className={cx("mt-1 text-[14px] font-semibold", tone === "warn" ? "text-amber-700 dark:text-amber-300" : CONSOLE.heading)}>{value}</p>
      </div>
    );
  };
  return (
    <Panel title="Ownership & production handoff" subtitle="Scoped accountabilities on this event" action={<PanelLink to="#team">Event team</PanelLink>}>
      <div className="grid gap-3 sm:grid-cols-2">
        {tile(FiUsers, "Production owner", team.data ? (hosts.length ? `Assigned · ${hosts[0].full_name}` : "Not assigned") : "…", hosts.length ? null : "warn")}
        {tile(FiCpu, "Technical verification", lastRehearsal ? humanize(lastRehearsal.status) : "No rehearsal recorded", lastRehearsal ? null : "warn")}
        {tile(FiShield, "Viewer-access sign-off", "Approval-gated", "warn")}
        {tile(FiFileText, "Last evidence update", readiness?.evaluated_at ? fmtZonedShort(readiness.evaluated_at) : "—")}
      </div>
    </Panel>
  );
}

function Planning({ eventId, timezone, rehearsals }) {
  const planning = useApi(() => api.get(`/events/${eventId}/planning`).then((r) => r.data));
  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <Panel title="Planning checklist" subtitle="Run of show, content and logistics owners.">
        {planning.error ? (
          <OrganizationErrorState error={planning.error} onRetry={planning.reload} title="Couldn't load planning" />
        ) : !planning.data ? (
          <p className={cx("text-sm", CONSOLE.faint)}>Loading…</p>
        ) : planning.data.length === 0 ? (
          <p className={cx("text-sm", CONSOLE.muted)}>No planning items apply to this event.</p>
        ) : (
          <ul className={cx("divide-y", CONSOLE.divideY)}>
            {planning.data.map((p) => (
              <li key={p.category} className="flex items-center justify-between gap-3 py-2.5 text-sm">
                <span className={CONSOLE.body}>{humanize(p.category)}</span>
                <span className="flex items-center gap-2">
                  {p.blocking && <Badge tone="danger" size="sm">Blocking</Badge>}
                  <Badge tone={p.status === "complete" || p.status === "completed" ? "success" : "neutral"} size="sm">
                    {humanize(p.status)}
                  </Badge>
                </span>
              </li>
            ))}
          </ul>
        )}
      </Panel>
      <Panel title="Rehearsals" subtitle="Scheduled rehearsals and their outcomes.">
        {rehearsals.error ? (
          <OrganizationErrorState error={rehearsals.error} onRetry={rehearsals.reload} title="Couldn't load rehearsals" />
        ) : !rehearsals.data ? (
          <p className={cx("text-sm", CONSOLE.faint)}>Loading…</p>
        ) : rehearsals.data.length === 0 ? (
          <p className={cx("text-sm", CONSOLE.muted)}>No rehearsal has been scheduled.</p>
        ) : (
          <ul className={cx("divide-y", CONSOLE.divideY)}>
            {rehearsals.data.map((r) => (
              <li key={r.id} className="flex items-center justify-between gap-3 py-2.5 text-sm">
                <span className={CONSOLE.body}>{fmtZonedShort(r.scheduled_at, r.timezone || timezone)}</span>
                <Badge tone={r.status === "validated" ? "success" : r.repeat_required ? "warning" : "neutral"} size="sm">
                  {humanize(r.status)}
                </Badge>
              </li>
            ))}
          </ul>
        )}
      </Panel>
    </div>
  );
}

// Details & schedule. Wall-clock fields are in the EVENT's zone, converted to an absolute
// instant on save (data/timezones), exactly as the create dialog does — so what you type is
// what the event says, whatever zone this browser is in.
function DetailsForm({ event, onDone }) {
  const start = zonedParts(event.start_time, event.timezone);
  const end = zonedParts(event.end_time, event.timezone);
  const [form, setForm] = useState({
    title: event.title || "",
    short_description: event.short_description || "",
    description: event.description || "",
    date: start.date,
    start: start.time,
    end: end.time,
    timezone: event.timezone || "UTC",
    recording_enabled: Boolean(event.recording_enabled),
    chat_enabled: Boolean(event.chat_enabled),
    qa_enabled: Boolean(event.qa_enabled),
  });
  const [saving, setSaving] = useState(false);
  const inFlight = useRef(false);
  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e?.target ? (e.target.type === "checkbox" ? e.target.checked : e.target.value) : e }));

  const save = async (e) => {
    e.preventDefault();
    if (inFlight.current) return;
    if (!form.title.trim()) return notify.error("A title is required.");
    if (!isValidTimezone(form.timezone)) return notify.error("Select a valid timezone.");
    const start_time = form.date && form.start ? toZonedISO(form.date, form.start, form.timezone) : null;
    const end_time = form.date && form.end ? toZonedISO(form.date, form.end, form.timezone) : null;
    if (start_time && end_time && new Date(end_time) <= new Date(start_time)) {
      return notify.error("The end time must be after the start time.");
    }
    const body = {
      title: form.title.trim(),
      short_description: form.short_description.trim() || null,
      description: form.description.trim() || null,
      timezone: form.timezone,
      recording_enabled: form.recording_enabled,
      chat_enabled: form.chat_enabled,
      qa_enabled: form.qa_enabled,
    };
    if (start_time) body.start_time = start_time;
    if (end_time) body.end_time = end_time;
    inFlight.current = true;
    setSaving(true);
    try {
      const res = await api.patch(`/events/${event.id}`, body);
      const ref = res?.headers?.["x-request-id"];
      notify.success(ref ? `Event saved · Ref ${ref}` : "Event saved");
      onDone(true);
    } catch (err) {
      notify.error(errMsg(err));
    } finally {
      inFlight.current = false;
      setSaving(false);
    }
  };

  const toggles = [
    ["recording_enabled", "Record this event"],
    ["chat_enabled", "Chat"],
    ["qa_enabled", "Q&A"],
  ];

  return (
    <form onSubmit={save} className="space-y-4" aria-label="Event details">
      <div>
        <Label variant="console" htmlFor="prep-title">Title</Label>
        <Input variant="console" id="prep-title" value={form.title} onChange={set("title")} maxLength={200} required />
      </div>
      <div>
        <Label variant="console" htmlFor="prep-short">Short description</Label>
        <Input variant="console" id="prep-short" value={form.short_description} onChange={set("short_description")} maxLength={300} />
      </div>
      <div>
        <Label variant="console" htmlFor="prep-desc">Description</Label>
        <textarea id="prep-desc" rows={4} value={form.description} onChange={set("description")} className={CONSOLE.textarea} />
      </div>
      <div className="grid gap-4 sm:grid-cols-3">
        <div>
          <Label variant="console" htmlFor="prep-date">Date</Label>
          <Input variant="console" id="prep-date" type="date" value={form.date} onChange={set("date")} />
        </div>
        <div>
          <Label variant="console" htmlFor="prep-start">Starts</Label>
          <Input variant="console" id="prep-start" type="time" value={form.start} onChange={set("start")} />
        </div>
        <div>
          <Label variant="console" htmlFor="prep-end">Ends</Label>
          <Input variant="console" id="prep-end" type="time" value={form.end} onChange={set("end")} />
        </div>
      </div>
      <div>
        <Label variant="console" htmlFor="prep-tz">Timezone</Label>
        <TimezonePicker id="prep-tz" value={form.timezone} onChange={(tz) => setForm((f) => ({ ...f, timezone: tz }))} />
      </div>
      <fieldset className="flex flex-wrap gap-2">
        <legend className={cx("mb-1 text-[12px] font-medium", CONSOLE.muted)}>Features</legend>
        {toggles.map(([key, label]) => (
          <label key={key} className={CONSOLE.checkboxRow}>
            <input type="checkbox" className={CONSOLE.checkbox} checked={form[key]} onChange={set(key)} />
            <span className={cx("text-sm", CONSOLE.body)}>{label}</span>
          </label>
        ))}
      </fieldset>
      <div className="flex justify-end gap-2">
        <Button type="button" variant="secondary" size="sm" disabled={saving} onClick={() => onDone(false)}>Cancel</Button>
        <Button type="submit" size="sm" loading={saving} disabled={saving}>Save changes</Button>
      </div>
    </form>
  );
}

function Details() {
  const { event, reload } = useEventWorkspace();
  const [editing, setEditing] = useState(false);
  const rows = [
    ["Title", event.title || "Untitled event"],
    ["Starts (event time)", fmtZoned(event.start_time, event.timezone)],
    ["Starts (UTC)", fmtUtc(event.start_time)],
    ["Duration", fmtDuration(event.duration_minutes)],
    ["Timezone", event.timezone || "—"],
    ["Category", event.category || "—"],
    ["Visibility", visLabel(event.visibility)],
    ["Recording", event.recording_enabled ? "On" : "Off"],
    ["Chat", event.chat_enabled ? "On" : "Off"],
    ["Q&A", event.qa_enabled ? "On" : "Off"],
  ];
  return (
    <Panel
      id="details"
      title="Details & schedule"
      subtitle="Title, schedule and features. Saved changes are recorded in Activity & Audit."
      action={!editing && (
        <Button variant="secondary" size="sm" leftIcon={FiEdit2} onClick={() => setEditing(true)}>Edit</Button>
      )}
    >
      {editing ? (
        <DetailsForm
          event={event}
          onDone={(saved) => {
            setEditing(false);
            if (saved) reload();
          }}
        />
      ) : (
        <dl className={cx("divide-y text-sm", CONSOLE.divideY)}>
          {rows.map(([k, v]) => (
            <div key={k} className="flex justify-between gap-4 py-2.5">
              <dt className={CONSOLE.muted}>{k}</dt>
              <dd className={cx("text-right font-medium", CONSOLE.heading)}>{v}</dd>
            </div>
          ))}
        </dl>
      )}
    </Panel>
  );
}

function Team({ eventId, team }) {
  const [manageRole, setManageRole] = useState(null);
  const [inviteSpeaker, setInviteSpeaker] = useState(null);
  const inFlight = useRef(false);

  const lists = { Host: team.data?.hosts || [], Speaker: team.data?.speakers || [] };
  const remove = async (role, userId) => {
    if (inFlight.current) return;
    inFlight.current = true;
    try {
      const remaining = lists[role].filter((u) => u.id !== userId).map((u) => u.id);
      await api.patch(`/events/${eventId}/${ROLE_PATH[role]}`, { user_ids: remaining });
      notify.success(`${role} removed`);
      team.reload();
    } catch (e) {
      notify.error(errMsg(e));
    } finally {
      inFlight.current = false;
    }
  };

  return (
    <>
      <Panel id="team" title="Event team" subtitle="Hosts run the broadcast; speakers join from the backstage.">
        {team.error ? (
          <OrganizationErrorState error={team.error} onRetry={team.reload} title="Couldn't load the event team" />
        ) : !team.data ? (
          <p className={cx("text-sm", CONSOLE.faint)}>Loading…</p>
        ) : (
          <div className="space-y-6">
            <div>
              <h3 className={cx("mb-3 text-sm font-semibold", CONSOLE.heading)}>Hosts</h3>
              <PeoplePanel people={lists.Host} role="Host" onManage={() => setManageRole("Host")} onRemove={(id) => remove("Host", id)} />
            </div>
            <div>
              <h3 className={cx("mb-3 text-sm font-semibold", CONSOLE.heading)}>Speakers</h3>
              <PeoplePanel
                people={lists.Speaker} role="Speaker"
                onManage={() => setManageRole("Speaker")}
                onRemove={(id) => remove("Speaker", id)}
                onInvite={setInviteSpeaker}
              />
            </div>
          </div>
        )}
      </Panel>
      {manageRole && (
        <AssignPeopleModal
          key={manageRole}
          open
          onClose={() => setManageRole(null)}
          eventId={eventId}
          role={manageRole}
          assigned={lists[manageRole]}
          onSaved={team.reload}
        />
      )}
      <ContributorInviteModal
        open={!!inviteSpeaker}
        onClose={() => setInviteSpeaker(null)}
        eventId={eventId}
        speaker={inviteSpeaker}
        onInvited={team.reload}
      />
    </>
  );
}

export default function EventPreparation() {
  const { event, eventId, readiness, readinessError } = useEventWorkspace();
  const live = useApi(() => api.get(`/organization/events/${eventId}/live-status`).then((r) => r.data));
  const rehearsals = useApi(() => api.get(`/events/${eventId}/rehearsals`).then((r) => r.data));
  const team = useApi(() =>
    Promise.all([
      api.get(`/events/${eventId}/hosts`).then((r) => r.data),
      api.get(`/events/${eventId}/speakers`).then((r) => r.data),
    ]).then(([hosts, speakers]) => ({ hosts, speakers }))
  );
  // Scroll to a #hash once these have answered (loaded or failed), as Live Control does: earlier,
  // the content they add above a section pushed it down after the scroll — Back to #details
  // landed 80px below its place. data/error persist across reloads, so a refresh does not re-scroll.
  const settled = (q) => Boolean(q.data) || Boolean(q.error);
  useHashScroll({ ready: settled(live) && settled(rehearsals) && settled(team) });

  const gates = readiness?.gates || [];
  const passed = gates.filter((g) => g.passed).length;
  const blockers = gates.filter((g) => g.required && !g.passed);
  const open = gates.filter((g) => !g.required && !g.passed);
  const inputs = live.data?.inputs || [];

  return (
    <div id="hub" data-section="hub" className="scroll-mt-24 space-y-6">
      <EventSectionHeader
        icon={FiClipboard}
        eyebrow="Event workspace / 02"
        title="Preparation"
        subtitle="Coordinate event setup, source verification, crew readiness and the handoff to live operations."
        actions={
          <>
            <Button variant="secondary" size="sm" leftIcon={FiFileText} href="#details">Event details</Button>
            <Button size="sm" leftIcon={FiClipboard} href="#readiness">Review readiness</Button>
          </>
        }
      />

      {readiness && blockers.length > 0 && (
        <Banner tone="warning" title={`Go-live not authorized · ${blockers.length} required check${blockers.length === 1 ? "" : "s"} unresolved`}
          aside={<PanelLink to="#readiness">Open readiness review</PanelLink>} role="status">
          {blockers.map((g) => g.label).join("; ")}. No launch control is exposed here.
        </Banner>
      )}
      {readiness && !blockers.length && open.length > 0 && (
        <Banner tone="info" title={`${open.length} recommended check${open.length === 1 ? "" : "s"} open`}
          aside={<PanelLink to="#readiness">Review checks</PanelLink>}>
          Required checks pass, so arming is not blocked. {open.map((g) => g.label).join("; ")}.
        </Banner>
      )}

      <KpiRow>
        <KpiTile icon={FiClipboard} tone="violet" label="Readiness evidence"
          value={readiness ? `${passed}/${gates.length}` : readinessError ? null : "…"} caption="Checks passing" />
        <KpiTile icon={FiAlertTriangle} tone="amber" label="Critical blockers"
          value={readiness ? blockers.length : readinessError ? null : "…"}
          caption={blockers.length ? "Must be resolved before handoff" : "No required check failing"} />
        <KpiTile icon={FiCpu} tone="sky" label="Primary ingest" emphasis="text"
          value={!live.data ? (live.error ? null : "…") : inputs.length ? (inputs[0].signal_state === "healthy" ? "Signal healthy" : `Signal ${inputs[0].signal_state}`) : "Browser"}
          caption={inputs.length ? `${inputs[0].input_type.toUpperCase()} · ${humanize(inputs[0].state)}` : "Producer Console publishing"} />
        <KpiTile icon={FiCalendar} tone="green" label="Event schedule" emphasis="text"
          value={event.start_time ? "Confirmed" : "Not set"}
          caption={event.start_time ? `${zonedParts(event.start_time, event.timezone).date} · ${event.timezone || "local time"}` : "Add a start time below"} />
      </KpiRow>

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1.35fr)_minmax(0,1fr)]">
        <Panel id="readiness" title="Go-live readiness checklist" subtitle="Evaluated by the server from the event's own configuration and lifecycle.">
          <Checklist />
        </Panel>
        <Schedule event={event} rehearsals={rehearsals.data} />
      </div>

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1.35fr)_minmax(0,1fr)]">
        <Sources eventId={eventId} live={live} />
        <Handoff team={team} readiness={readiness} rehearsals={rehearsals.data} />
      </div>

      <Planning eventId={eventId} timezone={event.timezone} rehearsals={rehearsals} />
      <Details />
      <Team eventId={eventId} team={team} />
      <Panel title="Commercial & billing" subtitle="This event's order, quote and commercial readiness.">
        <EventCommercial event={event} />
      </Panel>
    </div>
  );
}
