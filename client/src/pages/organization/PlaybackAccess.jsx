import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  FiPlayCircle, FiLock, FiUnlock, FiUsers, FiEye, FiRefreshCw, FiCheck, FiMinus,
} from "react-icons/fi";
import api from "../../api";
import useApi from "../../hooks/useApi";
import { CONSOLE, cx, focusRing } from "../../ui/tokens";
import { ConsoleButton } from "../../ui/Button";
import Badge from "../../ui/Badge";
import Skeleton from "../../ui/Skeleton";
import Panel from "../../components/admin/Panel";
import StatRow from "../../components/admin/StatRow";
import FactGrid from "../../components/organization/FactGrid";
import OrganizationPageHeader from "../../components/organization/OrganizationPageHeader";
import OrganizationEmptyState from "../../components/organization/OrganizationEmptyState";
import OrganizationErrorState from "../../components/organization/OrganizationErrorState";
import EventAccessLinks from "../../components/organization/EventAccessLinks";
import { fmtDateTime, visLabel, VISIBILITY_HELP } from "../../data/events";
import { FAILURE_LABELS } from "../../data/playbackQoe";

// Playback & Access — who is allowed to watch, and on what terms.
//
// Every gate shown here is a field on the EVENT (services/viewer enforces them server-side):
// visibility, registration_required, password_protected, waiting_room_enabled, replay_enabled,
// and the scheduled watch window (start_time/end_time). This page reads them, it does not
// decide them — editing stays on the event, which is where the form and its validation live.
//
// Deliberately NOT called here: GET /events/{id}/playback. That endpoint mints a viewer
// LiveKit credential and RECORDS A JOIN (crud_attendee.record_join), so an admin browsing this
// page would appear in the event's watch history. Access posture is read off the event row.
//
// The access-link table is the existing EventAccessLinks component, unchanged — the create,
// rotate, revoke and one-time-reveal flows already live there.
//
// Viewer-side measurement is what viewers' OWN players reported (hooks/usePlaybackQoe.js ->
// POST /api/playback/qoe), aggregated by the server over every session in the window
// (GET /organization/playback/qoe — server/app/services/playback_qoe.py has the definitions).
// Live and replay are measured from different signals, so they are shown one at a time and
// never blended.

// Gate rows: one per enforced rule, so "off" is as legible as "on".
function GateRow({ on, label, detail, invert = false }) {
  const active = invert ? !on : on;
  return (
    <li className="flex items-start gap-2.5 py-2">
      <span
        className={cx(
          "mt-0.5 grid h-4 w-4 shrink-0 place-items-center rounded-full",
          active
            ? "bg-green-100 text-green-600 dark:bg-green-500/15 dark:text-green-400"
            : "bg-slate-100 text-slate-400 dark:bg-white/[0.07] dark:text-neutral-500"
        )}
        aria-hidden="true"
      >
        {active ? <FiCheck className="text-[10px]" /> : <FiMinus className="text-[10px]" />}
      </span>
      <span className="min-w-0">
        <span className={cx("block text-[13px] font-medium", active ? CONSOLE.heading : CONSOLE.muted)}>
          {label}
        </span>
        <span className={cx("block text-[12px] leading-snug", CONSOLE.faint)}>{detail}</span>
      </span>
    </li>
  );
}

const watchWindow = (ev) => {
  if (!ev.start_time) return "No scheduled window — playback follows the event status only";
  if (!ev.end_time) return `Opens ${fmtDateTime(ev.start_time)}; no close time set`;
  return `${fmtDateTime(ev.start_time)} → ${fmtDateTime(ev.end_time)}`;
};

// ── Viewer-side measurement ──────────────────────────────────────────────────────────────

const QOE_URL = "/organization/playback/qoe";
const QOE_MODES = [["live", "Live"], ["replay", "Replay"]];
const QOE_WINDOWS = [["all", "All time"], ["30d", "30 days"], ["7d", "7 days"], ["24h", "24 hours"]];

const fmtCount = (n) => Number(n || 0).toLocaleString();
const plural = (n, word) => `${fmtCount(n)} ${word}${n === 1 ? "" : "s"}`;
// The aggregate ratio (server: SUM(stalled) / SUM(watched)) as a percentage. A ratio that is
// above zero but rounds to it still says so, rather than reading as a clean 0%.
const fmtRatio = (ratio) => {
  if (ratio === 0) return "0%";
  const pct = ratio * 100;
  return pct < 0.1 ? "<0.1%" : `${pct.toFixed(1)}%`;
};
const fmtDuration = (ms) => {
  const s = Math.round((ms || 0) / 1000);
  if (s < 60) return `${s} s`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m} min`;
  return `${Math.floor(m / 60)} h ${m % 60} min`;
};
// Rounded on integers: (1.45).toFixed(1) is "1.4", because 1.45 is not exact in binary.
const fmtStartup = (ms) => (ms < 1000 ? `${ms} ms` : `${(Math.round(ms / 100) / 10).toFixed(1)} s`);

function Segmented({ label, options, value, onChange }) {
  return (
    <div className={cx("inline-flex rounded-lg p-0.5", CONSOLE.segment)} role="group" aria-label={label}>
      {options.map(([key, text]) => (
        <button
          key={key}
          type="button"
          onClick={() => onChange(key)}
          aria-pressed={value === key}
          className={cx(
            "rounded-md px-2.5 py-1 text-[12px] font-medium transition-colors duration-150 motion-reduce:transition-none",
            focusRing,
            value === key ? CONSOLE.segmentOn : CONSOLE.segmentOff
          )}
        >
          {text}
        </button>
      ))}
    </div>
  );
}

// "Failures by reason" is a breakdown, not one figure. Three states, kept apart: nothing has
// been measured (a dash and why), measured with no failures (said in words, never a dash),
// and the failures themselves, most frequent first.
function FailureBreakdown({ loading, measured, failures }) {
  if (loading || !measured) {
    return (
      <>
        <p className={cx("text-[20px] font-semibold", CONSOLE.faint)}>—</p>
        <p className={cx("mt-0.5 text-[11px] leading-snug", CONSOLE.faint)}>
          {loading ? "Loading" : "No playback data yet"}
        </p>
      </>
    );
  }
  if (failures.length === 0) {
    return <p className={cx("text-[13px] font-medium", CONSOLE.heading)}>No playback failures</p>;
  }
  return (
    <ul className={cx("divide-y", CONSOLE.divideY)} aria-label="Failures by reason">
      {failures.map(({ reason, count }) => (
        <li key={reason} className="flex items-baseline justify-between gap-3 py-1 text-[13px]">
          <span className={cx("min-w-0 truncate", CONSOLE.body)}>{FAILURE_LABELS[reason] || reason}</span>
          <span className={cx("shrink-0 font-semibold tabular-nums", CONSOLE.heading)}>{fmtCount(count)}</span>
        </li>
      ))}
    </ul>
  );
}

function PlaybackQuality({ eventId, refreshKey }) {
  const [mode, setMode] = useState("live");
  const [windowKey, setWindowKey] = useState("all");
  const [retry, setRetry] = useState(0);
  // One request per (event, window, refresh). Loading is derived from whether the answer on
  // hand is for the request being shown, so switching events never shows the last event's
  // figures under the new title.
  const requestKey = `${eventId}|${windowKey}|${refreshKey}|${retry}`;
  const [result, setResult] = useState({ key: null, data: null, error: null });
  useEffect(() => {
    let alive = true;
    api
      .get(QOE_URL, { params: { event_id: eventId, window: windowKey } })
      .then((r) => {
        if (alive) setResult({ key: requestKey, data: r.data, error: null });
      })
      .catch((e) => {
        if (alive) setResult({ key: requestKey, data: null, error: e });
      });
    return () => {
      alive = false;
    };
  }, [requestKey, eventId, windowKey]);

  const loading = result.key !== requestKey;
  const figures = loading ? null : result.data?.[mode];
  const measured = Boolean(figures?.sessions);
  const emptyReason = loading ? "Loading" : "No playback data yet";
  const ratio = measured ? figures.rebuffer_ratio : null;

  return (
    <Panel
      title="Viewer-side measurement"
      description="Reported by viewers' own players. Live and replay are measured separately."
      action={
        <div className="flex flex-wrap items-center gap-2">
          <Segmented label="Playback mode" options={QOE_MODES} value={mode} onChange={setMode} />
          <Segmented label="Time window" options={QOE_WINDOWS} value={windowKey} onChange={setWindowKey} />
        </div>
      }
    >
      {!loading && result.error ? (
        <OrganizationErrorState
          error={result.error}
          onRetry={() => setRetry((n) => n + 1)}
          title="Couldn't load playback quality"
        />
      ) : (
        <FactGrid
          columns={3}
          facts={[
            {
              label: "Playback starts",
              value: measured ? fmtCount(figures.playback_starts) : null,
              reason: emptyReason,
              detail: measured ? `of ${plural(figures.sessions, "playback session")} · not unique viewers` : null,
            },
            {
              label: "Rebuffer ratio",
              value: ratio == null ? null : fmtRatio(ratio),
              reason: measured ? "No watch time yet" : emptyReason,
              detail:
                ratio == null
                  ? null
                  : `${plural(figures.rebuffer_count, "stall")} · ${fmtDuration(figures.rebuffer_duration_ms)} of ${fmtDuration(figures.playback_duration_ms)} watched`,
            },
            {
              label: "Failures by reason",
              content: (
                <FailureBreakdown loading={loading} measured={measured} failures={figures?.failures || []} />
              ),
            },
          ]}
        />
      )}
      <p className={cx("mt-4 border-t pt-3 text-[12px] leading-snug", CONSOLE.divider, CONSOLE.faint)}>
        A start is a playback session whose player rendered media. Rebuffering is stall time after
        playback began; pauses, hidden tabs, seeks and broadcaster interruptions are not counted.
        Audience counts and engagement are on{" "}
        <Link to="/organization/analytics" className={cx("font-semibold", CONSOLE.link)}>
          Analytics
        </Link>
        .
      </p>
    </Panel>
  );
}

export default function PlaybackAccess() {
  const { data, loading, error, reload } = useApi(() =>
    // page_size (not `limit`) is this endpoint's parameter name, and it caps at 100.
    api
      .get("/events", { params: { page: 1, page_size: 100, sort_by: "start_time", order: "desc" } })
      .then((r) => r.data)
  );
  const [selectedId, setSelectedId] = useState(null);
  // The organization's last 24 hours of viewer-side measurement, every event, both modes.
  const {
    data: recentQoe, loading: recentLoading, error: recentError, reload: reloadRecent,
  } = useApi(() => api.get(QOE_URL, { params: { window: "24h" } }).then((r) => r.data));
  const [qoeRefresh, setQoeRefresh] = useState(0);
  const refresh = () => {
    reload();
    reloadRecent();
    setQoeRefresh((n) => n + 1);
  };
  const recentFact = (measuredValue, emptyReason) => {
    if (recentLoading) return { value: null, reason: "Loading" };
    if (recentError) return { value: null, reason: "Couldn't load playback quality" };
    return measuredValue == null ? { value: null, reason: emptyReason } : { value: measuredValue };
  };
  const recentFailures = recentFact(
    recentQoe?.total?.sessions ? fmtCount(recentQoe.total.failed_sessions) : null,
    "No playback data yet"
  );
  const recentStartup = recentFact(
    recentQoe?.live?.startup_ms_median != null ? fmtStartup(recentQoe.live.startup_ms_median) : null,
    "No live playback started yet"
  );

  const events = useMemo(() => {
    const rows = Array.isArray(data) ? data : data?.items || [];
    // Most recently scheduled first — the ones whose access rules are about to matter.
    return [...rows].sort(
      (a, b) => new Date(b.start_time || 0).getTime() - new Date(a.start_time || 0).getTime()
    );
  }, [data]);

  const selected = events.find((e) => String(e.id) === String(selectedId)) || events[0] || null;

  const posture = useMemo(() => {
    const open = events.filter((e) => e.visibility === "public").length;
    return {
      total: events.length,
      open,
      gated: events.length - open,
      passphrase: events.filter((e) => e.password_protected).length,
      registration: events.filter((e) => e.registration_required).length,
      replay: events.filter((e) => e.replay_enabled).length,
    };
  }, [events]);

  return (
    <div className="space-y-6">
      <OrganizationPageHeader
        breadcrumbs={[["Organization", "/organization/manage"], ["Audience & Access", "/organization/audience"], ["Event access"]]}
        eyebrow="Audience governance / Playback & access"
        title="Playback & Access"
        subtitle="The gates a viewer passes before media plays, per event, and the links that carry them in."
        actions={
          <ConsoleButton variant="secondary" leftIcon={FiRefreshCw} onClick={refresh} disabled={loading}>
            Refresh
          </ConsoleButton>
        }
      />

      {error && <OrganizationErrorState error={error} onRetry={reload} title="Couldn't load events" />}

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Panel className="px-5 py-4">
          <StatRow label="Events" value={loading ? null : posture.total} reason="Loading" />
          <StatRow label="Publicly viewable" value={loading ? null : posture.open} reason="Loading" />
        </Panel>
        <Panel className="px-5 py-4">
          <StatRow label="Gated by visibility" value={loading ? null : posture.gated} reason="Loading" />
          <StatRow label="Passphrase set" value={loading ? null : posture.passphrase} reason="Loading" />
        </Panel>
        <Panel className="px-5 py-4">
          <StatRow label="Registration required" value={loading ? null : posture.registration} reason="Loading" />
          <StatRow label="Replay enabled" value={loading ? null : posture.replay} reason="Loading" />
        </Panel>
        <Panel className="px-5 py-4">
          {/* Sessions that hit a failure, live and replay together (a count adds up across
              modes; a median does not, so start time is live only and says so). */}
          <StatRow label="Playback failures (24h)" value={recentFailures.value} reason={recentFailures.reason} />
          <StatRow label="Median live start (24h)" value={recentStartup.value} reason={recentStartup.reason} />
        </Panel>
      </div>

      {loading ? (
        <Panel title="Access by event">
          <div className="space-y-3">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-2/3" />
          </div>
        </Panel>
      ) : events.length === 0 ? (
        <Panel>
          <OrganizationEmptyState
            icon={FiPlayCircle}
            title="No events to govern yet"
            description="Access rules belong to an event. Create one and its visibility, registration and passphrase settings will appear here."
            action={
              <ConsoleButton href="/organization/events" size="sm">
                Go to Live Events
              </ConsoleButton>
            }
          />
        </Panel>
      ) : (
        <>
          {/* Event picker. Still a list rather than a dropdown — the access posture of each
              option is part of the choice, so it has to be readable before selecting — but
              laid out ACROSS the page instead of down a 280px column. The listbox semantics
              are unchanged, so keyboard and screen-reader behaviour is what it was. */}
          <Panel title="Events" description="Choose an event to see the gates a viewer passes.">
            <ul
              className="zk-scroll-thin grid max-h-[280px] grid-cols-1 gap-2 overflow-y-auto sm:grid-cols-2 lg:grid-cols-3"
              role="listbox"
              aria-label="Select an event"
            >
              {events.map((ev) => {
                const on = String(ev.id) === String(selected?.id);
                return (
                  <li key={ev.id}>
                    <button
                      type="button"
                      role="option"
                      aria-selected={on}
                      onClick={() => setSelectedId(ev.id)}
                      className={cx(
                        "w-full rounded-lg border px-3.5 py-2.5 text-left transition-colors duration-150 motion-reduce:transition-none",
                        focusRing,
                        on
                          ? cx(CONSOLE.navOn, "border-transparent")
                          : cx(CONSOLE.navOff, "border-slate-200 dark:border-white/[0.12]")
                      )}
                    >
                      <span className="flex items-center gap-2">
                        <span className="min-w-0 flex-1 truncate text-[13px] font-semibold">
                          {ev.title || "Untitled event"}
                        </span>
                        {ev.password_protected ? (
                          <FiLock className="shrink-0 text-[13px]" aria-label="Passphrase set" />
                        ) : (
                          <FiUnlock className="shrink-0 text-[13px] opacity-40" aria-hidden="true" />
                        )}
                      </span>
                      <span className={cx("mt-0.5 block truncate text-[11px]", on ? "" : CONSOLE.faint)}>
                        {visLabel(ev.visibility)}
                        {ev.start_time ? ` · ${fmtDateTime(ev.start_time)}` : ""}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          </Panel>

          {selected && (
            <div className="min-w-0 space-y-4">
              <Panel
                eyebrow="Access rules"
                title={selected.title || "Untitled event"}
                description={VISIBILITY_HELP[selected.visibility]}
                action={
                  <Link
                    to={`/organization/events/${selected.id}`}
                    className={cx("text-[12px] font-semibold", CONSOLE.link)}
                  >
                    Edit event →
                  </Link>
                }
              >
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone="brand">{visLabel(selected.visibility)}</Badge>
                  {selected.password_protected && (
                    <Badge tone="warning" icon={FiLock}>
                      Passphrase
                    </Badge>
                  )}
                  {selected.registration_required && (
                    <Badge tone="info" icon={FiUsers}>
                      Registration
                    </Badge>
                  )}
                  {selected.replay_enabled && <Badge tone="success">Replay on</Badge>}
                </div>

                <ul className={cx("mt-4 divide-y", CONSOLE.divideY)}>
                  <GateRow
                    on={selected.visibility !== "public"}
                    label="Visibility gate"
                    detail={`${visLabel(selected.visibility)} — ${
                      VISIBILITY_HELP[selected.visibility] || "who can discover and open the event page"
                    }`}
                  />
                  <GateRow
                    on={selected.registration_required}
                    label="Registration gate"
                    detail={
                      selected.registration_required
                        ? `Enforced on the media, not the page — an unregistered viewer still reaches the landing page to sign up.${
                            selected.registration_limit ? ` Capacity ${selected.registration_limit}.` : ""
                          }`
                        : "Anyone who can open the page can start playback."
                    }
                  />
                  <GateRow
                    on={selected.password_protected}
                    label="Passphrase gate"
                    detail={
                      selected.password_protected
                        ? "Checked when media starts, so the page stays reachable and the prompt is explainable."
                        : "No shared passphrase is set for this event."
                    }
                  />
                  <GateRow
                    on={selected.waiting_room_enabled}
                    label="Waiting room"
                    detail={
                      selected.waiting_room_enabled
                        ? "Arrivals are held until the host admits them."
                        : "Viewers join the stage view directly."
                    }
                  />
                  <GateRow
                    on={Boolean(selected.start_time)}
                    label="Scheduled watch window"
                    detail={watchWindow(selected)}
                  />
                  <GateRow
                    on={selected.replay_enabled}
                    label="Replay after the event"
                    detail={
                      selected.replay_enabled
                        ? "The recording stays playable to the same audience once the broadcast ends."
                        : "Playback closes with the broadcast; the recording is not published to viewers."
                    }
                  />
                </ul>
              </Panel>

              {/* The whole access-link lifecycle already exists as a component — create,
                  rotate, revoke, and the one-time raw-token reveal. Reused, not rebuilt. */}
              <EventAccessLinks event={selected} canManage />

              <PlaybackQuality eventId={selected.id} refreshKey={qoeRefresh} />

              <p className={cx("flex items-center gap-2 text-[12px]", CONSOLE.faint)}>
                <FiEye className="shrink-0" aria-hidden="true" />
                Viewing this page does not join the event — access posture is read from the
                event record, never by minting a viewer token.
              </p>
            </div>
          )}
        </>
      )}
    </div>
  );
}
