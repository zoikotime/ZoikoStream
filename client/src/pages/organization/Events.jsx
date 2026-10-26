import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import {
  FiAlertTriangle, FiCalendar, FiCheck, FiChevronDown, FiChevronRight, FiClock, FiLink, FiList,
  FiPlay, FiPlus, FiRadio, FiSearch, FiShield, FiTrash2,
} from "react-icons/fi";
import api, { errMsg } from "../../api";
import useInterval from "../../hooks/useInterval";
import { notify } from "../../ui/Toast";
import OrganizationPageHeader from "../../components/organization/OrganizationPageHeader";
import OrganizationErrorState from "../../components/organization/OrganizationErrorState";
import { Banner, Footnote, KpiRow, KpiTile, Panel, PanelLink } from "../../components/organization/console/ConsoleUI";
import RowActionsMenu from "../../components/organization/console/RowActionsMenu";
import { ConsoleButton as Button } from "../../ui/Button";
import Badge from "../../ui/Badge";
import DataTable from "../../components/admin/DataTable";
import { CONSOLE, actionStops, cx, focusRing } from "../../ui/tokens";
import { STATUS_FILTER_OPTIONS, displayStatusOf, statusMeta, visLabel } from "../../data/events";
import { dayKey, monthKey } from "../../data/eventCalendar";
import { safeZone } from "../../data/eventTime";
import { copyViewerLink } from "../../utils/viewerLink";
import CreateEventModal from "./CreateEventModal";
import EventsCalendar from "./EventsCalendar";

// Events (ZST-UX-NAV-001 §4, §5; ZST-ENG-DASH-001 ORG-02): the organization's event portfolio.
//
// Everything the page shows comes from the server for the WHOLE organization: the list, its
// counters, the readiness of each upcoming event (the same gates its Preparation section
// shows) and what needs attention. The filters live in the URL (?status=, ?range=, ?q=,
// ?view=calendar), so the rail's "Live now", a dashboard card, the topbar search and Back all
// land on the same view.

const PAGE_SIZE = 10;
// How often an open Events page re-reads statuses, so a host going live (or ending) shows up
// here without a reload. Skipped while the tab is hidden.
const REFRESH_MS = 15000;
// Columns the API can sort by (crud.event._EVENT_SORTS). Duration is derived, so it is not one.
const SORT_FIELD = { title: "title", status: "status", start_time: "start_time" };
// "on_air" is Live now (live AND degraded: crud/event.DISPLAY_ON_AIR); "upcoming" is every
// pre-live, non-draft status, Missed excluded (DISPLAY_UPCOMING).
const ON_AIR_FILTER = "on_air";
const UPCOMING_FILTER = "upcoming";
const STATUS_VALUES = new Set(["all", ON_AIR_FILTER, UPCOMING_FILTER, ...STATUS_FILTER_OPTIONS.map(([k]) => k)]);
const TABS = [
  ["all", "All"],
  [UPCOMING_FILTER, "Upcoming"],
  [ON_AIR_FILTER, "Live"],
  ["ended", "Completed"],
  ["draft", "Drafts"],
];
const RANGES = {
  "": { label: "Any date" },
  next7: { label: "Next 7 days", from: 0, to: 7 },
  next30: { label: "Next 30 days", from: 0, to: 30 },
  past30: { label: "Past 30 days", from: -30, to: 0 },
};
const SORTS = {
  "start_time:desc": "Sort: Latest date",
  "start_time:asc": "Sort: Next date",
  "title:asc": "Sort: Title A–Z",
};

function rangeParams(key, now = Date.now()) {
  const r = RANGES[key];
  if (!r || r.from === undefined) return {};
  const day = 86400000;
  return { date_from: new Date(now + r.from * day).toISOString(), date_to: new Date(now + r.to * day).toISOString() };
}

// The page's list, counters and attention summary, all from the server. A failed background
// refresh keeps what is on screen; only a failure with nothing to show becomes the error state.
function useEventsPage({ status, q, page, sort, range }) {
  const [state, setState] = useState({ key: null, data: null, error: null });
  const [tick, setTick] = useState(0);
  const key = JSON.stringify({ status, q, page, sort, range });
  useEffect(() => {
    let alive = true;
    const now = Date.now();
    Promise.all([
      api.get("/events", {
        params: {
          page,
          page_size: PAGE_SIZE,
          q: q || undefined,
          // The DISPLAY filter: "missed" is derived by the API, and "scheduled" there means
          // still upcoming — the same split the table and the counters show.
          display_status: status === "all" ? undefined : status,
          sort_by: (sort && SORT_FIELD[sort.key]) || undefined,
          order: sort && SORT_FIELD[sort.key] ? sort.dir : undefined,
          ...rangeParams(range, now),
        },
      }),
      api.get("/events/status-counts"),
    ])
      .then(([list, counts]) => {
        if (!alive) return;
        // display_counts: Scheduled is upcoming only and Missed is counted apart. An older API
        // without it falls back to the canonical counts.
        setState({ key, error: null, data: {
          items: list.data.items, total: list.data.total,
          counts: counts.data.display_counts || counts.data.counts,
          attention: counts.data.attention || null,
        } });
      })
      .catch((e) => {
        if (alive) setState((prev) => (prev.data ? { ...prev, key } : { key, data: null, error: e }));
      });
    return () => { alive = false; };
  }, [key, status, q, page, sort, range, tick]);
  return {
    ...state,
    // Loading is derived: the request for the current filters has not answered yet.
    loading: state.key !== key,
    refresh: () => setTick((t) => t + 1),
  };
}

// The side panels: events in the next 30 days (the KPI's count, and the schedule list) and
// this month's events for the mini calendar. Reads only; a failure greys out its own panel.
function useSideData(tick) {
  const [side, setSide] = useState({ upcoming: undefined, month: undefined, error: null });
  useEffect(() => {
    let alive = true;
    const now = new Date();
    const from = new Date(now.getFullYear(), now.getMonth(), 1);
    const to = new Date(now.getFullYear(), now.getMonth() + 1, 1);
    Promise.allSettled([
      api.get("/events", { params: { date_from: now.toISOString(), date_to: new Date(now.getTime() + 30 * 86400000).toISOString(), sort_by: "start_time", order: "asc", page_size: 3 } }),
      api.get("/events", { params: { date_from: from.toISOString(), date_to: to.toISOString(), sort_by: "start_time", order: "asc", page_size: 100 } }),
    ]).then(([up, month]) => {
      if (!alive) return;
      setSide({
        upcoming: up.status === "fulfilled" ? up.value.data : null,
        month: month.status === "fulfilled" ? month.value.data : null,
        error: up.status === "rejected" ? up.reason : null,
      });
    });
    return () => { alive = false; };
  }, [tick]);
  return side;
}

const fmtDay = (iso, zone) => {
  const d = iso ? new Date(iso) : null;
  if (!d || Number.isNaN(d.getTime())) return { date: "Date not set", time: "—" };
  const timeZone = safeZone(zone);
  return {
    date: d.toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric", timeZone }),
    time: `${d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", timeZone })} ${
      d.toLocaleTimeString("en-GB", { timeZoneName: "short", timeZone }).split(" ").pop()}`,
  };
};

function Thumb({ event }) {
  const src = event.thumbnail || event.banner_image;
  const Icon = ["live", "degraded", "ended"].includes(event.status) ? FiPlay : FiCalendar;
  return src ? (
    <img src={src} alt="" className="h-9 w-12 shrink-0 rounded-lg object-cover" />
  ) : (
    <span aria-hidden="true" className="grid h-9 w-12 shrink-0 place-items-center rounded-lg bg-gradient-to-br from-[#2b2a6b] via-[#5b3aa6] to-[#c0478f] text-white/90">
      <Icon />
    </span>
  );
}

function Readiness({ event }) {
  const row = (Icon, text, cls) => (
    <span className={cx("inline-flex items-center gap-1.5 text-[12.5px]", cls)}>
      <Icon aria-hidden="true" className="shrink-0" /> {text}
    </span>
  );
  if (["live", "degraded"].includes(event.status)) return row(FiCheck, "On air · monitoring", CONSOLE.muted);
  if (event.status === "ended") return row(FiCheck, "Event ended", CONSOLE.muted);
  if (event.status === "cancelled") return row(FiClock, "Cancelled", CONSOLE.faint);
  if (event.status === "archived") return row(FiClock, "Archived", CONSOLE.faint);
  if (event.status === "draft") return row(FiClock, "Setup incomplete", CONSOLE.muted);
  // Missed already says the start passed, once, in Status — readiness no longer applies, and
  // repeating the warning here would be the second status the Missed badge replaced.
  if (displayStatusOf(event) === "missed") return row(FiClock, "No longer applicable", CONSOLE.faint);
  if (event.readiness_verdict === "blocked") {
    return row(FiAlertTriangle, `${event.readiness_blockers} required check${event.readiness_blockers === 1 ? "" : "s"} pending`, "font-semibold text-amber-600 dark:text-amber-300");
  }
  if (event.readiness_verdict === "conditional") {
    return row(FiClock, `${event.readiness_failing} recommended check${event.readiness_failing === 1 ? "" : "s"} open`, CONSOLE.muted);
  }
  if (event.readiness_verdict === "passed") return row(FiCheck, "Ready", "font-semibold text-emerald-600 dark:text-emerald-300");
  return row(FiClock, "Checks in progress", CONSOLE.muted);
}

function MiniCalendar({ month: data, onOpen }) {
  const now = new Date();
  const first = new Date(now.getFullYear(), now.getMonth(), 1);
  const lead = first.getDay();
  const days = new Date(now.getFullYear(), now.getMonth() + 1, 0).getDate();
  const byDay = useMemo(() => {
    const m = {};
    for (const e of data?.items || []) {
      if (!e.start_time) continue;
      const k = dayKey(new Date(e.start_time));
      (m[k] ||= []).push(e);
    }
    return m;
  }, [data]);
  const cells = Array.from({ length: Math.ceil((lead + days) / 7) * 7 }, (_, i) => {
    const d = i - lead + 1;
    return d >= 1 && d <= days ? new Date(now.getFullYear(), now.getMonth(), d) : null;
  });
  const today = dayKey(now);
  const label = now.toLocaleDateString("en-US", { month: "long", year: "numeric" });
  return (
    <Panel title="Event calendar" subtitle={`${label} · your time zone`} action={<PanelLink to={`/organization/events?view=calendar&month=${monthKey(now)}`}>Open calendar</PanelLink>}>
      <div className="grid grid-cols-7 gap-y-1 text-center text-[12px]" aria-label={`Events in ${label}`}>
        {["S", "M", "T", "W", "T", "F", "S"].map((d, i) => (
          <span key={`${d}-${i}`} aria-hidden="true" className={cx("pb-1 text-[11px] font-semibold", CONSOLE.faint)}>{d}</span>
        ))}
        {cells.map((date, i) => {
          if (!date) return <span key={`pad-${i}`} />;
          const k = dayKey(date);
          const list = byDay[k] || [];
          const live = list.some((e) => ["live", "degraded"].includes(e.status));
          return (
            <button
              key={k}
              type="button"
              onClick={() => onOpen(list)}
              disabled={!list.length}
              aria-label={`${date.toLocaleDateString("en-US", { month: "long", day: "numeric" })}${list.length ? `, ${list.length} event${list.length === 1 ? "" : "s"}` : ""}`}
              className={cx(
                "relative mx-auto grid h-8 w-8 place-items-center rounded-lg font-medium",
                focusRing,
                k === today ? cx("bg-gradient-to-br text-white", actionStops) : list.length ? cx(CONSOLE.heading, "hover:bg-violet-50 dark:hover:bg-white/[0.06]") : CONSOLE.muted
              )}
            >
              {date.getDate()}
              {list.length > 0 && (
                <span aria-hidden="true" className={cx("absolute bottom-0.5 h-1 w-1 rounded-full", live ? "bg-rose-500" : k === today ? "bg-white" : "bg-violet-500")} />
              )}
            </button>
          );
        })}
      </div>
      <p className={cx("mt-3 flex items-center gap-3 text-[11px]", CONSOLE.faint)}>
        <span className="inline-flex items-center gap-1"><span className="h-1.5 w-1.5 rounded-full bg-rose-500" aria-hidden="true" /> Live</span>
        <span className="inline-flex items-center gap-1"><span className="h-1.5 w-1.5 rounded-full bg-violet-500" aria-hidden="true" /> Scheduled</span>
      </p>
      {data === null && <Footnote>This month&rsquo;s events couldn&rsquo;t be read.</Footnote>}
    </Panel>
  );
}

function NextOnSchedule({ upcoming, onOpen }) {
  const items = upcoming?.items || [];
  return (
    <Panel title="Next on the schedule" subtitle="Upcoming events in the next 30 days" action={<PanelLink to="/organization/events?status=upcoming">See all</PanelLink>}>
      {upcoming === null ? (
        <p className={cx("text-sm", CONSOLE.muted)}>The schedule couldn&rsquo;t be read.</p>
      ) : upcoming === undefined ? (
        <p className={cx("text-sm", CONSOLE.faint)}>Loading…</p>
      ) : items.length === 0 ? (
        <p className={cx("text-sm", CONSOLE.muted)}>Nothing in the next 30 days.</p>
      ) : (
        <ul className={cx("divide-y border-t", CONSOLE.divider, CONSOLE.divideY)}>
          {items.map((e) => {
            const d = new Date(e.start_time);
            const zone = safeZone(e.timezone);
            const st = statusMeta(displayStatusOf(e));
            return (
              <li key={e.id}>
                <button type="button" onClick={() => onOpen(e.id)} className={cx("flex w-full items-center gap-3 py-3 text-left", focusRing)}>
                  <span className="grid h-12 w-12 shrink-0 place-content-center rounded-xl bg-violet-50 text-center text-violet-700 dark:bg-violet-500/15 dark:text-violet-200">
                    <span className="text-[16px] font-bold leading-none">{d.toLocaleDateString("en-GB", { day: "2-digit", timeZone: zone })}</span>
                    <span className="text-[10px] font-bold uppercase">{d.toLocaleDateString("en-GB", { month: "short", timeZone: zone })}</span>
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className={cx("block truncate text-[13px] font-semibold", CONSOLE.heading)}>{e.title || "Untitled event"}</span>
                    <span className={cx("block text-[11.5px]", CONSOLE.faint)}>{fmtDay(e.start_time, e.timezone).time} · {st.label}</span>
                  </span>
                  <FiChevronRight aria-hidden="true" className={CONSOLE.faint} />
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </Panel>
  );
}

export default function OrganizationEvents() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  // The search box follows ?q= too, so the console's topbar search lands here pre-filled —
  // and a second search from the topbar, with this page already open, replaces the first.
  const urlQ = searchParams.get("q") || "";
  const [query, setQuery] = useState(urlQ);
  const [seenQ, setSeenQ] = useState(urlQ);
  if (urlQ !== seenQ) {
    setSeenQ(urlQ);
    setQuery(urlQ);
  }
  const [q, setQ] = useState(urlQ);
  const requestedStatus = searchParams.get("status") || "all";
  const statusFilter = STATUS_VALUES.has(requestedStatus) ? requestedStatus : "all";
  const view = searchParams.get("view") === "calendar" ? "calendar" : "list";
  const range = RANGES[searchParams.get("range") || ""] ? searchParams.get("range") || "" : "";
  const [page, setPage] = useState(1);
  const [sort, setSort] = useState({ key: "start_time", dir: "desc" });
  // One writer for the page's URL state. Functional, so it never drops a parameter another
  // control set in the same tick (create=true from the topbar, the month of the calendar).
  const setUrl = (patch) => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      for (const [k, v] of Object.entries(patch)) {
        if (v == null || v === "" || (k === "status" && v === "all")) next.delete(k);
        else next.set(k, v);
      }
      return next;
    }, { replace: true });
  };
  const setStatusFilter = (value) => setUrl({ status: value });
  // `?create=true` is how everything outside this page asks for the dialog (the topbar and
  // rail buttons, the dashboard). It is read on EVERY render, not once per mount, so the
  // request works whether or not the page was already open (the reported bug).
  const requestedViaUrl = searchParams.get("create") === "true";
  // The page's own "Create event" button opens the dialog directly and never touches the URL.
  const [createOpen, setCreateOpen] = useState(false);
  const showCreate = createOpen || requestedViaUrl;
  const closeCreate = () => {
    setCreateOpen(false);
    if (!requestedViaUrl) return;
    const next = new URLSearchParams(searchParams);
    next.delete("create");
    setSearchParams(next, { replace: true });
  };

  // Debounced, and a new search starts from its own first page.
  useEffect(() => {
    const t = setTimeout(() => {
      setQ(query.trim());
      setPage(1);
    }, 300);
    return () => clearTimeout(t);
  }, [query]);

  const { data, loading, error, refresh: reload } = useEventsPage({ status: statusFilter, q, page, sort, range });
  const [sideTick, setSideTick] = useState(0);
  const side = useSideData(sideTick);
  useInterval(() => {
    if (!document.hidden) reload();
  }, REFRESH_MS);
  const reloadAll = () => {
    reload();
    setSideTick((t) => t + 1);
  };

  const rows = data?.items || [];
  const total = data?.total ?? 0;
  const counts = data?.counts || {};
  const count = (...keys) => (data ? keys.reduce((n, k) => n + (counts[k] || 0), 0) : null);
  const tabCount = {
    all: count(...Object.keys(counts).filter((k) => k !== "archived")),
    [UPCOMING_FILTER]: count("published", "scheduled", "ready_to_arm", "armed"),
    [ON_AIR_FILTER]: count("live", "degraded"),
    ended: count("ended"),
    draft: count("draft"),
  };
  const attention = data?.attention;
  const attentionTotal = attention ? attention.blocked + attention.conditional : null;
  const today = new Date().toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" });
  const filtering = Boolean(query || statusFilter !== "all" || range);

  const del = async (ev) => {
    if (!window.confirm(`Delete "${ev.title || "this event"}"? This cannot be undone.`)) return;
    try {
      await api.delete(`/events/${ev.id}`);
      notify.success("Event deleted");
      reloadAll();
    } catch (e) {
      notify.error(errMsg(e));
    }
  };

  const columns = [
    {
      key: "title",
      header: "Event",
      // Room for a title on one or two lines beside its thumbnail, not one word per line.
      className: "min-w-[12rem]",
      sortable: true,
      sortValue: (r) => (r.title || "").toLowerCase(),
      render: (r) => (
        <div className="flex items-center gap-3">
          <Thumb event={r} />
          <div className="min-w-0">
            <button
              onClick={() => navigate(`/organization/events/${r.id}`)}
              className={cx("block text-left text-[13px] font-semibold text-slate-800 hover:text-violet-600 dark:text-slate-100 dark:hover:text-violet-400", focusRing)}
            >
              {r.title || "Untitled event"}
            </button>
            <span className={cx("block text-[11.5px]", CONSOLE.faint)}>
              {visLabel(r.visibility)} event{r.category ? ` · ${r.category}` : ""}
              {r.registered_count ? ` · ${r.registered_count} registered` : ""}
            </span>
          </div>
        </div>
      ),
    },
    {
      key: "start_time",
      header: "Date & time",
      // A date is read as one unit: never "06 / Nov / 2026" down a squeezed column.
      className: "whitespace-nowrap",
      headerClassName: "whitespace-nowrap",
      sortable: true,
      sortValue: (r) => (r.start_time ? new Date(r.start_time).getTime() : 0),
      render: (r) => {
        const d = fmtDay(r.start_time, r.timezone);
        return (
          <div>
            <p className={cx("text-[13px] font-medium", CONSOLE.heading)}>{d.date}</p>
            <p className={cx("text-[11.5px]", CONSOLE.faint)}>{d.time}</p>
          </div>
        );
      },
    },
    {
      key: "status",
      header: "Status",
      sortable: true,
      sortValue: (r) => r.status,
      render: (r) => {
        // One status, decided by the API: a Scheduled event past its start (plus grace) that
        // never went live arrives as "missed" and gets that badge alone.
        const shown = displayStatusOf(r);
        const m = statusMeta(shown);
        return (
          <div>
            <Badge tone={m.tone} dot={m.pulse}>{m.label}</Badge>
            {r.start_overdue && shown !== "missed" && (
              <span className="mt-1 block text-[11px] font-medium text-amber-600 dark:text-amber-400">Start time passed</span>
            )}
          </div>
        );
      },
    },
    { key: "readiness", header: "Readiness", render: (r) => <Readiness event={r} /> },
  ];

  return (
    <div className="space-y-6">
      <OrganizationPageHeader
        breadcrumbs={[["Organization", "/organization/manage"], ["Events", "/organization/events"], [view === "calendar" ? "Calendar" : "All Events"]]}
        eyebrow="Event portfolio"
        title="Events"
        subtitle="Plan, oversee and manage your events from a single portfolio."
        actions={
          <>
            <span className={cx("hidden items-center gap-1.5 text-[12.5px] md:inline-flex", CONSOLE.faint)}>
              <FiClock aria-hidden="true" /> {today} · UTC
            </span>
            <Button variant="secondary" size="sm" leftIcon={view === "calendar" ? FiList : FiCalendar}
              onClick={() => setUrl({ view: view === "calendar" ? null : "calendar", month: null })}>
              {view === "calendar" ? "List view" : "Calendar view"}
            </Button>
            <Button size="sm" leftIcon={FiPlus} onClick={() => setCreateOpen(true)}>
              Create event
            </Button>
          </>
        }
      />

      <KpiRow>
        <KpiTile icon={FiCalendar} tone="violet" label="All events" value={tabCount.all} caption="Including drafts and completed" />
        <KpiTile icon={FiRadio} tone="pink" label="Live now" value={tabCount[ON_AIR_FILTER]} caption="Requires active monitoring" />
        <KpiTile icon={FiClock} tone="sky" label="Upcoming" value={side.upcoming ? side.upcoming.total : side.upcoming === undefined ? "…" : null}
          caption={side.upcoming === null ? "Couldn’t load events" : "Scheduled over the next 30 days"} />
        <KpiTile icon={FiAlertTriangle} tone="amber" label="Needs attention" value={attentionTotal}
          caption={attention ? `${attention.blocked} blocked · ${attention.conditional} with checks open` : "Readiness review"} />
      </KpiRow>

      {attention && attention.events.length > 0 && (
        <Banner tone="warning" title={`${attentionTotal} event${attentionTotal === 1 ? "" : "s"} need${attentionTotal === 1 ? "s" : ""} attention`}>
          Readiness checks must be reviewed before the applicable event goes live:{" "}
          {attention.events.slice(0, 3).map((e, i) => (
            <span key={e.id}>
              {i > 0 && ", "}
              <Link to={`/organization/events/${e.id}/preparation#readiness`} className={cx("font-semibold underline-offset-2 hover:underline", CONSOLE.link)}>
                {e.title}
              </Link>
            </span>
          ))}
          {attention.events.length > 3 ? " and more" : ""}.
        </Banner>
      )}

      {view === "calendar" ? (
        <EventsCalendar
          month={searchParams.get("month")}
          onMonth={(m) => setUrl({ month: m || null })}
          onOpen={(id) => navigate(`/organization/events/${id}`)}
        />
      ) : error ? (
        <OrganizationErrorState error={error} onRetry={reload} title="Couldn't load events" />
      ) : (
        <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_19rem] 2xl:grid-cols-[minmax(0,1fr)_24rem]">
          <Panel
            title={<span className="inline-flex items-center gap-2">All events <Badge tone="brand" size="sm">{total}</Badge></span>}
            subtitle="Showing events you are authorized to access"
            action={
              <div role="group" aria-label="View" className={cx("inline-flex rounded-lg border p-0.5", CONSOLE.divider)}>
                {[["list", "List", FiList], ["calendar", "Calendar", FiCalendar]].map(([key, label, Icon]) => (
                  <button
                    key={key}
                    type="button"
                    aria-pressed={view === key}
                    onClick={() => setUrl({ view: key === "list" ? null : key, month: null })}
                    className={cx(
                      "inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 text-[12.5px] font-medium",
                      view === key ? "bg-violet-50 text-violet-700 dark:bg-violet-500/15 dark:text-violet-200" : CONSOLE.muted,
                      focusRing
                    )}
                  >
                    <Icon aria-hidden="true" /> {label}
                  </button>
                ))}
              </div>
            }
            flush
          >
            <div className="space-y-3 px-5 pb-3">
              <div role="tablist" aria-label="Status" className="flex flex-wrap gap-1.5">
                {TABS.map(([value, label]) => {
                  const on = statusFilter === value;
                  return (
                    <button
                      key={value}
                      type="button"
                      role="tab"
                      aria-selected={on}
                      onClick={() => { setStatusFilter(value); setPage(1); }}
                      className={cx(
                        "inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-[13px] font-medium",
                        focusRing,
                        on ? "bg-violet-50 text-violet-700 dark:bg-violet-500/15 dark:text-violet-200" : cx(CONSOLE.muted, "hover:bg-slate-50 dark:hover:bg-white/[0.05]")
                      )}
                    >
                      {value === ON_AIR_FILTER && <FiRadio aria-hidden="true" />}
                      {label}
                      <span className={cx("rounded-md px-1.5 text-[11px] tabular-nums", on ? "bg-white/70 dark:bg-white/10" : "bg-slate-100 text-slate-600 dark:bg-white/[0.08] dark:text-neutral-200")}>
                        {tabCount[value] ?? "—"}
                      </span>
                    </button>
                  );
                })}
              </div>
              <div className="flex flex-wrap gap-2">
                <label className="relative min-w-[14rem] flex-1">
                  <span className="sr-only">Search events by name or description</span>
                  <FiSearch aria-hidden="true" className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                  <input type="search" value={query} onChange={(e) => setQuery(e.target.value)}
                    placeholder="Search events by name…" className={CONSOLE.search} />
                </label>
                <div className="relative">
                  <select value={statusFilter} onChange={(e) => { setStatusFilter(e.target.value); setPage(1); }}
                    aria-label="Filter by status" className={cx(CONSOLE.select, "appearance-none pr-8")}>
                    {/* Archived events leave the working list; the Archived option brings them back. */}
                    <option value="all">All except archived</option>
                    <option value={ON_AIR_FILTER}>Live now (live or degraded)</option>
                    <option value={UPCOMING_FILTER}>Upcoming (before go-live)</option>
                    {STATUS_FILTER_OPTIONS.map(([k, v]) => (
                      <option key={k} value={k}>{v.label}</option>
                    ))}
                  </select>
                  <FiChevronDown aria-hidden="true" className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
                </div>
                <div className="relative">
                  <select value={range} onChange={(e) => { setUrl({ range: e.target.value }); setPage(1); }}
                    aria-label="Date range" className={cx(CONSOLE.select, "appearance-none pr-8")}>
                    {Object.entries(RANGES).map(([k, r]) => <option key={k || "any"} value={k}>{r.label}</option>)}
                  </select>
                  <FiChevronDown aria-hidden="true" className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
                </div>
                <div className="relative">
                  <select value={`${sort.key}:${sort.dir}`} onChange={(e) => { const [key, dir] = e.target.value.split(":"); setSort({ key, dir }); setPage(1); }}
                    aria-label="Sort" className={cx(CONSOLE.select, "appearance-none pr-8")}>
                    {Object.entries(SORTS).map(([k, label]) => <option key={k} value={k}>{label}</option>)}
                    {!SORTS[`${sort.key}:${sort.dir}`] && <option value={`${sort.key}:${sort.dir}`}>Sort: by column</option>}
                  </select>
                  <FiChevronDown aria-hidden="true" className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
                </div>
              </div>
            </div>
            <div className="border-t border-slate-100 dark:border-white/[0.08]">
              <DataTable
                columns={columns}
                rows={rows}
                rowKey={(r) => r.id}
                loading={loading}
                pageSize={PAGE_SIZE}
                serverSort={sort}
                onSortChange={(next) => {
                  setPage(1);
                  setSort(next);
                }}
                serverPage={page}
                serverPageCount={Math.max(1, Math.ceil(total / PAGE_SIZE))}
                serverTotal={total}
                onPageChange={setPage}
                minWidth={760}
                // Open, copy and delete: labelled and visible at rest — they are the page's
                // primary controls, not hover reveals.
                rowActionsHeader="Actions"
                alwaysShowRowActions
                itemLabel="events"
                empty={{
                  icon: FiCalendar,
                  title: filtering ? "No events match your filters" : "No events yet",
                  description: filtering ? "Try clearing the search, status or date filter." : "Create your first event to start streaming.",
                  action: filtering ? null : (
                    <Button size="sm" leftIcon={FiPlus} onClick={() => setCreateOpen(true)}>Create event</Button>
                  ),
                }}
                rowActions={(r) => (
                  <>
                    <Link
                      to={`/organization/events/${r.id}${r.status === "ended" ? "/replay" : ""}`}
                      aria-label={`Open workspace for ${r.title || "event"}`}
                      className={cx("mr-1 inline-flex items-center gap-1 whitespace-nowrap rounded px-1 text-[12.5px] font-semibold", CONSOLE.link, focusRing)}
                    >
                      {r.status === "ended" ? "View Replay" : "Open Workspace"} <FiChevronRight aria-hidden="true" />
                    </Link>
                    {/* The row's other actions in its contextual overflow (ZST-UX-NAV-001 §16.1:
                        destructive actions in overflow, with confirmation). The trigger is
                        visible at rest; Delete still asks before it acts. */}
                    <RowActionsMenu
                      label={`More actions for ${r.title || "this event"}`}
                      items={[
                        {
                          key: "copy",
                          label: "Copy viewer link",
                          icon: FiLink,
                          // Copy ONLY — the console never opens the attendee page.
                          onSelect: async () => {
                            if (await copyViewerLink(r)) notify.success("Viewer link copied");
                            else notify.error("Unable to copy viewer link.");
                          },
                        },
                        { key: "delete", label: "Delete event", icon: FiTrash2, danger: true, onSelect: () => del(r) },
                      ]}
                    />
                  </>
                )}
              />
            </div>
          </Panel>

          <div className="space-y-6">
            <MiniCalendar month={side.month} onOpen={(list) => (list.length === 1 ? navigate(`/organization/events/${list[0].id}`) : setUrl({ view: "calendar", month: monthKey(new Date()) }))} />
            <NextOnSchedule upcoming={side.upcoming} onOpen={(id) => navigate(`/organization/events/${id}`)} />
          </div>
        </div>
      )}

      <Footnote>
        <FiShield aria-hidden="true" className="mr-1 inline" />
        Access and available actions are enforced by organization, event role and entitlement — on the server, for every request.
      </Footnote>

      <CreateEventModal open={showCreate} onClose={closeCreate} onCreated={reloadAll} />
    </div>
  );
}
