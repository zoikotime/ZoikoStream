import { useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import {
  FiArrowLeft,
  FiCalendar,
  FiClock,
  FiUser,
  FiEye,
  FiUploadCloud,
  FiLink,
  FiTrash2,
  FiXCircle,
  FiUserPlus,
  FiUsers,
  FiDownload,
  FiVideo,
  FiLoader,
} from "react-icons/fi";
import { cx } from "../../ui/tokens";
import Card from "../../ui/Card";
import Button from "../../ui/Button";
import { notify } from "../../ui/Toast";
import api, { errMsg } from "../../api";
import { STATUS_LABEL, STATUS_PILL, VISIBILITY_LABEL, VIS_PILL, fmtDate } from "../../data/events";

const TABS = ["Overview", "Team", "Registration", "Recording", "Analytics", "Settings"];

const label = "text-xs font-medium uppercase tracking-wide text-slate-400";

function Meta({ icon: Icon, label: l, children }) {
  return (
    <div className="flex items-start gap-3">
      <span className="mt-0.5 grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400">
        <Icon />
      </span>
      <div className="min-w-0">
        <p className={label}>{l}</p>
        <div className="mt-0.5 text-sm font-medium text-slate-800 dark:text-slate-100">{children}</div>
      </div>
    </div>
  );
}

function PersonCard({ name, role }) {
  if (!name) return <Card><p className="text-sm text-slate-500 dark:text-slate-400">No {role.toLowerCase()} assigned.</p></Card>;
  return (
    <Card className="flex items-center gap-3" padding="md">
      <span className="grid h-11 w-11 shrink-0 place-items-center rounded-full bg-gradient-to-br from-emerald-500 to-teal-600 text-sm font-semibold text-white">
        {name.split(" ").map((w) => w[0]).join("").slice(0, 2)}
      </span>
      <div className="min-w-0">
        <p className="truncate font-medium text-slate-800 dark:text-slate-100">{name}</p>
        <p className="text-xs text-slate-500 dark:text-slate-400">{role}</p>
      </div>
    </Card>
  );
}

// A tab whose backing data (view analytics, recordings) doesn't exist on the backend
// yet — say so plainly rather than showing fabricated numbers.
function ComingSoon({ text }) {
  return <Card><p className="text-sm text-slate-500 dark:text-slate-400">{text}</p></Card>;
}

// One registrant per line: "Name, email" or just "email" (name is then derived from the
// address). Lets an org admin paste a whole spreadsheet column instead of using the
// public per-person registration form for every attendee.
function parseBulkInput(text) {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const [first, second] = line.split(",").map((s) => s.trim());
      if (second) return { full_name: first, email: second };
      const full_name = first.split("@")[0].replace(/[._-]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
      return { full_name, email: first };
    })
    .filter((r) => r.email && r.email.includes("@"));
}

const rTh = "px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-slate-400 whitespace-nowrap";
const rTd = "px-4 py-3 text-sm text-slate-600 dark:text-slate-300 whitespace-nowrap";

function RegistrationsPanel({ streamId, registrations, isLive, onImported }) {
  const [bulkOpen, setBulkOpen] = useState(false);
  const [bulkText, setBulkText] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [promoting, setPromoting] = useState(null);

  const promote = async (r) => {
    setPromoting(r.id);
    try {
      await api.post(`/streams/${streamId}/stage/promote`, { email: r.email, display_name: r.full_name });
      notify.success(`${r.full_name} can now go on camera — send them back to the event link.`);
    } catch (err) {
      notify.error(errMsg(err, "Failed to promote"));
    } finally {
      setPromoting(null);
    }
  };

  const parsed = parseBulkInput(bulkText);

  const submitBulk = async () => {
    if (!parsed.length) return;
    setSubmitting(true);
    try {
      const res = await api.post(`/streams/${streamId}/registrations/bulk`, { registrants: parsed });
      onImported(res.data.created);
      notify.success(
        `${res.data.created.length} registered` + (res.data.skipped.length ? `, ${res.data.skipped.length} already registered` : "")
      );
      setBulkText("");
      setBulkOpen(false);
    } catch (err) {
      notify.error(errMsg(err, "Failed to import registrants"));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Card className="flex items-center gap-3" padding="md">
          <span className="grid h-11 w-11 shrink-0 place-items-center rounded-xl bg-blue-100 text-blue-600 dark:bg-blue-500/15 dark:text-blue-400">
            <FiUsers className="text-lg" />
          </span>
          <div>
            <p className="text-2xl font-bold text-slate-900 dark:text-white">{registrations.length}</p>
            <p className="text-xs text-slate-500 dark:text-slate-400">Total registered</p>
          </div>
        </Card>
        <Button variant="secondary" size="sm" className="w-fit" onClick={() => setBulkOpen((v) => !v)}>
          <FiUserPlus className="text-base" /> Bulk Invite
        </Button>
      </div>

      {bulkOpen && (
        <Card>
          <h3 className="mb-1.5 font-semibold text-slate-900 dark:text-white">Bulk import registrants</h3>
          <p className="mb-3 text-sm text-slate-500 dark:text-slate-400">
            One per line — paste a name and email separated by a comma (<code>Jane Doe, jane@acme.com</code>), or just an
            email on its own. Each person gets a confirmation email with their event link.
          </p>
          <textarea
            rows={6}
            value={bulkText}
            onChange={(e) => setBulkText(e.target.value)}
            placeholder={"Jane Doe, jane@acme.com\nsam@globex.com"}
            className="w-full rounded-xl border border-slate-200 bg-white px-3.5 py-2.5 text-sm text-slate-800 shadow-sm outline-none transition placeholder:text-slate-400 focus:border-emerald-400 focus:ring-2 focus:ring-emerald-500/20 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100"
          />
          <div className="mt-3 flex items-center justify-between">
            <p className="text-xs text-slate-400">{parsed.length} valid {parsed.length === 1 ? "entry" : "entries"} detected</p>
            <Button size="sm" onClick={submitBulk} disabled={!parsed.length || submitting}>
              {submitting ? "Importing…" : `Import ${parsed.length || ""}`}
            </Button>
          </div>
        </Card>
      )}

      <Card padding="none" className="overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px]">
            <thead className="border-b border-slate-100 dark:border-slate-800">
              <tr>
                <th className={rTh}>Name</th>
                <th className={rTh}>Email</th>
                <th className={rTh}>Company</th>
                <th className={rTh}>Source</th>
                <th className={rTh}>Registered</th>
                {isLive && <th className={rTh}>Stage</th>}
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
              {registrations.map((r) => (
                <tr key={r.id} className="hover:bg-slate-50 dark:hover:bg-slate-800/50">
                  <td className={cx(rTd, "font-medium text-slate-800 dark:text-slate-100")}>{r.full_name}</td>
                  <td className={rTd}>{r.email}</td>
                  <td className={rTd}>{r.company || "—"}</td>
                  <td className={rTd}>{r.source === "bulk" ? "Bulk import" : "Self-registered"}</td>
                  <td className={rTd}>{new Date(r.created_at).toLocaleDateString()}</td>
                  {isLive && (
                    <td className={rTd}>
                      <button
                        onClick={() => promote(r)}
                        disabled={promoting === r.id}
                        className="rounded-lg bg-emerald-600 px-2.5 py-1 text-xs font-semibold text-white transition hover:bg-emerald-500 disabled:opacity-50"
                      >
                        {promoting === r.id ? "Inviting…" : "Promote to speaker"}
                      </button>
                    </td>
                  )}
                </tr>
              ))}
              {registrations.length === 0 && (
                <tr><td colSpan={isLive ? 6 : 5} className="px-4 py-12 text-center text-sm text-slate-400">No one has registered yet.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}

const RECORDING_STATUS_LABEL = { recording: "Recording", processing: "Processing", ready: "Ready", failed: "Failed" };
const RECORDING_STATUS_PILL = {
  recording: "bg-rose-100 text-rose-700 dark:bg-rose-500/15 dark:text-rose-400",
  processing: "bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-400",
  ready: "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-400",
  failed: "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300",
};

// Polls each non-final recording every 8s -- there's no publicly reachable webhook URL
// in local dev for LiveKit to notify us the file is ready, so this is the fallback.
function RecordingsPanel({ streamId, recordings, onUpdated }) {
  useEffect(() => {
    const pending = recordings.filter((r) => r.status === "recording" || r.status === "processing");
    if (!pending.length) return;
    const t = setInterval(() => {
      pending.forEach((r) => {
        api
          .post(`/streams/${streamId}/recordings/${r.id}/refresh`)
          .then((res) => onUpdated(res.data))
          .catch(() => {});
      });
    }, 8000);
    return () => clearInterval(t);
  }, [streamId, recordings, onUpdated]);

  if (recordings.length === 0) {
    return <ComingSoon text="No recordings yet — start one from the host studio's Record button while the event is live." />;
  }

  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
      {recordings.map((r) => (
        <Card key={r.id} padding="md">
          <div className="flex items-center justify-between">
            <span className={cx("inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-semibold", RECORDING_STATUS_PILL[r.status])}>
              {(r.status === "recording" || r.status === "processing") && <FiLoader className="animate-spin text-xs" />}
              {RECORDING_STATUS_LABEL[r.status] || r.status}
            </span>
            <span className="text-xs text-slate-400">{new Date(r.created_at).toLocaleString()}</span>
          </div>

          <div className="mt-3 flex items-center gap-3">
            <span className="grid h-11 w-11 shrink-0 place-items-center rounded-xl bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400">
              <FiVideo />
            </span>
            <div className="min-w-0 flex-1">
              {r.status === "ready" && r.file_url ? (
                <a href={r.file_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1.5 text-sm font-medium text-emerald-600 hover:underline dark:text-emerald-400">
                  <FiDownload /> Download / play recording
                </a>
              ) : r.status === "failed" ? (
                <p className="text-sm text-slate-500 dark:text-slate-400">Recording failed — try starting a new one.</p>
              ) : (
                <p className="text-sm text-slate-500 dark:text-slate-400">
                  {r.status === "recording" ? "Recording in progress…" : "Processing — checking again shortly…"}
                </p>
              )}
              {r.duration_seconds != null && (
                <p className="text-xs text-slate-400">{Math.round(r.duration_seconds / 60)} min</p>
              )}
            </div>
          </div>
        </Card>
      ))}
    </div>
  );
}

const timeRange = (event) => {
  if (!event.start_time && !event.end_time) return "—";
  return `${event.start_time || "—"}–${event.end_time || "—"} ${event.timezone}`;
};

export default function EventDetails() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [tab, setTab] = useState("Overview");
  const [event, setEvent] = useState(null);
  const [members, setMembers] = useState([]);
  const [registrations, setRegistrations] = useState([]);
  const [recordings, setRecordings] = useState([]);
  const [loading, setLoading] = useState(true);
  const [notFound, setNotFound] = useState(false);

  useEffect(() => {
    Promise.all([
      api.get(`/streams/${id}`),
      api.get("/organization/members").catch(() => ({ data: [] })),
      api.get(`/streams/${id}/registrations`).catch(() => ({ data: [] })),
      api.get(`/streams/${id}/recordings`).catch(() => ({ data: [] })),
    ])
      .then(([eventRes, membersRes, registrationsRes, recordingsRes]) => {
        setEvent(eventRes.data);
        setMembers(membersRes.data);
        setRegistrations(registrationsRes.data);
        setRecordings(recordingsRes.data);
      })
      .catch(() => setNotFound(true))
      .finally(() => setLoading(false));
  }, [id]);

  const memberName = (memberId) => members.find((m) => m.id === memberId)?.full_name || null;

  const updateEvent = async (patch, successMsg) => {
    try {
      const res = await api.put(`/streams/${id}`, patch);
      setEvent(res.data);
      if (successMsg) notify.success(successMsg);
    } catch (err) {
      notify.error(errMsg(err, "Failed to update event"));
    }
  };

  const act = async (name) => {
    if (name === "Copy Link") {
      navigator.clipboard?.writeText(`${window.location.origin}/e/${event.id}`);
      return notify.success("Event link copied");
    }
    if (name === "Publish") return updateEvent({ status: "scheduled" }, `"${event.title}" published`);
    if (name === "Cancel") return updateEvent({ status: "canceled" }, "Event canceled");
    if (name === "Delete") {
      if (!window.confirm(`Delete "${event.title}"? This can't be undone.`)) return;
      try {
        await api.delete(`/streams/${id}`);
        notify.success(`"${event.title}" deleted`);
        navigate("/organization/events");
      } catch (err) {
        notify.error(errMsg(err, "Failed to delete event"));
      }
    }
  };

  if (loading) {
    return <Card><p className="text-sm text-slate-500 dark:text-slate-400">Loading…</p></Card>;
  }

  if (notFound || !event) {
    return (
      <div className="space-y-4">
        <Button variant="secondary" size="sm" onClick={() => navigate("/organization/events")}>
          <FiArrowLeft /> Back to Events
        </Button>
        <Card><p className="text-sm text-slate-500 dark:text-slate-400">Event not found.</p></Card>
      </div>
    );
  }

  const QUICK = [
    { name: "Copy Link", icon: FiLink, variant: "secondary" },
    ...(event.status === "draft" ? [{ name: "Publish", icon: FiUploadCloud, variant: "primary" }] : []),
    ...(["draft", "scheduled"].includes(event.status) ? [{ name: "Cancel", icon: FiXCircle, variant: "secondary" }] : []),
    { name: "Delete", icon: FiTrash2, variant: "danger" },
  ];

  return (
    <div className="space-y-6">
      <button
        onClick={() => navigate("/organization/events")}
        className="inline-flex items-center gap-1.5 text-sm font-medium text-slate-500 hover:text-slate-800 dark:text-slate-400 dark:hover:text-slate-200"
      >
        <FiArrowLeft /> Back to Events
      </button>

      {/* Banner */}
      <div className="relative overflow-hidden rounded-2xl bg-gradient-to-br from-emerald-600 to-slate-900 p-6 sm:p-8">
        <div className="absolute inset-0 bg-gradient-to-tr from-slate-900/70 via-slate-900/30 to-transparent" />
        <div className="relative flex flex-col gap-2">
          <span className={cx("inline-flex w-fit items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-semibold", STATUS_PILL[event.status])}>
            {event.status === "live" && <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-current" />}
            {STATUS_LABEL[event.status] || event.status}
          </span>
          <h1 className="text-2xl font-bold tracking-tight text-white sm:text-3xl">{event.title}</h1>
          {event.description && <p className="max-w-2xl text-sm text-white/80">{event.description}</p>}
        </div>
      </div>

      {/* Meta + quick actions */}
      <Card>
        <div className="grid grid-cols-2 gap-5 lg:grid-cols-4">
          <Meta icon={FiCalendar} label="Date">{fmtDate(event.scheduled_date)}</Meta>
          <Meta icon={FiClock} label="Time">{timeRange(event)}</Meta>
          <Meta icon={FiUser} label="Host">{memberName(event.host_id) || "Unassigned"}</Meta>
          <Meta icon={FiEye} label="Visibility"><span className={VIS_PILL[event.visibility]}>{VISIBILITY_LABEL[event.visibility]}</span></Meta>
        </div>

        <div className="mt-6 flex flex-wrap gap-2 border-t border-slate-100 pt-5 dark:border-slate-800">
          {QUICK.map(({ name, icon: Icon, variant }) => (
            <Button key={name} variant={variant} size="sm" onClick={() => act(name)}>
              <Icon className="text-base" /> {name}
            </Button>
          ))}
        </div>
      </Card>

      {/* Tabs */}
      <div className="flex gap-1 overflow-x-auto border-b border-slate-200 dark:border-slate-800">
        {TABS.map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={cx(
              "-mb-px whitespace-nowrap border-b-2 px-4 py-2.5 text-sm font-medium transition",
              tab === t
                ? "border-emerald-500 text-emerald-600 dark:text-emerald-400"
                : "border-transparent text-slate-500 hover:text-slate-800 dark:text-slate-400 dark:hover:text-slate-200"
            )}
          >
            {t}
          </button>
        ))}
      </div>

      {/* Tab panels */}
      {tab === "Overview" && (
        <Card>
          <h2 className="mb-2 font-semibold text-slate-900 dark:text-white">About this event</h2>
          <p className="text-sm leading-relaxed text-slate-600 dark:text-slate-300">{event.description || "No description added."}</p>
          <div className="mt-4 flex flex-wrap gap-2 text-xs">
            {event.category && (
              <span className="rounded-full bg-slate-100 px-2.5 py-1 font-medium text-slate-600 dark:bg-slate-800 dark:text-slate-300">{event.category}</span>
            )}
            <span className="rounded-full bg-slate-100 px-2.5 py-1 font-medium text-slate-600 dark:bg-slate-800 dark:text-slate-300">
              Registration: {event.registration_required ? "Required" : "Open"}
            </span>
          </div>
        </Card>
      )}

      {tab === "Team" && (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <PersonCard name={memberName(event.host_id)} role="Host" />
          <PersonCard name={memberName(event.moderator_id)} role="Moderator" />
        </div>
      )}

      {tab === "Registration" && (
        <RegistrationsPanel
          streamId={id}
          registrations={registrations}
          isLive={event.status === "live"}
          onImported={(created) => setRegistrations((list) => [...created, ...list])}
        />
      )}
      {tab === "Recording" && (
        <RecordingsPanel
          streamId={id}
          recordings={recordings}
          onUpdated={(updated) => setRecordings((list) => list.map((r) => (r.id === updated.id ? updated : r)))}
        />
      )}
      {tab === "Analytics" && <ComingSoon text="View analytics aren't tracked yet — this will show viewer counts and watch time once tracking lands." />}

      {tab === "Settings" && (
        <Card>
          <h2 className="mb-4 font-semibold text-slate-900 dark:text-white">Event Settings</h2>
          <dl className="divide-y divide-slate-100 text-sm dark:divide-slate-800">
            {[
              ["Category", event.category || "—"],
              ["Visibility", VISIBILITY_LABEL[event.visibility]],
              ["Registration", event.registration_required ? "Required" : "Open"],
              ["Timezone", event.timezone],
            ].map(([k, v]) => (
              <div key={k} className="flex justify-between py-3">
                <dt className="text-slate-500 dark:text-slate-400">{k}</dt>
                <dd className="font-medium text-slate-800 dark:text-slate-100">{v}</dd>
              </div>
            ))}
          </dl>
          <div className="mt-4 flex flex-wrap gap-2 border-t border-slate-100 pt-4 dark:border-slate-800">
            <Button variant="secondary" size="sm" onClick={() => act("Copy Link")}><FiLink /> Copy Link</Button>
            <Button variant="danger" size="sm" onClick={() => act("Delete")}><FiTrash2 /> Delete Event</Button>
          </div>
        </Card>
      )}
    </div>
  );
}
