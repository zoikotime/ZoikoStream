import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { FiRefreshCw, FiCheckSquare, FiAlertTriangle } from "react-icons/fi";
import api from "../../api";
import useApi from "../../hooks/useApi";
import useInterval from "../../hooks/useInterval";
import { CONSOLE, SEVERITY, cx, type } from "../../ui/tokens";
import { ConsoleButton } from "../../ui/Button";
import Badge from "../../ui/Badge";
import ConsoleScreen from "../../components/admin/ConsoleScreen";
import Panel from "../../components/admin/Panel";
import StatCard from "../../components/admin/StatCard";
import StatRow from "../../components/admin/StatRow";
import DataTable from "../../components/admin/DataTable";
import UpcomingEvents from "../../components/admin/sections/UpcomingEvents";
import { timeAgo } from "../../components/admin/format";

// Event Readiness — whether the events about to happen are actually set up to happen.
//
// Every gate is evaluated server-side against the event's stored configuration (services/ops
// _GATES: title, schedule, host, moderator, recording, account standing, redundancy), and
// `required_for` decides whether a failed gate BLOCKS the event or is a conditional pass — a
// missing moderator is fatal for an unrepeatable event and merely a warning for a standard one.
//
// This page does not re-derive any of that. Re-implementing the gates in the browser is how a
// console ends up disagreeing with the API about whether an event may go ahead.
const REFRESH_MS = 30_000;

const VERDICT_LABEL = { blocked: "Blocked", conditional: "Conditional", passed: "Ready" };
const IMPACT_TONE = { unrepeatable: "danger", high: "warning", standard: "neutral" };

export default function EventReadiness() {
  const center = useApi(() =>
    api
      .get("/admin/command-center", { params: { range: "live", scope: "core_live", include_test: false } })
      .then((r) => ({ ...r.data, fetched_at: Date.now() }))
  );
  const [state, setState] = useState("live");
  const ops = useApi(() => api.get("/admin/live-events", { params: { state } }).then((r) => r.data));

  const reloadAll = () => {
    center.reload();
    ops.reload();
  };
  useInterval(reloadAll, REFRESH_MS);

  const [ageSeconds, setAgeSeconds] = useState(0);
  useInterval(
    () => setAgeSeconds(Math.floor((Date.now() - center.data.fetched_at) / 1000)),
    1000,
    Boolean(center.data)
  );

  const upcoming = useMemo(() => center.data?.upcoming_events || [], [center.data]);

  const counts = useMemo(() => {
    const by = (v) => upcoming.filter((e) => (e.verdict || e.readiness) === v).length;
    return {
      total: upcoming.length,
      blocked: by("blocked"),
      conditional: by("conditional"),
      passed: by("passed"),
      unrepeatable: upcoming.filter((e) => e.impact === "unrepeatable").length,
    };
  }, [upcoming]);

  // The gate ledger: which gate is failing most often across the upcoming set. Answers the
  // operator's real question — "what do we keep getting wrong?" — from data already loaded.
  const gateFailures = useMemo(() => {
    const tally = new Map();
    upcoming.forEach((ev) => {
      (ev.gates || []).forEach((g) => {
        if (g.passed) return;
        const entry = tally.get(g.key) || { key: g.key, label: g.label || g.key, count: 0, blocking: 0 };
        entry.count += 1;
        if (g.blocking) entry.blocking += 1;
        tally.set(g.key, entry);
      });
    });
    return [...tally.values()].sort((a, b) => b.blocking - a.blocking || b.count - a.count);
  }, [upcoming]);

  const sessions = ops.data?.items || ops.data || [];

  const sessionColumns = [
    {
      key: "event",
      header: "Event",
      sortable: true,
      render: (s) => (
        <div className="min-w-0">
          <p className={cx("truncate text-[13px] font-semibold", CONSOLE.heading)}>
            {s.event || s.title || "Untitled event"}
          </p>
          <p className={cx("truncate text-[11px]", CONSOLE.faint)}>{s.organization || "—"}</p>
        </div>
      ),
    },
    {
      key: "impact",
      header: "Impact",
      sortable: true,
      render: (s) => <Badge tone={IMPACT_TONE[s.impact] || "neutral"}>{s.impact || "standard"}</Badge>,
    },
    {
      key: "owner",
      header: "Host on duty",
      render: (s) =>
        s.owner ? (
          <span className={cx("text-[13px]", CONSOLE.body)}>{s.owner}</span>
        ) : (
          <span className="inline-flex items-center gap-1.5 text-[12px] font-semibold text-rose-600 dark:text-rose-400">
            <FiAlertTriangle aria-hidden="true" />
            Nobody rostered
          </span>
        ),
    },
    {
      key: "started_at",
      header: state === "live" ? "Running for" : "Ended",
      align: "right",
      sortable: true,
      sortValue: (s) => new Date(s.started_at || s.ended_at || 0).getTime(),
      render: (s) => (
        <span className={cx("text-[12px]", type.mono, CONSOLE.muted)}>
          {s.started_at || s.ended_at ? timeAgo(s.started_at || s.ended_at) : "—"}
        </span>
      ),
    },
  ];

  return (
    <ConsoleScreen
      title="Event Readiness"
      subtitle="Gate verdicts for events about to run, and what is currently on air. A blocked event fails a gate its impact class treats as mandatory."
      ageSeconds={ageSeconds}
      hasData={Boolean(center.data)}
      loading={center.loading}
      error={center.error}
      endpoint="/admin/command-center"
      onRetry={reloadAll}
      actions={
        <ConsoleButton variant="secondary" leftIcon={FiRefreshCw} onClick={reloadAll} disabled={center.loading}>
          Refresh
        </ConsoleButton>
      }
    >
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard label="Upcoming assessed" value={counts.total} loading={center.loading} />
        <StatCard label="Blocked" value={counts.blocked} loading={center.loading} color="#f43f5e" />
        <StatCard label="Conditional" value={counts.conditional} loading={center.loading} color="#f59e0b" />
        <StatCard label="Unrepeatable" value={counts.unrepeatable} loading={center.loading} />
      </div>

      {/* The existing readiness table, unchanged — it already renders gates per event. */}
      <UpcomingEvents events={upcoming} />

      <div className="grid gap-4 xl:grid-cols-[1fr_1.35fr]">
        <Panel
          title="Most-failed gates"
          description="Across every upcoming event in this window."
          flush
        >
          {center.loading ? (
            <ul className={cx("divide-y", CONSOLE.divideY)} aria-hidden="true">
              {Array.from({ length: 4 }).map((_, i) => (
                <li key={i} className="px-5 py-3">
                  <div className="zk-skeleton h-4 w-40 rounded bg-slate-200 dark:bg-white/[0.07]" />
                </li>
              ))}
            </ul>
          ) : gateFailures.length === 0 ? (
            <p className={cx("px-5 py-10 text-center text-[13px]", CONSOLE.faint)}>
              {counts.total === 0
                ? "No upcoming events to assess in this window."
                : "Every gate passes on every upcoming event."}
            </p>
          ) : (
            <ul className={cx("divide-y", CONSOLE.divideY)}>
              {gateFailures.map((g) => (
                <li key={g.key} className="flex items-center justify-between gap-3 px-5 py-3">
                  <div className="min-w-0">
                    <p className={cx("truncate text-[13px] font-medium", CONSOLE.heading)}>{g.label}</p>
                    <p className={cx("truncate text-[11px]", CONSOLE.faint)}>
                      {g.blocking > 0
                        ? `${g.blocking} blocking · ${g.count - g.blocking} conditional`
                        : `${g.count} conditional`}
                    </p>
                  </div>
                  <span
                    className={cx(
                      "shrink-0 rounded-full px-2 py-0.5 text-[11px] font-semibold",
                      g.blocking > 0 ? SEVERITY.blocked : SEVERITY.conditional
                    )}
                  >
                    {g.count}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Panel>

        <Panel
          title={state === "live" ? "On air now" : "Recently ended"}
          action={
            <div className={cx("inline-flex rounded-lg p-0.5", CONSOLE.segment)} role="group" aria-label="Session state">
              {[
                ["live", "Live"],
                ["recent", "Recent"],
              ].map(([value, label]) => (
                <button
                  key={value}
                  type="button"
                  onClick={() => {
                    setState(value);
                    ops.reload();
                  }}
                  aria-pressed={state === value}
                  className={cx(
                    "rounded-md px-2.5 py-1 text-[12px] font-medium transition-colors duration-150 motion-reduce:transition-none",
                    state === value ? CONSOLE.segmentOn : CONSOLE.segmentOff
                  )}
                >
                  {label}
                </button>
              ))}
            </div>
          }
          flush
        >
          <DataTable
            columns={sessionColumns}
            rows={Array.isArray(sessions) ? sessions : []}
            rowKey={(s) => s.id || s.event_id}
            loading={ops.loading}
            minWidth={640}
            pageSize={8}
            empty={{
              icon: FiCheckSquare,
              title: state === "live" ? "Nothing on air" : "No recent sessions",
              description:
                state === "live"
                  ? "No broadcast is currently live across the platform."
                  : "No session has ended recently in this window.",
            }}
          />
        </Panel>
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <Panel title="How a verdict is reached">
          <ul className={cx("space-y-2 text-[13px]", CONSOLE.body)}>
            <li>
              <strong className={CONSOLE.heading}>{VERDICT_LABEL.blocked}</strong> — a gate that
              this event&apos;s impact class treats as mandatory is failing. It should not go ahead
              until the gate clears.
            </li>
            <li>
              <strong className={CONSOLE.heading}>{VERDICT_LABEL.conditional}</strong> — a gate is
              failing, but not one that is mandatory at this impact class. Someone accepted the
              risk implicitly; make it explicit.
            </li>
            <li>
              <strong className={CONSOLE.heading}>{VERDICT_LABEL.passed}</strong> — every gate the
              class requires is satisfied.
            </li>
          </ul>
          <p className={cx("mt-4 border-t pt-3 text-[12px] leading-snug", CONSOLE.divider, CONSOLE.faint)}>
            Gates are evaluated by the API against stored configuration. This console displays
            the verdict; it never recomputes it, so the two can never disagree.
          </p>
        </Panel>

        <Panel title="Not assessed">
          <StatRow
            label="Encoder pre-flight result"
            value={null}
            reason="Encoder-side checks are not reported to the platform"
          />
          <StatRow
            label="Rehearsal completed"
            value={null}
            reason="Rehearsals are not modelled as a distinct session type"
          />
          <StatRow
            label="Contributor device checks"
            value={null}
            reason="Device-check outcomes are not persisted per event"
          />
          <p className={cx("mt-3 border-t pt-3 text-[12px]", CONSOLE.divider, CONSOLE.faint)}>
            Roster and staffing gaps that <em>are</em> assessed appear in the table above and on{" "}
            <Link to="/admin/live-events" className={cx("font-semibold", CONSOLE.link)}>
              Live Operations
            </Link>
            .
          </p>
        </Panel>
      </div>
    </ConsoleScreen>
  );
}
