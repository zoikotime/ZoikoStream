import { useEffect, useRef, useState } from "react";
import { AreaChart } from "../../ui/charts";
import { CONSOLE, cx, focusRing } from "../../ui/tokens";
import { Button, Panel, StatCard } from "../../components/admin";
import api, { diagnoseLoadError } from "../../api";
import useApi from "../../hooks/useApi";

// API traffic — the detail behind the Command Center's API-health tile.
//
// This view exists because that tile's "Traffic →" link had nowhere honest to go: it pointed
// at /admin/developers, which is API KEYS. Nothing in the console showed request volume,
// error rate or latency, even though the platform has been recording all three for every
// request (main.py's middleware -> services/ops.request_stats -> flushed per minute).
//
// GET /admin/api-traffic reuses services/ops.api_window, the exact function behind the tile,
// so this page and the dashboard can never disagree about the same window.
//
// Nothing here is derived in the browser: every figure is the server's, and a figure the
// server could not measure renders as "—" rather than as a zero.
const RANGES = [
  ["live", "Live"],
  ["1h", "1h"],
  ["24h", "24h"],
  ["7d", "7d"],
];

const WINDOW_LABEL = { live: "last 15 minutes", "1h": "last hour", "24h": "last 24 hours", "7d": "last 7 days" };

// Short clock for the x-axis. The server labels every bucket with its start instant.
const bucketLabel = (iso, range) =>
  new Date(iso).toLocaleTimeString(undefined, range === "7d"
    ? { month: "short", day: "numeric" }
    : { hour: "2-digit", minute: "2-digit" });

export default function ApiTraffic() {
  const [range, setRange] = useState("24h");
  const { data, loading, error, reload } = useApi(() =>
    api.get("/admin/api-traffic", { params: { range } }).then((r) => r.data)
  );

  // useApi takes no dependency list — it refetches when reload() is called — so a range
  // change asks for the new window explicitly. Same arrangement Support.jsx uses for its
  // status filter; the ref skips the duplicate fetch on mount.
  const mounted = useRef(false);
  useEffect(() => {
    if (!mounted.current) { mounted.current = true; return; }
    reload();
  }, [range, reload]);

  const period = WINDOW_LABEL[range] || "selected window";
  const state = data?.measurement_state;
  const measured = state === "measured" || state === "partial";

  // "—" for anything the window did not measure. An error RATE over zero requests does not
  // exist, and the server sends null rather than 0 for it; that distinction is kept here.
  const requests = measured && data?.requests != null ? data.requests.toLocaleString() : "—";
  const errorRate = data?.value != null ? `${data.value}` : "—";
  const p95 = data?.p95_ms != null ? `${data.p95_ms}` : "—";

  const points = (data?.series || []).map((p) => ({ label: bucketLabel(p.t, range), value: p.value }));

  return (
    <div className="mx-auto max-w-[1200px] space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-[24px] font-semibold tracking-tight text-slate-900 dark:text-white">API traffic</h1>
          <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
            Request volume, HTTP error rate and p95 latency across the platform API.
          </p>
        </div>

        <div
          role="group"
          aria-label="Time range"
          className={cx("flex items-center gap-0.5 rounded-lg p-0.5", CONSOLE.segment)}
        >
          {RANGES.map(([key, label]) => (
            <button
              key={key}
              type="button"
              onClick={() => setRange(key)}
              aria-pressed={range === key}
              className={cx(
                "rounded-md px-2.5 py-1.5 text-[12px] font-semibold transition",
                focusRing,
                range === key ? CONSOLE.segmentOn : CONSOLE.segmentOff
              )}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      {error ? (
        <div
          role="alert"
          className="flex items-center justify-between gap-3 rounded-xl border border-rose-200 bg-rose-50 px-5 py-4 text-sm text-rose-700 dark:border-rose-500/20 dark:bg-rose-500/10 dark:text-rose-300"
        >
          <span>{diagnoseLoadError(error, "/admin/api-traffic")}</span>
          <Button variant="secondary" size="sm" onClick={reload}>Retry</Button>
        </div>
      ) : (
        <>
          {/* Said plainly rather than drawn as a flat line: a window with no flushed minutes
              is unmeasured, which is a different fact from "no traffic". */}
          {!loading && state === "not_measured" && (
            <p className={cx("rounded-xl border px-4 py-3 text-[13px]", CONSOLE.inset, CONSOLE.body)}>
              No request minutes were recorded in the {period}. Counts are flushed once a
              minute per process, so a window shorter than that — or a period with no running
              process — has nothing to report.
            </p>
          )}
          {!loading && state === "partial" && data?.coverage_pct != null && (
            <p className={cx("rounded-xl border px-4 py-3 text-[13px]", CONSOLE.inset, CONSOLE.body)}>
              Partial window: {data.coverage_pct}% of the expected minutes were recorded, so
              these totals are a floor, not a complete count.
            </p>
          )}

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            <StatCard label={`Requests · ${period}`} value={requests} loading={loading} />
            <StatCard label={`Error rate · ${period}`} value={errorRate} suffix={data?.value != null ? "%" : ""} loading={loading} />
            <StatCard
              label={`p95 latency · ${period}`}
              value={p95}
              suffix={data?.p95_ms != null ? "ms" : ""}
              loading={loading}
            />
          </div>

          <AreaChart
            title="Requests"
            subtitle={`Requests per bucket over the ${period}. Buckets with no recorded minutes are gaps, not zeros.`}
            data={points}
            keys={[{ key: "value", name: "Requests", color: "#f59e0b" }]}
            loading={loading}
            empty={!loading && points.length === 0}
          />

          <Panel title="What these numbers are" static>
            <ul className={cx("list-disc space-y-1.5 pl-5 text-[13px]", CONSOLE.body)}>
              <li>
                Counted in the API process itself, for every request it answers, and flushed to
                platform metrics once a minute — so this is the platform API, not one tenant.
              </li>
              <li>
                p95 is read from latency histogram buckets, so it is an upper bound for its
                bucket rather than an exact percentile
                {data?.p95_overflow ? ", and this window overflowed the top bucket — the real value is higher" : ""}.
              </li>
              <li>
                The Command Center&apos;s API-health tile reports the same figures for the same
                window; both call one function on the server.
              </li>
            </ul>
          </Panel>
        </>
      )}
    </div>
  );
}
