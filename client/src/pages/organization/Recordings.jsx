// client/src/pages/organization/Recordings.jsx
// Recordings — manage all recorded live events. Route: /organization/recordings
// Rendered inside OrganizationLayout (sidebar + topbar). No backend: search/filter/
// sort and delete run on local state; "Watch Replay" deep-links to the watch page.
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  FiSearch, FiFilter, FiChevronDown, FiUploadCloud, FiPlay,
  FiEye, FiHardDrive, FiDownload, FiShare2, FiTrash2,
  FiFilm, FiDatabase, FiTrendingUp, FiClock,
} from "react-icons/fi";
import { cx } from "../../ui/tokens";
import Card from "../../ui/Card";
import Button from "../../ui/Button";
import StatsCard from "../../ui/StatsCard";
import { notify } from "../../ui/Toast";
import { fmtDate } from "../../data/events";
import {
  recordings as seed, RECORDING_CATEGORIES, fmtStorage, fmtSize, fmtWatch,
} from "../../data/recordings";

// Literal gradient per accent — Tailwind JIT can't compile interpolated names.
// (Same convention as the watch-page thumbnails.)
const THUMB = {
  violet: "from-violet-600 to-slate-900",
  emerald: "from-emerald-600 to-slate-900",
  blue: "from-blue-600 to-slate-900",
  amber: "from-amber-500 to-slate-900",
  indigo: "from-indigo-600 to-slate-900",
  rose: "from-rose-600 to-slate-900",
};

const control =
  "rounded-xl border border-slate-200 bg-white px-3.5 py-2 text-sm text-slate-700 shadow-sm outline-none focus:border-emerald-400 focus:ring-2 focus:ring-emerald-500/20 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200";

function ActionBtn({ icon: Icon, label, danger, onClick }) {
  return (
    <button
      onClick={onClick}
      title={label}
      aria-label={label}
      className={cx(
        "inline-flex flex-1 items-center justify-center gap-1.5 rounded-lg border px-2 py-1.5 text-xs font-medium transition",
        danger
          ? "border-slate-200 text-slate-600 hover:border-rose-300 hover:bg-rose-50 hover:text-rose-600 dark:border-slate-700 dark:text-slate-300 dark:hover:border-rose-500/40 dark:hover:bg-rose-500/10 dark:hover:text-rose-400"
          : "border-slate-200 text-slate-600 hover:bg-slate-50 hover:text-slate-800 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800 dark:hover:text-slate-100"
      )}
    >
      <Icon className="text-sm" /> {label}
    </button>
  );
}

function RecordingCard({ r, onDownload, onShare, onDelete }) {
  return (
    <Card padding="none" hover className="flex flex-col overflow-hidden">
      {/* Thumbnail */}
      <div className={cx("group relative grid aspect-video place-items-center bg-gradient-to-br", THUMB[r.accent] || THUMB.emerald)}>
        <Link
          to={`/events/${r.id}/watch`}
          aria-label={`Watch ${r.name}`}
          className="grid h-12 w-12 place-items-center rounded-full bg-white/15 text-white backdrop-blur transition hover:scale-110 hover:bg-white/25"
        >
          <FiPlay className="ml-0.5 text-xl" />
        </Link>
        <span className="absolute bottom-2 right-2 rounded bg-black/60 px-1.5 py-0.5 text-[11px] font-medium text-white">
          {r.duration}
        </span>
        <span className="absolute left-2 top-2 rounded bg-black/40 px-1.5 py-0.5 text-[11px] font-medium text-white backdrop-blur">
          {r.category}
        </span>
      </div>

      {/* Body */}
      <div className="flex flex-1 flex-col p-4">
        <p className="truncate font-semibold text-slate-800 dark:text-slate-100" title={r.name}>{r.name}</p>
        <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">{fmtDate(r.date)}</p>

        <div className="mt-3 flex items-center gap-4 text-xs text-slate-500 dark:text-slate-400">
          <span className="inline-flex items-center gap-1"><FiEye /> {r.views.toLocaleString()} views</span>
          <span className="inline-flex items-center gap-1"><FiHardDrive /> {fmtSize(r.sizeGB)}</span>
        </div>

        {/* Actions */}
        <div className="mt-4 space-y-2 border-t border-slate-100 pt-3 dark:border-slate-800">
          <Button size="sm" href={`/events/${r.id}/watch`} className="w-full">
            <FiPlay className="text-base" /> Watch Replay
          </Button>
          <div className="flex items-center gap-1.5">
            <ActionBtn icon={FiDownload} label="Download" onClick={() => onDownload(r)} />
            <ActionBtn icon={FiShare2} label="Share" onClick={() => onShare(r)} />
            <ActionBtn icon={FiTrash2} label="Delete" danger onClick={() => onDelete(r)} />
          </div>
        </div>
      </div>
    </Card>
  );
}

export default function OrganizationRecordings() {
  const [list, setList] = useState(seed);
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("All");
  const [sort, setSort] = useState("date-desc");

  // Totals reflect the live list (so deleting updates the stat cards), not the search filter.
  const stats = useMemo(() => {
    const views = list.reduce((s, r) => s + r.views, 0);
    const storage = list.reduce((s, r) => s + r.sizeGB, 0);
    const avg = list.length ? list.reduce((s, r) => s + r.avgWatchMin, 0) / list.length : 0;
    return [
      { title: "Total Recordings", value: list.length, icon: FiFilm, accent: "emerald" },
      { title: "Total Storage", value: fmtStorage(storage), icon: FiDatabase, accent: "blue" },
      { title: "Total Views", value: views, icon: FiTrendingUp, accent: "violet" },
      { title: "Avg. Watch Time", value: fmtWatch(avg), icon: FiClock, accent: "amber" },
    ];
  }, [list]);

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    const out = list.filter(
      (r) => (category === "All" || r.category === category) && (!q || r.name.toLowerCase().includes(q))
    );
    const sorters = {
      "date-desc": (a, b) => b.date.localeCompare(a.date),
      "date-asc": (a, b) => a.date.localeCompare(b.date),
      "views-desc": (a, b) => b.views - a.views,
      "size-desc": (a, b) => b.sizeGB - a.sizeGB,
      "name-asc": (a, b) => a.name.localeCompare(b.name),
    };
    return [...out].sort(sorters[sort]);
  }, [list, query, category, sort]);

  // No backend: Download/Share just confirm intent; Delete mutates local state.
  const onDownload = (r) => notify.info(`Preparing “${r.name}” for download…`);
  const onShare = (r) => {
    const url = `${window.location.origin}/events/${r.id}/watch`;
    navigator.clipboard?.writeText(url);
    notify.success("Replay link copied to clipboard");
  };
  const onDelete = (r) => {
    if (!window.confirm(`Delete the recording “${r.name}”? This can't be undone.`)) return;
    setList((l) => l.filter((x) => x.id !== r.id));
    notify.success("Recording deleted");
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-slate-900 dark:text-white">Recordings</h1>
          <p className="text-sm text-slate-500 dark:text-slate-400">Manage all recorded live events for your organization</p>
        </div>

        <div className="flex flex-wrap items-center gap-2.5">
          <div className="relative">
            <FiSearch className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
            <input
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search recordings..."
              className={cx(control, "w-full pl-9 sm:w-56")}
            />
          </div>

          {/* Filter by category */}
          <div className="relative">
            <FiFilter className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
            <select
              value={category}
              onChange={(e) => setCategory(e.target.value)}
              aria-label="Filter by category"
              className={cx(control, "appearance-none pl-9 pr-8")}
            >
              {RECORDING_CATEGORIES.map((c) => (
                <option key={c} value={c}>{c === "All" ? "All categories" : c}</option>
              ))}
            </select>
            <FiChevronDown className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
          </div>

          {/* Sort */}
          <div className="relative">
            <select
              value={sort}
              onChange={(e) => setSort(e.target.value)}
              aria-label="Sort recordings"
              className={cx(control, "appearance-none pr-8")}
            >
              <option value="date-desc">Newest first</option>
              <option value="date-asc">Oldest first</option>
              <option value="views-desc">Most viewed</option>
              <option value="size-desc">Largest size</option>
              <option value="name-asc">Name (A–Z)</option>
            </select>
            <FiChevronDown className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
          </div>

          <Button size="sm" onClick={() => notify.info("Upload flow coming soon")}>
            <FiUploadCloud className="text-base" /> Upload Recording
          </Button>
        </div>
      </div>

      {/* Statistics cards */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {stats.map((s) => (
          <StatsCard key={s.title} {...s} />
        ))}
      </div>

      {/* Recording cards */}
      {shown.length > 0 ? (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {shown.map((r) => (
            <RecordingCard key={r.id} r={r} onDownload={onDownload} onShare={onShare} onDelete={onDelete} />
          ))}
        </div>
      ) : (
        <Card className="py-16 text-center">
          <p className="text-sm text-slate-400">No recordings match your filters.</p>
        </Card>
      )}
    </div>
  );
}
