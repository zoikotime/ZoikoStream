import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  FiCalendar,
  FiRadio,
  FiEdit,
  FiCheckSquare,
  FiSearch,
  FiFilter,
  FiPlus,
  FiMoreVertical,
  FiEye,
  FiEdit2,
  FiCopy,
  FiUploadCloud,
  FiTrash2,
  FiVideo,
  FiChevronDown,
} from "react-icons/fi";
import { cx } from "../../ui/tokens";
import Card from "../../ui/Card";
import Button from "../../ui/Button";
import StatsCard from "../../ui/StatsCard";
import CreateEventModal from "./CreateEventModal";
import { EVENTS, STATUS_PILL, VIS_PILL, fmtDate } from "../../data/events";

const ACTIONS = [
  { label: "View", icon: FiEye },
  { label: "Edit", icon: FiEdit2 },
  { label: "Duplicate", icon: FiCopy },
  { label: "Publish", icon: FiUploadCloud },
  { label: "Delete", icon: FiTrash2, danger: true },
];

// Row actions dropdown. Closes on outside click / Escape.
function ActionsMenu({ event, onAction }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    if (!open) return;
    const close = (e) => {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false);
    };
    const onKey = (e) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div ref={ref} className="relative flex justify-end">
      <button
        onClick={() => setOpen((v) => !v)}
        aria-label={`Actions for ${event.name}`}
        aria-haspopup="menu"
        aria-expanded={open}
        className="grid h-8 w-8 place-items-center rounded-lg text-slate-400 transition hover:bg-slate-100 hover:text-slate-600 dark:hover:bg-slate-800 dark:hover:text-slate-200"
      >
        <FiMoreVertical />
      </button>
      {open && (
        <div
          role="menu"
          className="absolute right-0 top-9 z-20 w-40 overflow-hidden rounded-xl border border-slate-200 bg-white py-1 shadow-lg dark:border-slate-700 dark:bg-slate-900"
        >
          {ACTIONS.map(({ label, icon: Icon, danger }) => (
            <button
              key={label}
              role="menuitem"
              onClick={() => {
                setOpen(false);
                onAction(label, event);
              }}
              className={cx(
                "flex w-full items-center gap-2.5 px-3.5 py-2 text-sm transition",
                danger
                  ? "text-rose-600 hover:bg-rose-50 dark:text-rose-400 dark:hover:bg-rose-500/10"
                  : "text-slate-600 hover:bg-slate-50 dark:text-slate-300 dark:hover:bg-slate-800"
              )}
            >
              <Icon className="text-base" /> {label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

const th = "px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-slate-400 whitespace-nowrap";
const td = "px-4 py-3 text-sm text-slate-600 dark:text-slate-300 whitespace-nowrap";
const control =
  "rounded-xl border border-slate-200 bg-white px-3.5 py-2 text-sm text-slate-700 shadow-sm outline-none focus:border-emerald-400 focus:ring-2 focus:ring-emerald-500/20 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200";

export default function OrganizationEvents() {
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("All");
  const [sort, setSort] = useState("date-desc");
  const [createOpen, setCreateOpen] = useState(false);

  const stats = useMemo(
    () => [
      { title: "Upcoming Events", value: EVENTS.filter((e) => e.status === "Upcoming").length, icon: FiCalendar, accent: "blue" },
      { title: "Live Events", value: EVENTS.filter((e) => e.status === "Live").length, icon: FiRadio, accent: "emerald", live: true },
      { title: "Draft Events", value: EVENTS.filter((e) => e.status === "Draft").length, icon: FiEdit, accent: "amber" },
      { title: "Completed Events", value: EVENTS.filter((e) => e.status === "Completed").length, icon: FiCheckSquare, accent: "violet" },
    ],
    []
  );

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    let out = EVENTS.filter(
      (e) =>
        (statusFilter === "All" || e.status === statusFilter) &&
        (!q || e.name.toLowerCase().includes(q) || e.host.toLowerCase().includes(q))
    );
    const sorters = {
      "date-desc": (a, b) => b.date.localeCompare(a.date),
      "date-asc": (a, b) => a.date.localeCompare(b.date),
      "name-asc": (a, b) => a.name.localeCompare(b.name),
      "viewers-desc": (a, b) => (b.viewers || 0) - (a.viewers || 0),
    };
    return [...out].sort(sorters[sort]);
  }, [query, statusFilter, sort]);

  // ponytail: no backend — View opens the details page; the rest just log intent.
  const handleAction = (action, event) => {
    if (action === "View") return navigate(`/organization/events/${event.id}`);
    console.log(`${action}: ${event.name}`);
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-slate-900 dark:text-white">Events</h1>
          <p className="text-sm text-slate-500 dark:text-slate-400">
            Manage all live events for your organization
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2.5">
          <div className="relative">
            <FiSearch className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
            <input
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search events..."
              className={cx(control, "w-full pl-9 sm:w-56")}
            />
          </div>

          {/* Filter (native select — no dependency needed) */}
          <div className="relative">
            <FiFilter className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
            <select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
              aria-label="Filter by status"
              className={cx(control, "appearance-none pl-9 pr-8")}
            >
              {["All", "Upcoming", "Live", "Draft", "Completed"].map((s) => (
                <option key={s} value={s}>{s === "All" ? "All statuses" : s}</option>
              ))}
            </select>
            <FiChevronDown className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
          </div>

          {/* Sort */}
          <div className="relative">
            <select
              value={sort}
              onChange={(e) => setSort(e.target.value)}
              aria-label="Sort events"
              className={cx(control, "appearance-none pr-8")}
            >
              <option value="date-desc">Newest first</option>
              <option value="date-asc">Oldest first</option>
              <option value="name-asc">Name (A–Z)</option>
              <option value="viewers-desc">Most viewers</option>
            </select>
            <FiChevronDown className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
          </div>

          <Button size="sm" onClick={() => setCreateOpen(true)}>
            <FiPlus className="text-base" /> Create Event
          </Button>
        </div>
      </div>

      {/* Statistics cards */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {stats.map((s) => (
          <StatsCard key={s.title} {...s} />
        ))}
      </div>

      {/* Data table */}
      <Card padding="none" className="overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[900px]">
            <thead className="border-b border-slate-100 dark:border-slate-800">
              <tr>
                <th className={th}>Event</th>
                <th className={th}>Status</th>
                <th className={th}>Date</th>
                <th className={th}>Host</th>
                <th className={th}>Visibility</th>
                <th className={th}>Registration</th>
                <th className={`${th} text-right`}>Viewers</th>
                <th className={`${th} text-right`}>Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
              {rows.map((e) => (
                <tr key={e.id} className="transition hover:bg-slate-50 dark:hover:bg-slate-800/50">
                  {/* Thumbnail + name */}
                  <td className={td}>
                    <div className="flex items-center gap-3">
                      <span className="grid h-10 w-16 shrink-0 place-items-center rounded-lg bg-gradient-to-br from-slate-800 to-slate-600 text-white dark:from-slate-700 dark:to-slate-900">
                        <FiVideo />
                      </span>
                      <button
                        onClick={() => navigate(`/organization/events/${e.id}`)}
                        className="text-left font-medium text-slate-800 hover:text-emerald-600 dark:text-slate-100 dark:hover:text-emerald-400"
                      >
                        {e.name}
                      </button>
                    </div>
                  </td>
                  <td className={td}>
                    <span className={cx("inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-semibold", STATUS_PILL[e.status])}>
                      {e.status === "Live" && <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-current" />}
                      {e.status}
                    </span>
                  </td>
                  <td className={td}>{fmtDate(e.date)}</td>
                  <td className={td}>{e.host}</td>
                  <td className={td}>
                    <span className={cx("font-medium", VIS_PILL[e.visibility])}>{e.visibility}</span>
                  </td>
                  <td className={td}>{e.registration}</td>
                  <td className={cx(td, "text-right font-medium text-slate-800 dark:text-slate-100")}>
                    {e.viewers != null ? e.viewers.toLocaleString() : "—"}
                  </td>
                  <td className={cx(td, "text-right")}>
                    <ActionsMenu event={e} onAction={handleAction} />
                  </td>
                </tr>
              ))}
              {rows.length === 0 && (
                <tr>
                  <td colSpan={8} className="px-4 py-12 text-center text-sm text-slate-400">
                    No events match your filters.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </Card>

      <CreateEventModal open={createOpen} onClose={() => setCreateOpen(false)} />
    </div>
  );
}
