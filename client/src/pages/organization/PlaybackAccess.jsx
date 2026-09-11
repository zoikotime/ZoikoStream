import { useMemo, useState } from "react";
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

export default function PlaybackAccess() {
  const { data, loading, error, reload } = useApi(() =>
    // page_size (not `limit`) is this endpoint's parameter name, and it caps at 100.
    api
      .get("/events", { params: { page: 1, page_size: 100, sort_by: "start_time", order: "desc" } })
      .then((r) => r.data)
  );
  const [selectedId, setSelectedId] = useState(null);

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
        title="Playback & Access"
        subtitle="The gates a viewer passes before media plays, per event, and the links that carry them in."
        actions={
          <ConsoleButton variant="secondary" leftIcon={FiRefreshCw} onClick={reload} disabled={loading}>
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
          <StatRow
            label="Playback failures (24h)"
            value={null}
            reason="Client-side playback QoE is not ingested"
          />
          <StatRow
            label="Median start time"
            value={null}
            reason="Client-side playback QoE is not ingested"
          />
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

              <Panel title="Viewer-side measurement">
                <FactGrid
                  columns={3}
                  facts={[
                    { label: "Playback starts", value: null, reason: "Client-side playback QoE is not ingested" },
                    { label: "Rebuffer ratio", value: null, reason: "Client-side playback QoE is not ingested" },
                    { label: "Failures by reason", value: null, reason: "Client-side playback QoE is not ingested" },
                  ]}
                />
                <p className={cx("mt-4 border-t pt-3 text-[12px] leading-snug", CONSOLE.divider, CONSOLE.faint)}>
                  Audience counts and engagement that <em>are</em> measured live on{" "}
                  <Link to="/organization/analytics" className={cx("font-semibold", CONSOLE.link)}>
                    Analytics
                  </Link>
                  . What is missing here is the player&apos;s own view of quality.
                </p>
              </Panel>

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
