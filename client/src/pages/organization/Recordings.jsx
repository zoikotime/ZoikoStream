// client/src/pages/organization/Recordings.jsx
// Media Library — every recording the organization has, on real data from /media/*.
// Route: /organization/recordings. Rendered inside OrganizationLayout.
//
// ONE screen serves the organization library, the host library (Mine), the event library
// (?event=<id>), the archive and the recycle bin: they differ only by the query the server is asked
// for, and five screens would be five places to keep the tenant scoping and the actions consistent.
import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  FiSearch, FiChevronDown, FiPlay, FiEye, FiHardDrive, FiDownload, FiShare2,
  FiTrash2, FiFilm, FiDatabase, FiClock, FiFolder, FiFolderPlus, FiArchive, FiRotateCcw,
  FiAlertTriangle, FiGrid, FiList, FiRefreshCw, FiX, FiCheck,
} from "react-icons/fi";
import { cx } from "../../ui/tokens";
import Card from "../../ui/Card";
import Button from "../../ui/Button";
import Badge from "../../ui/Badge";
import StatsCard from "../../ui/StatsCard";
import Modal from "../../ui/Modal";
import ConfirmDialog from "../../ui/ConfirmDialog";
import { notify } from "../../ui/Toast";
import DataTable from "../../components/admin/DataTable";
import useApi from "../../hooks/useApi";
import useMutation from "../../hooks/useMutation";
import api from "../../api";
import { fmtDate } from "../../data/events";
import {
  SCOPES, SORTS, CATEGORIES, VISIBILITY, fmtBytes, fmtDuration, fmtCount, fmtPercent, statusOf,
} from "../../data/media";

const control =
  "rounded-xl border border-slate-200 bg-white px-3.5 py-2 text-sm text-slate-700 shadow-sm outline-none focus:border-emerald-400 focus:ring-2 focus:ring-emerald-500/20 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200";

function Thumb({ item, className = "" }) {
  const status = statusOf(item);
  return (
    <div className={cx("group relative grid place-items-center bg-gradient-to-br from-slate-700 to-slate-900", className)}>
      {item.has_file ? (
        <Link
          to={`/organization/recordings/${item.id}`}
          aria-label={`Open ${item.title}`}
          className="grid h-12 w-12 place-items-center rounded-full bg-white/15 text-white backdrop-blur transition hover:scale-110 hover:bg-white/25"
        >
          <FiPlay className="ml-0.5 text-xl" />
        </Link>
      ) : (
        <FiAlertTriangle className="text-2xl text-amber-300/80" aria-hidden="true" />
      )}
      {item.duration_ms > 0 && (
        <span className="absolute bottom-2 right-2 rounded bg-black/65 px-1.5 py-0.5 text-[11px] font-medium tabular-nums text-white">
          {fmtDuration(item.duration_ms)}
        </span>
      )}
      <span className="absolute left-2 top-2">
        <Badge tone={status.tone} size="sm">{status.label}</Badge>
      </span>
    </div>
  );
}

function RecordingCard({ item, onAction }) {
  const inBin = Boolean(item.deleted_at);
  return (
    <Card padding="none" hover className="flex flex-col overflow-hidden">
      <Thumb item={item} className="aspect-video" />
      <div className="flex flex-1 flex-col p-4">
        <Link
          to={`/organization/recordings/${item.id}`}
          className="truncate font-semibold text-slate-800 hover:text-emerald-600 dark:text-slate-100 dark:hover:text-emerald-400"
          title={item.title}
        >
          {item.title}
        </Link>
        <p className="mt-0.5 truncate text-xs text-slate-500 dark:text-slate-400">
          {item.event_title && item.event_title !== item.title ? `${item.event_title} · ` : ""}
          {item.stopped_at ? fmtDate(item.stopped_at) : "Not finished"}
        </p>

        <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-slate-500 dark:text-slate-400">
          <span className="inline-flex items-center gap-1"><FiEye /> {fmtCount(item.view_count)}</span>
          <span className="inline-flex items-center gap-1"><FiHardDrive /> {fmtBytes(item.size_bytes)}</span>
          {item.download_count > 0 && (
            <span className="inline-flex items-center gap-1"><FiDownload /> {fmtCount(item.download_count)}</span>
          )}
        </div>

        {item.tags?.length > 0 && (
          <div className="mt-2 flex flex-wrap gap-1">
            {item.tags.slice(0, 3).map((tag) => (
              <span key={tag} className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                {tag}
              </span>
            ))}
            {item.tags.length > 3 && <span className="text-[10px] text-slate-400">+{item.tags.length - 3}</span>}
          </div>
        )}

        {/* An honest line where a card would otherwise look merely empty. */}
        {item.status === "failed" && (
          <p className="mt-2 rounded-lg bg-rose-50 px-2 py-1.5 text-[11px] text-rose-700 dark:bg-rose-500/10 dark:text-rose-300">
            {item.error || "The capture produced no file."}
            {item.retryable && " Retry from the host console while the event is live."}
          </p>
        )}

        <div className="mt-auto space-y-2 pt-3">
          <div className="flex items-center gap-1.5 border-t border-slate-100 pt-3 dark:border-slate-800">
            {inBin ? (
              <>
                <ActionBtn icon={FiRotateCcw} label="Restore" onClick={() => onAction("restore", item)} />
                <ActionBtn icon={FiTrash2} label="Purge" danger onClick={() => onAction("purge", item)} />
              </>
            ) : (
              <>
                <ActionBtn icon={FiShare2} label="Share" onClick={() => onAction("share", item)} />
                <ActionBtn
                  icon={item.archived_at ? FiRotateCcw : FiArchive}
                  label={item.archived_at ? "Unarchive" : "Archive"}
                  onClick={() => onAction("archive", item)}
                />
                <ActionBtn icon={FiTrash2} label="Delete" danger onClick={() => onAction("delete", item)} />
              </>
            )}
          </div>
        </div>
      </div>
    </Card>
  );
}

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

export default function OrganizationRecordings() {
  const [params, setParams] = useSearchParams();
  const scope = params.get("scope") || "all";
  const eventId = params.get("event") || "";
  const folderId = params.get("folder") || "";

  const [query, setQuery] = useState("");
  const [debounced, setDebounced] = useState("");
  const [category, setCategory] = useState("");
  const [tag, setTag] = useState("");
  const [sort, setSort] = useState("newest");
  const [view, setView] = useState("grid");
  const [page, setPage] = useState(1);
  const [newFolder, setNewFolder] = useState(false);
  const [confirm, setConfirm] = useState(null);

  // Search is debounced against the SERVER: the library is paged, so filtering the current page
  // client-side would hide matches that live on page 3.
  useEffect(() => {
    const timer = setTimeout(() => {
      setDebounced(query);
      setPage(1);   // a new search starts at the first page, not wherever the last one left off
    }, 300);
    return () => clearTimeout(timer);
  }, [query]);

  const qs = useMemo(() => {
    const search = new URLSearchParams({ scope, sort, page: String(page), page_size: "60" });
    if (debounced.trim()) search.set("q", debounced.trim());
    if (category) search.set("category", category);
    if (tag) search.set("tag", tag);
    if (eventId) search.set("event_id", eventId);
    if (folderId) search.set("folder_id", folderId);
    return search.toString();
  }, [scope, sort, page, debounced, category, tag, eventId, folderId]);

  const library = useApi(() => api.get(`/media/library?${qs}`).then((r) => r.data));
  const stats = useApi(() => api.get("/media/stats").then((r) => r.data));
  const folders = useApi(() => api.get("/media/folders").then((r) => r.data));
  const filters = useApi(() => api.get("/media/filters").then((r) => r.data));

  const reloadAll = () => {
    library.reload();
    stats.reload();
    folders.reload();
  };

  const act = useMutation({ onDone: reloadAll });

  const setParam = (key, value) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
    setPage(1);
  };

  const onAction = (kind, item) => {
    if (kind === "share") {
      const url = `${window.location.origin}/organization/recordings/${item.id}`;
      navigator.clipboard?.writeText(url).then(
        () => notify.success("Link copied"),
        () => notify.info(url)
      );
      return;
    }
    if (kind === "archive") {
      act.run(
        () => api.post(`/media/recordings/${item.id}/archive`, { archived: !item.archived_at }),
        { success: item.archived_at ? "Moved back to the library" : "Archived" }
      );
      return;
    }
    if (kind === "restore") {
      act.run(() => api.post(`/media/recordings/${item.id}/restore`), { success: "Restored" });
      return;
    }
    // Delete and purge are the two that need a confirmation, and they say different things:
    // delete is reversible, purge is not.
    setConfirm({ kind, item });
  };

  const runConfirm = async () => {
    const { kind, item } = confirm;
    const ok = await act.run(
      () => (kind === "purge"
        ? api.delete(`/media/recordings/${item.id}/purge`)
        : api.delete(`/media/recordings/${item.id}`)),
      {
        success: (res) => {
          if (kind !== "purge") return "Moved to the recycle bin";
          const data = res?.data || {};
          // Reports what happened to the BYTES, not just the row — an admin told "purged" while
          // the file is still in the bucket has been misinformed about a deletion request.
          return data.object_deleted
            ? "Purged, and the file was deleted from storage"
            : `Row purged. ${data.object_error || "The stored file was not removed."}`;
        },
      }
    );
    if (ok) setConfirm(null);
  };

  const createFolder = useMutation({
    success: "Folder created",
    onDone: () => {
      folders.reload();
      setNewFolder(false);
    },
  });

  const data = library.data;
  const items = data?.items || [];
  const storage = stats.data?.storage;

  const cards = [
    { title: "Recordings", value: stats.data?.recordings ?? 0, icon: FiFilm, accent: "emerald" },
    {
      title: "Storage used",
      value: storage ? fmtBytes(storage.used_bytes) : "—",
      icon: FiDatabase,
      accent: stats.data?.storage_alert ? "rose" : "blue",
      delta: storage?.percent_used != null ? fmtPercent(storage.percent_used, 1) : undefined,
      up: false,
    },
    { title: "Replay views", value: stats.data?.views ?? 0, icon: FiEye, accent: "violet" },
    {
      title: "Avg. length",
      value: stats.data?.avg_duration_ms ? fmtDuration(stats.data.avg_duration_ms) : "—",
      icon: FiClock,
      accent: "amber",
    },
  ];

  const columns = [
    {
      key: "title", header: "Recording", className: "whitespace-nowrap",
      render: (r) => (
        <Link to={`/organization/recordings/${r.id}`} className="font-medium text-slate-800 hover:text-emerald-600 dark:text-slate-100">
          {r.title}
        </Link>
      ),
    },
    { key: "status", header: "Status", render: (r) => <Badge tone={statusOf(r).tone} size="sm">{statusOf(r).label}</Badge> },
    { key: "stopped_at", header: "Recorded", className: "whitespace-nowrap", render: (r) => (r.stopped_at ? fmtDate(r.stopped_at) : "—") },
    { key: "duration_ms", header: "Length", align: "right", render: (r) => fmtDuration(r.duration_ms) },
    { key: "size_bytes", header: "Size", align: "right", render: (r) => fmtBytes(r.size_bytes) },
    { key: "view_count", header: "Views", align: "right", render: (r) => fmtCount(r.view_count) },
    { key: "download_count", header: "Downloads", align: "right", render: (r) => fmtCount(r.download_count) },
    {
      key: "visibility", header: "Audience",
      render: (r) => VISIBILITY.find((v) => v.key === r.visibility)?.label || r.visibility,
    },
  ];

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-slate-900 dark:text-white">Media Library</h1>
          <p className="text-sm text-slate-500 dark:text-slate-400">
            Every recording your organization has captured — searchable, organized and access-controlled
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2.5">
          <div className="relative">
            <FiSearch className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
            <input
              type="search" value={query} onChange={(e) => setQuery(e.target.value)}
              placeholder="Search recordings…" aria-label="Search recordings"
              className={cx(control, "w-full pl-9 sm:w-60")}
            />
          </div>
          <Button size="sm" variant="secondary" onClick={reloadAll} disabled={library.loading}>
            <FiRefreshCw className={cx("text-base", library.loading && "animate-spin")} /> Refresh
          </Button>
          <Button size="sm" onClick={() => setNewFolder(true)}>
            <FiFolderPlus className="text-base" /> New folder
          </Button>
        </div>
      </div>

      {/* Storage warning — a real threshold breach, not decoration. */}
      {stats.data?.storage_alert && (
        <Card className="border-amber-200 bg-amber-50 dark:border-amber-500/30 dark:bg-amber-500/10">
          <div className="flex items-start gap-3">
            <FiAlertTriangle className="mt-0.5 shrink-0 text-lg text-amber-600 dark:text-amber-400" />
            <div className="text-sm">
              <p className="font-semibold text-amber-900 dark:text-amber-200">Storage almost full</p>
              <p className="text-amber-800 dark:text-amber-300/90">
                {fmtPercent(storage?.percent_used, 1)} of {fmtBytes(storage?.quota_bytes)} used.
                The recycle bin is holding {fmtBytes(storage?.recycle_bin_bytes)} — emptying it frees that space.
              </p>
            </div>
          </div>
        </Card>
      )}

      {!stats.loading && stats.data && !stats.data.storage_configured && (
        <Card className="border-slate-200 bg-slate-50 dark:border-slate-700 dark:bg-slate-800/40">
          <p className="text-sm text-slate-600 dark:text-slate-300">
            <strong className="font-semibold text-slate-800 dark:text-slate-100">Recording storage is not configured.</strong>{" "}
            Captures cannot be retained until an S3-compatible bucket is set (<code className="rounded bg-slate-200 px-1 text-xs dark:bg-slate-700">S3_BUCKET</code>),
            so recordings will report as failed with no file. Everything else on this page works.
          </p>
        </Card>
      )}

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {cards.map((c) => <StatsCard key={c.title} {...c} />)}
      </div>

      {/* Shelves */}
      <div className="flex flex-wrap items-center gap-1.5 border-b border-slate-200 pb-px dark:border-slate-800">
        {SCOPES.map((s) => {
          const count = s.key === "deleted" ? storage?.counts?.recycle_bin
            : s.key === "archived" ? storage?.counts?.archived
              : s.key === "failed" ? stats.data?.failed : null;
          return (
            <button
              key={s.key}
              onClick={() => setParam("scope", s.key === "all" ? "" : s.key)}
              title={s.hint}
              className={cx(
                "-mb-px inline-flex items-center gap-1.5 border-b-2 px-3 py-2 text-sm font-medium transition",
                scope === s.key
                  ? "border-emerald-500 text-emerald-600 dark:text-emerald-400"
                  : "border-transparent text-slate-500 hover:text-slate-800 dark:text-slate-400 dark:hover:text-slate-200"
              )}
            >
              {s.label}
              {count > 0 && (
                <span className="rounded-full bg-slate-100 px-1.5 text-[10px] tabular-nums text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                  {count}
                </span>
              )}
            </button>
          );
        })}
      </div>

      <div className="flex flex-col gap-6 xl:flex-row">
        {/* Folders */}
        <aside className="shrink-0 xl:w-56">
          <Card padding="sm">
            <p className="px-2 pb-2 text-[11px] font-semibold uppercase tracking-wide text-slate-400">Folders</p>
            <ul className="space-y-0.5">
              <FolderRow active={!folderId} label="All recordings" count={data?.total} onClick={() => setParam("folder", "")} />
              {(folders.data || []).filter((f) => !f.parent_id).map((f) => (
                <div key={f.id}>
                  <FolderRow
                    active={folderId === f.id} label={f.name} count={f.count}
                    onClick={() => setParam("folder", f.id)}
                  />
                  {(folders.data || []).filter((c) => c.parent_id === f.id).map((child) => (
                    <FolderRow
                      key={child.id} nested active={folderId === child.id} label={child.name}
                      count={child.count} onClick={() => setParam("folder", child.id)}
                    />
                  ))}
                </div>
              ))}
              {folders.data?.length === 0 && (
                <li className="px-2 py-1.5 text-xs text-slate-400">
                  No folders yet — create one to group recordings.
                </li>
              )}
            </ul>
          </Card>
        </aside>

        <div className="min-w-0 flex-1 space-y-4">
          {/* Filters */}
          <div className="flex flex-wrap items-center gap-2.5">
            <Select value={category} onChange={setCategory} label="Category">
              <option value="">All categories</option>
              {(filters.data?.categories?.length ? filters.data.categories : CATEGORIES).map((c) => (
                <option key={c} value={c}>{c}</option>
              ))}
            </Select>
            <Select value={tag} onChange={setTag} label="Tag">
              <option value="">All tags</option>
              {(filters.data?.tags || []).map((t) => (
                <option key={t.tag} value={t.tag}>{t.tag} ({t.count})</option>
              ))}
            </Select>
            <Select value={sort} onChange={setSort} label="Sort recordings">
              {SORTS.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
            </Select>
            {(eventId || folderId || category || tag || debounced) && (
              <button
                onClick={() => {
                  setCategory(""); setTag(""); setQuery("");
                  setParams(new URLSearchParams(scope === "all" ? {} : { scope }), { replace: true });
                }}
                className="inline-flex items-center gap-1 rounded-lg px-2 py-1.5 text-xs font-medium text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800"
              >
                <FiX /> Clear filters
              </button>
            )}
            <div className="ml-auto flex items-center gap-1 rounded-lg border border-slate-200 p-0.5 dark:border-slate-700">
              {[["grid", FiGrid], ["list", FiList]].map(([key, Icon]) => (
                <button
                  key={key} onClick={() => setView(key)} aria-label={`${key} view`}
                  aria-pressed={view === key}
                  className={cx("grid h-7 w-7 place-items-center rounded",
                    view === key ? "bg-slate-100 text-slate-800 dark:bg-slate-700 dark:text-slate-100" : "text-slate-400")}
                >
                  <Icon className="text-sm" />
                </button>
              ))}
            </div>
          </div>

          {library.error ? (
            <Card className="py-12 text-center text-sm text-rose-600 dark:text-rose-400">
              This library could not be loaded. <button onClick={library.reload} className="underline">Try again</button>
            </Card>
          ) : library.loading ? (
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {Array.from({ length: 6 }, (_, i) => (
                <Card key={i} padding="none" className="overflow-hidden">
                  <div className="zk-skeleton aspect-video bg-slate-100 dark:bg-slate-800" />
                  <div className="space-y-2 p-4">
                    <div className="zk-skeleton h-4 w-3/4 rounded bg-slate-100 dark:bg-slate-800" />
                    <div className="zk-skeleton h-3 w-1/2 rounded bg-slate-100 dark:bg-slate-800" />
                  </div>
                </Card>
              ))}
            </div>
          ) : items.length === 0 ? (
            <Card className="py-16 text-center">
              <FiFilm className="mx-auto mb-3 text-3xl text-slate-300 dark:text-slate-600" />
              <p className="font-medium text-slate-700 dark:text-slate-200">
                {scope === "deleted" ? "The recycle bin is empty"
                  : scope === "archived" ? "Nothing is archived"
                    : scope === "failed" ? "No failed captures — good"
                      : debounced || category || tag ? "No recordings match those filters"
                        : "No recordings yet"}
              </p>
              {scope === "all" && !debounced && !category && !tag && (
                <p className="mx-auto mt-1 max-w-md text-sm text-slate-500 dark:text-slate-400">
                  Recordings appear here automatically when a host records a live event, or when an
                  event has “record automatically” switched on.
                </p>
              )}
            </Card>
          ) : view === "grid" ? (
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4">
              {items.map((item) => (
                <RecordingCard key={item.id} item={item} onAction={onAction} />
              ))}
            </div>
          ) : (
            <Card padding="none" className="overflow-hidden">
              <DataTable columns={columns} rows={items} rowKey={(r) => r.id} minWidth={900} />
            </Card>
          )}

          {data && data.pages > 1 && (
            <div className="flex items-center justify-between text-sm">
              <p className="text-slate-500 dark:text-slate-400">
                Page {data.page} of {data.pages} · {fmtCount(data.total)} recordings
              </p>
              <div className="flex gap-2">
                <Button size="sm" variant="secondary" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
                  Previous
                </Button>
                <Button size="sm" variant="secondary" disabled={page >= data.pages} onClick={() => setPage((p) => p + 1)}>
                  Next
                </Button>
              </div>
            </div>
          )}
        </div>
      </div>

      <Modal open={newFolder} onClose={() => setNewFolder(false)} title="New folder">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            const name = new FormData(e.currentTarget).get("name");
            createFolder.run(() => api.post("/media/folders", { name }));
          }}
          className="space-y-4"
        >
          <label className="block">
            <span className="mb-1.5 block text-sm font-medium text-slate-700 dark:text-slate-200">Folder name</span>
            <input name="name" required maxLength={120} autoFocus className={cx(control, "w-full")} placeholder="Q3 town halls" />
          </label>
          <p className="text-xs text-slate-500 dark:text-slate-400">
            Folders nest one level deep. Deleting a folder never deletes recordings — they move back
            to the top of the library.
          </p>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="secondary" onClick={() => setNewFolder(false)}>Cancel</Button>
            <Button type="submit" disabled={createFolder.busy}>
              <FiCheck /> Create folder
            </Button>
          </div>
        </form>
      </Modal>

      <ConfirmDialog
        open={Boolean(confirm)}
        onClose={() => setConfirm(null)}
        onConfirm={runConfirm}
        busy={act.busy}
        title={confirm?.kind === "purge" ? "Permanently delete this recording?" : "Move to the recycle bin?"}
        confirmLabel={confirm?.kind === "purge" ? "Delete permanently" : "Move to bin"}
        body={
          confirm?.kind === "purge"
            ? `“${confirm?.item?.title}” and its stored video file will be deleted for good. This cannot be undone.`
            : `“${confirm?.item?.title}” will be restorable from the recycle bin for ${stats.data?.recycle_bin_days ?? 30} days. The video file is kept until it is purged.`
        }
      />
    </div>
  );
}

function FolderRow({ label, count, active, nested, onClick }) {
  return (
    <li>
      <button
        onClick={onClick}
        className={cx(
          "flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-sm transition",
          nested && "pl-6",
          active
            ? "bg-emerald-50 font-medium text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-400"
            : "text-slate-600 hover:bg-slate-50 dark:text-slate-300 dark:hover:bg-slate-800"
        )}
      >
        <FiFolder className="shrink-0 text-sm" />
        <span className="min-w-0 flex-1 truncate">{label}</span>
        {count != null && <span className="shrink-0 text-[11px] tabular-nums text-slate-400">{count}</span>}
      </button>
    </li>
  );
}

function Select({ value, onChange, label, children }) {
  return (
    <div className="relative">
      <select
        value={value} onChange={(e) => onChange(e.target.value)} aria-label={label}
        className={cx(control, "appearance-none pr-8")}
      >
        {children}
      </select>
      <FiChevronDown className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
    </div>
  );
}
