import { useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { FiPlus, FiSearch, FiEye, FiTrash2, FiCalendar, FiChevronDown, FiLink } from "react-icons/fi";
import api, { errMsg } from "../../api";
import useApi from "../../hooks/useApi";
import { notify } from "../../ui/Toast";
import OrganizationPageHeader from "../../components/organization/OrganizationPageHeader";
import OrganizationErrorState from "../../components/organization/OrganizationErrorState";
import StatCard from "../../components/admin/StatCard";
import { ConsoleButton as Button } from "../../ui/Button";
import Badge from "../../ui/Badge";
import DataTable from "../../components/admin/DataTable";
import { cx, focusRing } from "../../ui/tokens";
import { EVENT_STATUS, statusMeta, visLabel, fmtDateTime, fmtDuration } from "../../data/events";
import { copyViewerLink } from "../../utils/viewerLink";
import CreateEventModal from "./CreateEventModal";

const control = cx(
  "h-9 rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-700 outline-none",
  "focus:border-violet-400 focus:ring-2 focus:ring-violet-500/20",
  "dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200",
  focusRing
);

export default function OrganizationEvents() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  // `?create=true` is how everything outside this page asks for the dialog: the topbar's
  // "Request live event" button (OrganizationLayout renders it for every /organization/*
  // route), the dashboard CTA, and the empty-state button below.
  //
  // THE BUG THIS REPLACES: the parameter was read ONCE, in a useState initializer, and an
  // initializer runs only when the route mounts. From any other page that was fine —
  // clicking "Request live event" navigates here, /organization/events mounts, the
  // initializer sees create=true. On /organization/events itself there is no mount: the
  // topbar lives in the layout, so the click changes nothing but the query string and React
  // Router re-renders the same mounted element. The initializer never ran again and the
  // button did nothing at all, on that one route.
  //
  // So the request is now READ ON EVERY RENDER instead of once per mount, which is what
  // makes it work with or without a mount behind it. It is derived, not copied into state:
  // mirroring it with a setState would be a cascading render, and `set-state-in-effect` /
  // `set-state-in-render` both reject that here for good reason.
  const requestedViaUrl = searchParams.get("create") === "true";
  // Separate flag for the page's own "Create event" button, which opens the dialog directly
  // and must not touch the URL — its behaviour is exactly what it was.
  const [createOpen, setCreateOpen] = useState(false);
  const showCreate = createOpen || requestedViaUrl;

  // Clearing the parameter on close (not on open) is the same external-system
  // synchronization it always was: left in the URL, a refresh or a back-navigation would
  // reopen a dialog the reader had dismissed. Doing it here rather than in an effect also
  // makes a SECOND click work — the URL is back to /organization/events by then, so the
  // next navigate() is a real location change instead of a no-op to an identical one.
  // A new URLSearchParams keeps the router's own object immutable, as it expects.
  const closeCreate = () => {
    setCreateOpen(false);
    if (!requestedViaUrl) return;
    const next = new URLSearchParams(searchParams);
    next.delete("create");
    setSearchParams(next, { replace: true });
  };

  const { data, loading, error, reload } = useApi(() =>
    api.get("/events", { params: { page_size: 100 } }).then((r) => r.data.items)
  );
  const events = useMemo(() => data || [], [data]);

  const kpis = [
    { label: "Live", value: events.filter((e) => e.status === "live").length },
    { label: "Scheduled", value: events.filter((e) => e.status === "scheduled").length },
    { label: "Drafts", value: events.filter((e) => e.status === "draft").length },
    { label: "Ended", value: events.filter((e) => e.status === "ended").length },
  ];

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    return events.filter(
      (e) =>
        (statusFilter === "all" || e.status === statusFilter) &&
        (!q || (e.title || "").toLowerCase().includes(q) || (e.slug || "").toLowerCase().includes(q))
    );
  }, [events, query, statusFilter]);

  const del = async (ev) => {
    if (!window.confirm(`Delete "${ev.title || "this event"}"? This cannot be undone.`)) return;
    try {
      await api.delete(`/events/${ev.id}`);
      notify.success("Event deleted");
      reload();
    } catch (e) {
      notify.error(errMsg(e));
    }
  };

  const columns = [
    {
      key: "title",
      header: "Event",
      sortable: true,
      sortValue: (r) => (r.title || "").toLowerCase(),
      render: (r) => (
        <button
          onClick={() => navigate(`/organization/events/${r.id}`)}
          className={cx("text-left font-medium text-slate-800 hover:text-violet-600 dark:text-slate-100 dark:hover:text-violet-400", focusRing)}
        >
          {r.title || "Untitled event"}
          {r.slug && <span className="block text-xs font-normal text-slate-400">/{r.slug}</span>}
        </button>
      ),
    },
    {
      key: "status",
      header: "Status",
      sortable: true,
      sortValue: (r) => r.status,
      render: (r) => {
        const m = statusMeta(r.status);
        return <Badge tone={m.tone} dot={m.pulse}>{m.label}</Badge>;
      },
    },
    {
      key: "start_time",
      header: "Starts",
      sortable: true,
      sortValue: (r) => (r.start_time ? new Date(r.start_time).getTime() : 0),
      render: (r) => fmtDateTime(r.start_time),
    },
    { key: "visibility", header: "Visibility", render: (r) => visLabel(r.visibility) },
    {
      key: "registration_required",
      header: "Registration",
      render: (r) => (r.registration_required ? "Required" : "Open"),
    },
    {
      key: "duration_minutes",
      header: "Duration",
      align: "right",
      sortable: true,
      sortValue: (r) => r.duration_minutes ?? 0,
      render: (r) => fmtDuration(r.duration_minutes),
    },
    {
      key: "actions",
      header: "Actions",
      align: "right",
      // Icon-only: the rail is already six columns wide and the label lives in the tooltip
      // and the accessible name. Copy ONLY — there is deliberately no affordance here that
      // opens the attendee page, and the URL is never rendered.
      render: (r) => (
        <button
          type="button"
          title="Copy viewer link"
          aria-label={`Copy viewer link for ${r.title || "this event"}`}
          onClick={async (e) => {
            // The row itself navigates to the event; copying must not also open it.
            e.stopPropagation();
            if (await copyViewerLink(r.id)) notify.success("Viewer link copied");
            else notify.error("Unable to copy viewer link.");
          }}
          className={cx(
            "inline-grid h-8 w-8 place-items-center rounded-lg text-slate-400 transition-colors duration-150",
            "hover:bg-slate-100 hover:text-violet-600 dark:hover:bg-white/[0.08] dark:hover:text-violet-300",
            "motion-reduce:transition-none",
            focusRing
          )}
        >
          <FiLink aria-hidden="true" />
        </button>
      ),
    },
  ];

  return (
    <div className="space-y-6">
      <OrganizationPageHeader
        title="Events"
        subtitle="Manage all live events for your organization"
        actions={
          <>
            <div className="relative">
              <FiSearch className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
              <input
                type="search"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search events…"
                className={cx(control, "w-full pl-8 sm:w-52")}
              />
            </div>
            <div className="relative">
              <select
                value={statusFilter}
                onChange={(e) => setStatusFilter(e.target.value)}
                aria-label="Filter by status"
                className={cx(control, "appearance-none pr-8")}
              >
                <option value="all">All statuses</option>
                {Object.entries(EVENT_STATUS).map(([k, v]) => (
                  <option key={k} value={k}>{v.label}</option>
                ))}
              </select>
              <FiChevronDown className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
            </div>
            <Button size="sm" leftIcon={FiPlus} onClick={() => setCreateOpen(true)}>
              Create event
            </Button>
          </>
        }
      />

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
              pageSize={10}
              initialSort={{ key: "start_time", dir: "desc" }}
              minWidth={820}
              empty={{
                icon: FiCalendar,
                title: query || statusFilter !== "all" ? "No events match your filters" : "No events yet",
                description:
                  query || statusFilter !== "all"
                    ? "Try clearing the search or status filter."
                    : "Create your first event to start streaming.",
                action:
                  query || statusFilter !== "all" ? null : (
                    <Button size="sm" leftIcon={FiPlus} onClick={() => setCreateOpen(true)}>
                      Create event
                    </Button>
                  ),
              }}
              rowActions={(r) => (
                <>
                  <Button
                    variant="ghost"
                    size="sm"
                    iconOnly
                    leftIcon={FiEye}
                    aria-label={`View ${r.title || "event"}`}
                    onClick={() => navigate(`/organization/events/${r.id}`)}
                  />
                  <Button
                    variant="ghost"
                    size="sm"
                    iconOnly
                    leftIcon={FiTrash2}
                    aria-label={`Delete ${r.title || "event"}`}
                    className="text-rose-500 hover:bg-rose-50 hover:text-rose-600 dark:hover:bg-rose-500/10"
                    onClick={() => del(r)}
                  />
                </>
              )}
            />
          </div>
        </>
      )}

      <CreateEventModal open={showCreate} onClose={closeCreate} onCreated={reload} />
    </div>
  );
}
