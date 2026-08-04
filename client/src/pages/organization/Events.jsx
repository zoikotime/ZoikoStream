import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  FiPlus, FiSearch, FiEye, FiTrash2, FiCalendar, FiChevronDown, FiCopy, FiEdit2,
  FiUploadCloud, FiArchive, FiSlash, FiX, FiRadio,
} from "react-icons/fi";
import api from "../../api";
import useApi from "../../hooks/useApi";
import useMutation from "../../hooks/useMutation";
import { notify } from "../../ui/Toast";
import { useAuth } from "../../auth/AuthContext";
import OrganizationPageHeader from "../../components/organization/OrganizationPageHeader";
import OrganizationErrorState from "../../components/organization/OrganizationErrorState";
import EventStatusBadge from "../../components/organization/EventStatusBadge";
import StatCard from "../../components/admin/StatCard";
import { ConsoleButton as Button } from "../../ui/Button";
import Badge from "../../ui/Badge";
import ConfirmDialog from "../../ui/ConfirmDialog";
import DataTable from "../../components/admin/DataTable";
import { cx, focusRing } from "../../ui/tokens";
import {
  STATUS_ORDER, TEAM_ROLES, VISIBILITY_LABEL, fmtDateTime, isOnAir, statusMeta, visLabel,
} from "../../data/events";
import EventFormModal from "./EventFormModal";

// The Organization Admin's event control panel. Route: /organization/events
//
// Filtering, sorting and pagination are ALL server-side (GET /events takes q, statuses,
// visibility, category, sort_by, order, page, page_size). The previous version fetched
// page_size=100 and narrowed in the browser, which silently hid every event past the 100th.
//
// Only org admins reach this route (RoleRoute in App.jsx) — but the mutating controls are
// additionally gated on the caller's role here, so a host who ever lands on the page sees a
// read-only table instead of buttons the API would reject.

const PAGE_SIZE = 25;
const DEBOUNCE_MS = 300;

const control = cx(
  "h-9 rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-700 outline-none",
  "focus:border-violet-400 focus:ring-2 focus:ring-violet-500/20",
  "dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200",
  focusRing
);

// Bulk verbs the API accepts (POST /events/bulk), with the wording the confirmation uses.
const BULK = {
  publish: { label: "Publish", icon: FiUploadCloud, verb: "publish" },
  unpublish: { label: "Unpublish", icon: FiSlash, verb: "move back to draft" },
  cancel: { label: "Cancel", icon: FiSlash, verb: "cancel", danger: true },
  archive: { label: "Archive", icon: FiArchive, verb: "archive" },
  delete: { label: "Delete", icon: FiTrash2, verb: "delete", danger: true },
};

/** Small count chip used by the Hosts/Moderators/Speakers columns. Renders a real 0 rather
 *  than an em-dash — "no hosts assigned" is information the admin needs to act on. */
function CountChip({ n, warn }) {
  return (
    <span
      className={cx(
        "inline-flex min-w-6 justify-center rounded-md px-1.5 py-0.5 text-xs font-semibold tabular-nums",
        n === 0 && warn
          ? "bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-400"
          : "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300"
      )}
    >
      {n}
    </span>
  );
}

/** Recording / Replay cell: on|off from config, plus whether an artefact actually exists.
 *  A switch being on is a promise; a file existing is a fact — the cell shows both. */
function MediaCell({ enabled, present, label }) {
  if (!enabled) return <span className="text-slate-400">Off</span>;
  return (
    <span className="inline-flex items-center gap-1.5">
      <Badge tone={present ? "success" : "neutral"} size="sm">{present ? "Ready" : "On"}</Badge>
      <span className="sr-only">{label} {present ? "available" : "enabled but not captured yet"}</span>
    </span>
  );
}

export default function OrganizationEvents() {
  const navigate = useNavigate();
  const { user } = useAuth();
  const canManage = ["org_admin", "super_admin"].includes(user?.role);

  const [searchParams, setSearchParams] = useSearchParams();
  const [formEvent, setFormEvent] = useState(null); // the event being edited
  const [formOpen, setFormOpen] = useState(false);
  const [confirm, setConfirm] = useState(null);     // { kind, event? , ids?, action?, clear? }

  // ── query state (drives the request) ──────────────────────────────────────
  const [search, setSearch] = useState("");
  const [debounced, setDebounced] = useState("");
  const [status, setStatus] = useState("all");
  const [visibility, setVisibility] = useState("all");
  const [category, setCategory] = useState("all");
  const [sort, setSort] = useState({ key: "start_time", dir: "desc" });
  const [page, setPage] = useState(1);

  // Debounced so typing doesn't fire a request per keystroke (the audit's P13).
  useEffect(() => {
    const id = setTimeout(() => setDebounced(search.trim()), DEBOUNCE_MS);
    return () => clearTimeout(id);
  }, [search]);

  // Any filter change invalidates the current page number — page 7 of a narrower result set
  // is usually empty, which reads as "no events". Reset during render (the pattern
  // components/admin/DataTable uses) so the stale page is never requested at all; an effect
  // would fire a request for page 7 first and then a second one for page 1.
  const filterKey = [debounced, status, visibility, category, sort?.key, sort?.dir].join("|");
  const [prevFilterKey, setPrevFilterKey] = useState(filterKey);
  if (filterKey !== prevFilterKey) {
    setPrevFilterKey(filterKey);
    setPage(1);
  }

  const params = useMemo(
    () => ({
      page,
      page_size: PAGE_SIZE,
      sort_by: sort?.key || "created_at",
      order: sort?.dir || "desc",
      ...(debounced ? { q: debounced } : {}),
      ...(status !== "all" ? { status } : {}),
      ...(visibility !== "all" ? { visibility } : {}),
      ...(category !== "all" ? { category } : {}),
    }),
    [page, sort, debounced, status, visibility, category]
  );

  // useApi holds the latest thunk in a ref and re-runs it on reload(), so `params` is read
  // at call time. The effect below turns a filter change into a refetch; it skips the FIRST
  // run because useApi already fetched on mount (otherwise every page load costs two
  // requests).
  const { data, loading, error, reload } = useApi(() =>
    api.get("/events", { params }).then((r) => r.data)
  );
  const mounted = useRef(false);
  useEffect(() => {
    if (!mounted.current) {
      mounted.current = true;
      return;
    }
    reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params]);

  const { data: categories } = useApi(() =>
    api.get("/events/categories").then((r) => r.data)
  );

  // Stable identity so the column/KPI memos below don't recompute on every render.
  const rows = useMemo(() => data?.items || [], [data]);
  const total = data?.total ?? 0;

  const mutate = useMutation({ onDone: reload });

  const openCreate = useCallback(() => {
    setFormEvent(null);
    setFormOpen(true);
  }, []);

  // ?create=true (from the dashboard's quick action) opens the form. DERIVED from the URL
  // rather than copied into state by an effect: the URL is the source of truth, and closing
  // the dialog is what clears the parameter.
  const wantsCreate = searchParams.get("create") === "true";
  const closeForm = useCallback(() => {
    setFormOpen(false);
    if (searchParams.get("create")) {
      searchParams.delete("create");
      setSearchParams(searchParams, { replace: true });
    }
  }, [searchParams, setSearchParams]);

  // ── row + bulk actions ────────────────────────────────────────────────────

  const duplicate = (ev) =>
    mutate.run(() => api.post(`/events/${ev.id}/duplicate`, { copy_team: true }), {
      success: (res) => `Duplicated as "${res.data.title}" (draft)`,
    });

  const runConfirm = async () => {
    if (confirm.kind === "delete-one") {
      const ok = await mutate.run(() => api.delete(`/events/${confirm.event.id}`), {
        success: "Event deleted",
      });
      if (ok) setConfirm(null);
      return;
    }
    // Bulk: the API reports per-id outcomes, so a partial success is stated rather than
    // rounded up to "done".
    const res = await mutate.run(
      () => api.post("/events/bulk", { action: confirm.action, ids: confirm.ids }),
      { success: null }
    );
    if (!res) return;
    const { succeeded = [], failed = [] } = res.data || {};
    if (succeeded.length) {
      notify.success(`${succeeded.length} event${succeeded.length === 1 ? "" : "s"} ${BULK[confirm.action].verb}d`);
    }
    if (failed.length) {
      // Name the first reason — the whole list would be a wall of toast, and the reasons
      // repeat (usually "Cannot publish an event without a title").
      notify.error(`${failed.length} skipped — ${failed[0].reason}`);
    }
    confirm.clear?.();   // drop the checkboxes; the rows they pointed at have changed
    setConfirm(null);
  };

  const kpis = useMemo(() => {
    // Counts describe the CURRENT result set, and the label says so — a KPI that silently
    // means something different from the table below it is worse than no KPI.
    const of = (pred) => rows.filter(pred).length;
    return [
      { label: "On air (this page)", value: of((e) => isOnAir(e.status)) },
      { label: "Scheduled (this page)", value: of((e) => e.status === "scheduled") },
      { label: "Drafts (this page)", value: of((e) => e.status === "draft") },
      { label: "Matching events", value: total },
    ];
  }, [rows, total]);

  const filtersActive = !!debounced || status !== "all" || visibility !== "all" || category !== "all";
  const clearFilters = () => {
    setSearch("");
    setStatus("all");
    setVisibility("all");
    setCategory("all");
  };

  const columns = useMemo(
    () => [
      {
        key: "title",
        header: "Event name",
        sortable: true,
        width: 260,
        render: (r) => (
          <button
            onClick={() => navigate(`/organization/events/${r.id}`)}
            className={cx(
              "max-w-[240px] text-left font-medium text-slate-800 hover:text-violet-600",
              "dark:text-slate-100 dark:hover:text-violet-400",
              focusRing
            )}
          >
            <span className="block truncate">{r.title || "Untitled event"}</span>
            {r.slug && <span className="block truncate text-xs font-normal text-slate-400">/{r.slug}</span>}
          </button>
        ),
      },
      { key: "status", header: "Status", sortable: true, render: (r) => <EventStatusBadge status={r.status} size="sm" /> },
      { key: "start_time", header: "Start", sortable: true, render: (r) => fmtDateTime(r.start_time) },
      { key: "end_time", header: "End", sortable: true, render: (r) => fmtDateTime(r.end_time) },
      {
        key: "organization_name",
        header: "Organization",
        render: (r) => <span className="truncate">{r.organization_name || "—"}</span>,
      },
      ...["host", "moderator", "speaker"].map((role) => ({
        key: `team_${role}`,
        header: TEAM_ROLES.find((t) => t.key === role).plural,
        align: "right",
        // A published event with no host cannot be broadcast — flag the zero.
        render: (r) => <CountChip n={r.team_counts?.[role] || 0} warn={role === "host"} />,
      })),
      {
        key: "registrations",
        header: "Registrations",
        align: "right",
        render: (r) =>
          r.registrations != null ? (
            r.registrations
          ) : (
            // Honest: this platform has no registrations table yet, so there is no count to
            // show. What IS known is whether registration is required and its cap.
            <span
              className="text-slate-400"
              title="Attendee registration is not collected yet — this shows the event's configuration."
            >
              {r.registration_required ? `Req.${r.registration_limit ? ` / ${r.registration_limit}` : ""}` : "Open"}
            </span>
          ),
      },
      {
        key: "current_viewers",
        header: "Viewers",
        align: "right",
        render: (r) =>
          r.current_viewers != null ? (
            <span className="font-semibold text-slate-800 dark:text-slate-100">{r.current_viewers}</span>
          ) : (
            <span className="text-slate-400">—</span>
          ),
      },
      {
        key: "recording_enabled",
        header: "Recording",
        render: (r) => <MediaCell enabled={r.recording_enabled} present={r.has_recording} label="Recording" />,
      },
      {
        key: "replay_enabled",
        header: "Replay",
        render: (r) => <MediaCell enabled={r.replay_enabled} present={r.has_replay} label="Replay" />,
      },
      { key: "visibility", header: "Visibility", render: (r) => visLabel(r.visibility) },
      {
        key: "created_by_name",
        header: "Created by",
        render: (r) => <span className="truncate">{r.created_by_name || "—"}</span>,
      },
      { key: "updated_at", header: "Last updated", sortable: true, render: (r) => fmtDateTime(r.updated_at) },
    ],
    [navigate]
  );

  return (
    <div className="space-y-6">
      <OrganizationPageHeader
        title="Events"
        subtitle="Create, configure, schedule and monitor every live event"
        actions={
          canManage && (
            <Button size="sm" leftIcon={FiPlus} onClick={openCreate}>
              Create event
            </Button>
          )
        }
      />

      {/* Filter bar. Its own row rather than crammed into the header so it stays usable at
          360px, and every control carries a label for screen readers. */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-0 flex-1 sm:max-w-xs">
          <FiSearch className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" aria-hidden="true" />
          <input
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search events…"
            aria-label="Search events by title, description or slug"
            className={cx(control, "w-full pl-8")}
          />
        </div>

        {[
          { value: status, set: setStatus, label: "status", all: "All statuses",
            options: STATUS_ORDER.map((s) => [s, statusMeta(s).label]) },
          { value: visibility, set: setVisibility, label: "visibility", all: "All visibility",
            options: Object.entries(VISIBILITY_LABEL) },
          { value: category, set: setCategory, label: "category", all: "All categories",
            options: (categories || []).map((c) => [c, c]) },
        ].map(({ value, set, label, all, options }) => (
          <div key={label} className="relative">
            <select
              value={value}
              onChange={(e) => set(e.target.value)}
              aria-label={`Filter by ${label}`}
              className={cx(control, "appearance-none pr-8")}
            >
              <option value="all">{all}</option>
              {options.map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
            <FiChevronDown className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-400" aria-hidden="true" />
          </div>
        ))}

        {filtersActive && (
          <Button variant="ghost" size="sm" leftIcon={FiX} onClick={clearFilters}>
            Clear filters
          </Button>
        )}
      </div>

      {error ? (
        <OrganizationErrorState error={error} onRetry={reload} title="Couldn't load events" />
      ) : (
        <>
          <div className="grid grid-cols-2 gap-4 xl:grid-cols-4">
            {kpis.map((k) => (
              <StatCard key={k.label} label={k.label} value={k.value} loading={loading} />
            ))}
          </div>

          <div className="rounded-xl border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900/50">
            <DataTable
              columns={columns}
              rows={rows}
              rowKey={(r) => r.id}
              loading={loading}
              minWidth={1500}
              // Server-side: the API orders and pages; the table renders one page.
              pageSize={PAGE_SIZE}
              total={total}
              page={page}
              onPageChange={setPage}
              sort={sort}
              onSortChange={setSort}
              selectable={canManage}
              bulkActions={({ selected, clear }) => (
                <div className="flex flex-wrap items-center gap-1.5">
                  {Object.entries(BULK).map(([action, { label, icon, danger }]) => (
                    <Button
                      key={action}
                      variant={danger ? "ghost" : "secondary"}
                      size="sm"
                      leftIcon={icon}
                      disabled={mutate.busy}
                      className={danger ? "text-rose-500 hover:bg-rose-50 hover:text-rose-600 dark:hover:bg-rose-500/10" : ""}
                      onClick={() => setConfirm({ kind: "bulk", action, ids: selected, clear })}
                    >
                      {label}
                    </Button>
                  ))}
                </div>
              )}
              empty={{
                icon: FiCalendar,
                title: filtersActive ? "No events match your filters" : "No events yet",
                description: filtersActive
                  ? "Try clearing the search or filters."
                  : "Create your first event to start streaming.",
                action: filtersActive ? (
                  <Button size="sm" variant="secondary" leftIcon={FiX} onClick={clearFilters}>
                    Clear filters
                  </Button>
                ) : (
                  canManage && (
                    <Button size="sm" leftIcon={FiPlus} onClick={openCreate}>
                      Create event
                    </Button>
                  )
                ),
              }}
              rowActions={(r) => (
                <>
                  <Button
                    variant="ghost"
                    size="sm"
                    iconOnly
                    leftIcon={FiEye}
                    aria-label={`Open ${r.title || "event"}`}
                    onClick={() => navigate(`/organization/events/${r.id}`)}
                  />
                  {isOnAir(r.status) && (
                    <Button
                      variant="ghost"
                      size="sm"
                      iconOnly
                      leftIcon={FiRadio}
                      href={`/events/${r.id}/watch`}
                      aria-label={`Watch ${r.title || "event"}`}
                    />
                  )}
                  {canManage && (
                    <>
                      <Button
                        variant="ghost"
                        size="sm"
                        iconOnly
                        leftIcon={FiEdit2}
                        aria-label={`Edit ${r.title || "event"}`}
                        onClick={() => {
                          setFormEvent(r);
                          setFormOpen(true);
                        }}
                      />
                      <Button
                        variant="ghost"
                        size="sm"
                        iconOnly
                        leftIcon={FiCopy}
                        disabled={mutate.busy}
                        aria-label={`Duplicate ${r.title || "event"}`}
                        onClick={() => duplicate(r)}
                      />
                      <Button
                        variant="ghost"
                        size="sm"
                        iconOnly
                        leftIcon={FiTrash2}
                        aria-label={`Delete ${r.title || "event"}`}
                        className="text-rose-500 hover:bg-rose-50 hover:text-rose-600 dark:hover:bg-rose-500/10"
                        onClick={() => setConfirm({ kind: "delete-one", event: r })}
                      />
                    </>
                  )}
                </>
              )}
            />
          </div>
        </>
      )}

      <EventFormModal
        open={formOpen || (wantsCreate && !formEvent)}
        event={formEvent}
        onClose={closeForm}
        onSaved={reload}
      />

      <ConfirmDialog
        open={!!confirm}
        onClose={() => setConfirm(null)}
        onConfirm={runConfirm}
        busy={mutate.busy}
        tone={confirm?.kind === "delete-one" || BULK[confirm?.action]?.danger ? "danger" : "primary"}
        title={
          confirm?.kind === "delete-one"
            ? "Delete this event?"
            : `${BULK[confirm?.action]?.label || "Apply"} ${confirm?.ids?.length || 0} event${confirm?.ids?.length === 1 ? "" : "s"}?`
        }
        confirmLabel={confirm?.kind === "delete-one" ? "Delete" : BULK[confirm?.action]?.label || "Apply"}
        body={
          confirm?.kind === "delete-one" ? (
            <>
              <strong className="font-semibold text-slate-800 dark:text-slate-100">
                {confirm.event.title || "This event"}
              </strong>{" "}
              will be removed from your events list. Its recordings, chat log and audit trail
              are retained.
            </>
          ) : (
            <>
              This will {BULK[confirm?.action]?.verb} {confirm?.ids?.length} selected event
              {confirm?.ids?.length === 1 ? "" : "s"}.
              <p className="mt-2 text-slate-500 dark:text-slate-400">
                Events that cannot make this transition are skipped and reported — nothing else
                in the selection is affected.
              </p>
            </>
          )
        }
      />
    </div>
  );
}
