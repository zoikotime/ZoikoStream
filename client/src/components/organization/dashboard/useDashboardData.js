import { useState } from "react";
import api from "../../../api";
import useApi from "../../../hooks/useApi";
import { DEFAULT_RANGE } from "./dashboardConfig";

// How many upcoming events the panel lists. The COUNT on the KPI card does not come from
// this — /events returns `total` for the whole filtered query, independent of page_size —
// so a small page here still yields an exact "12 upcoming".
export const UPCOMING_LIMIT = 4;
// "Upcoming" on the Overview means the next 30 days (ZST-UX-NAV-001 §8, ORG-01): what an owner
// has to prepare for, not everything ever scheduled.
export const UPCOMING_DAYS = 30;

// Everything the dashboard needs, from endpoints this frontend already calls elsewhere:
//
//   GET /organization/overview   — the page's existing call (live/paused sessions, attention)
//   GET /events                  — the Organization Events page's call, reused for its
//                                  server-side `total` of the next 30 days
//   GET /organization/analytics  — the Organization Analytics page's call
//
// No endpoint, schema or model was added for this screen. Where the platform has no producer
// for a figure the card renders "—" with the reason, rather than the figure being invented.
//
// Analytics is a SEPARATE request on its own period, deliberately: the period selector must
// not re-poll sessions and event counts, and the 30s refresh must not re-pull a 90-day
// aggregate every half minute.
export default function useDashboardData() {
  const core = useApi(async () => {
    // Evaluated per fetch, so "upcoming" stays relative to now across a long-lived tab.
    const now = new Date();
    const nowIso = now.toISOString();
    const horizonIso = new Date(now.getTime() + UPCOMING_DAYS * 86400 * 1000).toISOString();

    // allSettled, not all: the overview is the page, but the /events reads are supporting
    // figures. One failing should grey out its own panel, not replace the whole dashboard.
    // The upcoming query is issued first: it is the count the KPI card states.
    const [overview, upcoming, onAir, recent] = await Promise.allSettled([
      api.get("/organization/overview", { params: { range: "24h" } }).then((r) => r.data),
      api
        .get("/events", {
          params: {
            date_from: nowIso,
            date_to: horizonIso,
            sort_by: "start_time",
            order: "asc",
            page_size: UPCOMING_LIMIT,
          },
        })
        .then((r) => r.data),
      // The event pipeline (ORG-01): what is on air now, and what just ended.
      api.get("/events", { params: { display_status: "on_air", page_size: 3 } }).then((r) => r.data),
      api.get("/events", { params: { status: "ended", sort_by: "start_time", order: "desc", page_size: 2 } }).then((r) => r.data),
    ]);

    // The overview is the one hard dependency — let its failure reach useApi so the page
    // renders its existing diagnosis (404 = stale server build, 401/403 = session, …).
    if (overview.status === "rejected") throw overview.reason;

    return {
      overview: overview.value,
      upcoming: upcoming.status === "fulfilled" ? upcoming.value : null,
      onAir: onAir.status === "fulfilled" ? onAir.value : null,
      recent: recent.status === "fulfilled" ? recent.value : null,
    };
  });

  const [range, setRange] = useState(DEFAULT_RANGE);
  const analytics = useApi(() =>
    api.get("/organization/analytics", { params: { range } }).then((r) => r.data)
  );

  // useApi fetches on mount and on reload() only — it has no deps array. Same guarded-render
  // refetch the Analytics and Overview pages already use for exactly this.
  const [lastRange, setLastRange] = useState(range);
  if (range !== lastRange) {
    setLastRange(range);
    analytics.reload();
  }

  return { core, analytics, range, setRange };
}
