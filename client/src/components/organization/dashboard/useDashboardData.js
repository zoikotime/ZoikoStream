import { useState } from "react";
import api from "../../../api";
import useApi from "../../../hooks/useApi";
import { DEFAULT_RANGE } from "./dashboardConfig";

// How many upcoming events the panel lists. The COUNT on the KPI card does not come from
// this — /events returns `total` for the whole filtered query, independent of page_size —
// so a small page here still yields an exact "12 upcoming".
export const UPCOMING_LIMIT = 4;

// Everything the dashboard needs, from endpoints this frontend already calls elsewhere:
//
//   GET /organization/overview   — the page's existing call (live/paused sessions, attention)
//   GET /events                  — the Organization Events page's call, reused twice for its
//                                  server-side `total`: once for upcoming, once for ended
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
    const nowIso = new Date().toISOString();

    // allSettled, not all: the overview is the page, but the two /events counts are
    // supporting figures. One of them failing should grey out one card, not replace the
    // whole dashboard with an error.
    const [overview, upcoming, completed] = await Promise.allSettled([
      api.get("/organization/overview", { params: { range: "24h" } }).then((r) => r.data),
      api
        .get("/events", {
          params: {
            date_from: nowIso,
            sort_by: "start_time",
            order: "asc",
            page_size: UPCOMING_LIMIT,
          },
        })
        .then((r) => r.data),
      api.get("/events", { params: { status: "ended", page_size: 1 } }).then((r) => r.data),
    ]);

    // The overview is the one hard dependency — let its failure reach useApi so the page
    // renders its existing diagnosis (404 = stale server build, 401/403 = session, …).
    if (overview.status === "rejected") throw overview.reason;

    return {
      overview: overview.value,
      upcoming: upcoming.status === "fulfilled" ? upcoming.value : null,
      completed: completed.status === "fulfilled" ? completed.value : null,
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
