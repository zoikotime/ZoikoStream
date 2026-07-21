import { useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import {
  FiArrowLeft,
  FiCalendar,
  FiClock,
  FiUser,
  FiEye,
  FiEdit2,
  FiUploadCloud,
  FiLink,
  FiCopy,
  FiTrash2,
  FiVideo,
  FiDownload,
  FiUsers,
  FiCheckCircle,
  FiPlay,
} from "react-icons/fi";
import { cx } from "../../ui/tokens";
import Card from "../../ui/Card";
import Button from "../../ui/Button";
import StatsCard from "../../ui/StatsCard";
import BarChartCard from "../../components/Dashboard/BarChartCard";
import { notify } from "../../ui/Toast";
import { getEvent, STATUS_PILL, VIS_PILL, fmtDate } from "../../data/events";

const TABS = ["Overview", "Registration", "Hosts", "Moderators", "Speakers", "Recording", "Analytics", "Settings"];

// Literal gradient per accent — Tailwind JIT can't compile interpolated class names.
const BANNER = {
  violet: "from-violet-600 to-slate-900",
  emerald: "from-emerald-600 to-slate-900",
  blue: "from-blue-600 to-slate-900",
  amber: "from-amber-500 to-slate-900",
  indigo: "from-indigo-600 to-slate-900",
  rose: "from-rose-600 to-slate-900",
};

const label = "text-xs font-medium uppercase tracking-wide text-slate-400";

// Small labelled field for the overview meta row.
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

// People list used by Hosts / Moderators / Speakers tabs.
function PeoplePanel({ title, people, role }) {
  if (!people.length)
    return <Card><p className="text-sm text-slate-500 dark:text-slate-400">No {title.toLowerCase()} assigned yet.</p></Card>;
  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
      {people.map((name) => (
        <Card key={name} className="flex items-center gap-3" padding="md">
          <span className="grid h-11 w-11 shrink-0 place-items-center rounded-full bg-gradient-to-br from-emerald-500 to-teal-600 text-sm font-semibold text-white">
            {name.split(" ").map((w) => w[0]).join("")}
          </span>
          <div className="min-w-0">
            <p className="truncate font-medium text-slate-800 dark:text-slate-100">{name}</p>
            <p className="text-xs text-slate-500 dark:text-slate-400">{role}</p>
          </div>
        </Card>
      ))}
    </div>
  );
}

const REG_ROWS = [
  { name: "Jordan Blake", email: "jordan@acme.io", when: "May 12, 2024", status: "Confirmed" },
  { name: "Sam Rivera", email: "sam@globex.com", when: "May 13, 2024", status: "Confirmed" },
  { name: "Taylor Quinn", email: "taylor@initech.com", when: "May 14, 2024", status: "Waitlist" },
  { name: "Morgan Lee", email: "morgan@umbrella.co", when: "May 15, 2024", status: "Confirmed" },
];

const ATTENDANCE = [
  { label: "Mon", value: 120 }, { label: "Tue", value: 210 }, { label: "Wed", value: 180 },
  { label: "Thu", value: 260 }, { label: "Fri", value: 320 }, { label: "Sat", value: 290 }, { label: "Sun", value: 410 },
];

const th = "px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-slate-400 whitespace-nowrap";
const td = "px-4 py-3 text-sm text-slate-600 dark:text-slate-300 whitespace-nowrap";

export default function EventDetails() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [tab, setTab] = useState("Overview");
  const event = getEvent(id);

  if (!event) {
    return (
      <div className="space-y-4">
        <Button variant="secondary" size="sm" onClick={() => navigate("/organization/events")}>
          <FiArrowLeft /> Back to Events
        </Button>
        <Card><p className="text-sm text-slate-500 dark:text-slate-400">Event not found.</p></Card>
      </div>
    );
  }

  // ponytail: no backend — quick actions toast intent (Copy Link uses the clipboard API).
  const act = (name) => {
    if (name === "Copy Link") {
      navigator.clipboard?.writeText(`${window.location.origin}/e/${event.id}`);
      return notify.success("Event link copied");
    }
    if (name === "Delete") return notify.success(`"${event.name}" deleted`);
    notify.success(`${name}: ${event.name}`);
  };

  const QUICK = [
    { name: "Edit", icon: FiEdit2, variant: "secondary" },
    { name: "Publish", icon: FiUploadCloud, variant: "primary" },
    { name: "Copy Link", icon: FiLink, variant: "secondary" },
    { name: "Duplicate", icon: FiCopy, variant: "secondary" },
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
      <div className={cx("relative overflow-hidden rounded-2xl bg-gradient-to-br p-6 sm:p-8", BANNER[event.accent] || BANNER.emerald)}>
        <div className="absolute inset-0 bg-gradient-to-tr from-slate-900/70 via-slate-900/30 to-transparent" />
        <div className="relative flex flex-col gap-2">
          <span className={cx("inline-flex w-fit items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-semibold", STATUS_PILL[event.status])}>
            {event.status === "Live" && <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-current" />}
            {event.status}
          </span>
          <h1 className="text-2xl font-bold tracking-tight text-white sm:text-3xl">{event.name}</h1>
          <p className="max-w-2xl text-sm text-white/80">{event.description}</p>
        </div>
      </div>

      {/* Meta + quick actions */}
      <Card>
        <div className="grid grid-cols-2 gap-5 lg:grid-cols-4">
          <Meta icon={FiCalendar} label="Date">{fmtDate(event.date)}</Meta>
          <Meta icon={FiClock} label="Time">{event.start}–{event.end} {event.timezone.split("/").pop().replace("_", " ")}</Meta>
          <Meta icon={FiUser} label="Host">{event.host}</Meta>
          <Meta icon={FiEye} label="Visibility"><span className={VIS_PILL[event.visibility]}>{event.visibility}</span></Meta>
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
        <div className="grid grid-cols-1 gap-6 xl:grid-cols-3">
          <div className="space-y-6 xl:col-span-2">
            <Card>
              <h2 className="mb-2 font-semibold text-slate-900 dark:text-white">About this event</h2>
              <p className="text-sm leading-relaxed text-slate-600 dark:text-slate-300">{event.description}</p>
              <div className="mt-4 flex flex-wrap gap-2 text-xs">
                <span className="rounded-full bg-slate-100 px-2.5 py-1 font-medium text-slate-600 dark:bg-slate-800 dark:text-slate-300">{event.category}</span>
                <span className="rounded-full bg-slate-100 px-2.5 py-1 font-medium text-slate-600 dark:bg-slate-800 dark:text-slate-300">Registration: {event.registration}</span>
              </div>
            </Card>
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
              <StatsCard title="Registered" value={event.registered} icon={FiUsers} accent="blue" />
              <StatsCard title="Peak Viewers" value={event.viewers ?? 0} icon={FiEye} accent="violet" />
              <StatsCard title="Speakers" value={event.speakers.length} icon={FiUser} accent="emerald" />
            </div>
          </div>
          <Card>
            <h2 className="mb-4 font-semibold text-slate-900 dark:text-white">Team</h2>
            <div className="space-y-3 text-sm">
              <div className="flex justify-between"><span className="text-slate-500 dark:text-slate-400">Host</span><span className="font-medium text-slate-800 dark:text-slate-100">{event.host}</span></div>
              <div className="flex justify-between"><span className="text-slate-500 dark:text-slate-400">Moderators</span><span className="font-medium text-slate-800 dark:text-slate-100">{event.moderators.length || "—"}</span></div>
              <div className="flex justify-between"><span className="text-slate-500 dark:text-slate-400">Speakers</span><span className="font-medium text-slate-800 dark:text-slate-100">{event.speakers.length || "—"}</span></div>
            </div>
          </Card>
        </div>
      )}

      {tab === "Registration" && (
        <div className="space-y-6">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            <StatsCard title="Total Registered" value={event.registered} icon={FiUsers} accent="blue" />
            <StatsCard title="Confirmed" value={Math.round(event.registered * 0.82)} icon={FiCheckCircle} accent="emerald" />
            <StatsCard title="Waitlist" value={Math.round(event.registered * 0.05)} icon={FiClock} accent="amber" />
          </div>
          <Card padding="none" className="overflow-hidden">
            <div className="overflow-x-auto">
              <table className="w-full min-w-[560px]">
                <thead className="border-b border-slate-100 dark:border-slate-800">
                  <tr><th className={th}>Name</th><th className={th}>Email</th><th className={th}>Registered</th><th className={th}>Status</th></tr>
                </thead>
                <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                  {REG_ROWS.map((r) => (
                    <tr key={r.email} className="hover:bg-slate-50 dark:hover:bg-slate-800/50">
                      <td className={cx(td, "font-medium text-slate-800 dark:text-slate-100")}>{r.name}</td>
                      <td className={td}>{r.email}</td>
                      <td className={td}>{r.when}</td>
                      <td className={td}>
                        <span className={cx("rounded-full px-2.5 py-0.5 text-xs font-semibold", r.status === "Confirmed" ? STATUS_PILL.Live : STATUS_PILL.Draft)}>{r.status}</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        </div>
      )}

      {tab === "Hosts" && <PeoplePanel title="Hosts" people={[event.host]} role="Host" />}
      {tab === "Moderators" && <PeoplePanel title="Moderators" people={event.moderators} role="Moderator" />}
      {tab === "Speakers" && <PeoplePanel title="Speakers" people={event.speakers} role="Speaker" />}

      {tab === "Recording" && (
        <Card>
          {event.status === "Completed" ? (
            <div className="space-y-4">
              <div className="grid aspect-video w-full place-items-center rounded-xl bg-slate-900">
                <button onClick={() => act("Play recording")} className="grid h-16 w-16 place-items-center rounded-full bg-white/15 text-white backdrop-blur transition hover:bg-white/25">
                  <FiPlay className="ml-1 text-2xl" />
                </button>
              </div>
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2 text-sm text-slate-600 dark:text-slate-300">
                  <FiVideo className="text-slate-400" /> {event.name} — full recording · 01:12:40
                </div>
                <Button variant="secondary" size="sm" onClick={() => act("Download recording")}><FiDownload /> Download</Button>
              </div>
            </div>
          ) : (
            <p className="text-sm text-slate-500 dark:text-slate-400">Recording will be available after the event ends.</p>
          )}
        </Card>
      )}

      {tab === "Analytics" && (
        <div className="space-y-6">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <StatsCard title="Peak Viewers" value={event.viewers ?? 0} icon={FiEye} accent="violet" />
            <StatsCard title="Registered" value={event.registered} icon={FiUsers} accent="blue" />
            <StatsCard title="Avg. Watch Time" value={38} suffix=" min" icon={FiClock} accent="amber" />
            <StatsCard title="Attendance Rate" value={72} suffix="%" icon={FiCheckCircle} accent="emerald" />
          </div>
          <BarChartCard title="Concurrent Viewers" subtitle="Peak per day" data={ATTENDANCE} />
        </div>
      )}

      {tab === "Settings" && (
        <Card>
          <h2 className="mb-4 font-semibold text-slate-900 dark:text-white">Event Settings</h2>
          <dl className="divide-y divide-slate-100 text-sm dark:divide-slate-800">
            {[
              ["Category", event.category],
              ["Visibility", event.visibility],
              ["Registration", event.registration],
              ["Timezone", event.timezone],
            ].map(([k, v]) => (
              <div key={k} className="flex justify-between py-3">
                <dt className="text-slate-500 dark:text-slate-400">{k}</dt>
                <dd className="font-medium text-slate-800 dark:text-slate-100">{v}</dd>
              </div>
            ))}
          </dl>
          <div className="mt-4 border-t border-slate-100 pt-4 dark:border-slate-800">
            <Button variant="danger" size="sm" onClick={() => act("Delete")}><FiTrash2 /> Delete Event</Button>
          </div>
        </Card>
      )}
    </div>
  );
}
