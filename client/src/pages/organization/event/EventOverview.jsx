import { Link, Navigate, useSearchParams } from "react-router-dom";
import { useRef, useState } from "react";
import {
  FiActivity, FiAlertTriangle, FiCheck, FiCheckCircle, FiClock, FiEye, FiInfo, FiPlay, FiRadio, FiZap,
} from "react-icons/fi";
import api from "../../../api";
import useApi from "../../../hooks/useApi";
import useInterval from "../../../hooks/useInterval";
import Badge from "../../../ui/Badge";
import { ConsoleButton as Button } from "../../../ui/Button";
import { CONSOLE, cx, focusRing } from "../../../ui/tokens";
import { ON_AIR_STATUSES, displayStatusOf, statusMeta } from "../../../data/events";
import { DarkCard, Footnote, Panel, PanelLink } from "../../../components/organization/console/ConsoleUI";
import { CARD } from "../../../components/organization/console/consoleStyles";
import Freshness from "../../../components/organization/Freshness";
import EventSectionHeader from "./EventSectionHeader";
import EventLifecycleActions from "./EventLifecycleActions";
import { LEGACY_TABS, sectionPath, useEventWorkspace } from "./workspace";
import { fmtSeconds, fmtZonedShort } from "../../../data/eventTime";
import { OUTCOME_TONE } from "../../../data/activity";

// EVT-01 Event Overview (ZST-UX-NAV-001 §9.1; ZST-ENG-DASH-001 EVT-01 frame): where the event
// is in its lifecycle, how ready it is, what a viewer would meet, its operational status and
// what comes next — each a summary of the section that owns it. A panel whose source fails
// says so on its own; the rest of the page stays (§8.1).

export default function EventOverview() {
  const [params] = useSearchParams();
  const { eventId } = useEventWorkspace();
  const legacy = params.get("tab");
  if (legacy && legacy in LEGACY_TABS) {
    return <Navigate replace to={sectionPath(eventId, LEGACY_TABS[legacy])} />;
  }
  return <Overview />;
}

const LIVE_POLL_MS = 15_000;

// The seven-stage lifecycle (ZST-UX-NAV-001 §7.1) as a strip. Where the event IS comes from
// its status alone — never inferred from a schedule or a guess about progress — and the stages
// before it are shown as passed, the ones after as still to come.
const STAGES = [
  { label: "Contribute", section: "preparation", hash: "team", destination: "contribution setup" },
  { label: "Ingest", section: "preparation", hash: "sources", destination: "sources and ingest" },
  { label: "Produce", section: "live", destination: "Live Control" },
  { label: "Secure", section: "access", hash: "diagnostics", destination: "access diagnostics" },
  { label: "Deliver", section: "insights", hash: "quality", destination: "playback quality and delivery" },
  { label: "Understand", section: "insights", destination: "Insights" },
  { label: "Preserve", section: "replay", hash: "retention", destination: "replay preservation" },
];
const STAGE_OF = {
  draft: 0, published: 2, scheduled: 2, ready_to_arm: 3, armed: 3, live: 4, degraded: 4,
  ended: 5, cancelled: 5, archived: 6,
};

export function StageStrip({ status, eventId }) {
  const current = STAGE_OF[status] ?? 0;
  const [prevCurrent, setPrevCurrent] = useState(current);
  const [selected, setSelected] = useState(current);
  const links = useRef([]);

  if (prevCurrent !== current) {
    setPrevCurrent(current);
    setSelected(current);
  }

  const moveFocus = (event, index) => {
    let next;
    if (event.key === "ArrowRight") next = (index + 1) % STAGES.length;
    else if (event.key === "ArrowLeft") next = (index - 1 + STAGES.length) % STAGES.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = STAGES.length - 1;
    else if (event.key === " ") {
      event.preventDefault();
      links.current[index]?.click();
      return;
    } else return;

    event.preventDefault();
    setSelected(next);
    links.current[next]?.focus();
  };

  const selectedStage = STAGES[selected]?.label;
  return (
    <nav aria-label="Event lifecycle">
      <ol className="grid grid-cols-2 gap-2 sm:grid-cols-4 xl:grid-cols-7">
        {STAGES.map((stage, i) => {
          const state = i < current ? "done" : i === current ? "current" : "next";
          const isSelected = i === selected;
          const to = `${sectionPath(eventId, stage.section)}${stage.hash ? `#${stage.hash}` : ""}`;
          return (
            <li key={stage.label}>
              <Link
              ref={(node) => { links.current[i] = node; }}
              to={to}
              tabIndex={isSelected ? 0 : -1}
              aria-current={isSelected ? "step" : undefined}
              aria-label={`${stage.label}, ${state === "done" ? "completed" : state === "current" ? "current stage" : "upcoming"}; open ${stage.destination}`}
              className={cx(
                CARD,
                "flex min-h-full w-full cursor-pointer flex-col items-center gap-1.5 px-2 py-3 text-center transition-colors",
                isSelected
                  ? "border-violet-300 bg-violet-50/80 ring-1 ring-violet-200 dark:border-violet-400/40 dark:bg-violet-500/10 dark:ring-violet-400/20"
                  : "hover:border-violet-300 hover:bg-violet-50/50 dark:hover:border-violet-400/40 dark:hover:bg-violet-500/5",
                focusRing
              )}
              onClick={() => setSelected(i)}
              onFocus={() => setSelected(i)}
              onKeyDown={(event) => moveFocus(event, i)}
              >
              <span
                aria-hidden="true"
                className={cx(
                    "grid h-7 w-7 place-items-center rounded-full text-[13px]",
                    state === "done" && "bg-emerald-50 text-emerald-600 dark:bg-emerald-500/15 dark:text-emerald-300",
                    state === "current" && "bg-violet-100 text-violet-700 dark:bg-violet-500/25 dark:text-violet-200",
                    state === "next" && "bg-slate-100 text-slate-400 dark:bg-white/[0.08] dark:text-neutral-400"
                  )}
                >
                  {state === "done" ? <FiCheck /> : state === "current" ? <FiZap /> : <FiClock />}
                </span>
                <span className={cx("text-[12.5px] font-semibold", CONSOLE.heading)}>{stage.label}</span>
                <span className="sr-only">{state === "done" ? "Completed" : state === "current" ? "Current lifecycle stage" : "Upcoming"}</span>
              </Link>
            </li>
          );
        })}
      </ol>
      <span className="sr-only" aria-live="polite">
        Selected stage: {selectedStage}. Press Enter or Space to open.
      </span>
    </nav>
  );
}

function Ring({ passed, total }) {
  const pct = total ? passed / total : 0;
  const r = 34;
  const c = 2 * Math.PI * r;
  return (
    <div className="relative h-24 w-24 shrink-0" role="img" aria-label={`${passed} of ${total} readiness checks passed`}>
      <svg viewBox="0 0 80 80" className="h-full w-full -rotate-90">
        <circle cx="40" cy="40" r={r} fill="none" strokeWidth="8" className="stroke-slate-100 dark:stroke-white/10" />
        <circle cx="40" cy="40" r={r} fill="none" strokeWidth="8" strokeLinecap="round"
          stroke="url(#ring-grad)" strokeDasharray={`${c * pct} ${c}`} />
        <defs>
          <linearGradient id="ring-grad" x1="0" x2="1">
            <stop offset="0%" stopColor="#7c3aed" />
            <stop offset="100%" stopColor="#d946ef" />
          </linearGradient>
        </defs>
      </svg>
      <div className="absolute inset-0 grid place-items-center text-center">
        <div>
          <p className={cx("text-[20px] font-bold leading-none", CONSOLE.heading)}>{passed}/{total}</p>
          <p className={cx("text-[10px]", CONSOLE.faint)}>Completed</p>
        </div>
      </div>
    </div>
  );
}

const HEADLINE = {
  passed: "Ready for go-live",
  conditional: "Nearly ready",
  blocked: "Blocked — required checks failing",
};

function Overview() {
  const { event, eventId, reload, readiness, readinessError, readinessLoading } = useEventWorkspace();
  const onAir = ON_AIR_STATUSES.includes(event.status);
  const st = statusMeta(displayStatusOf(event));

  const live = useApi(() => api.get(`/organization/events/${eventId}/live-status`).then((r) => r.data));
  const rehearsals = useApi(() => api.get(`/events/${eventId}/rehearsals`).then((r) => r.data));
  const activity = useApi(() =>
    api.get(`/organization/events/${eventId}/activity`, { params: { page_size: 5 } }).then((r) => r.data)
  );
  // Polled only while on air: a stream's state is the one figure here that moves by itself.
  useInterval(live.reload, onAir ? LIVE_POLL_MS : null);

  const gates = readiness?.gates || [];
  const passed = gates.filter((g) => g.passed).length;
  const blockers = gates.filter((g) => g.required && !g.passed);
  // Failing first, so the rows that need work are the ones on screen.
  const rows = [...gates].sort((a, b) => Number(a.passed) - Number(b.passed)).slice(0, 4);
  const session = live.data?.current_session;
  const inputs = live.data?.inputs || [];

  const milestones = [
    ...(rehearsals.data || []).filter((r) => r.scheduled_at).map((r) => ({
      at: r.scheduled_at, title: "Rehearsal", detail: r.status ? r.status.replace(/_/g, " ") : "Scheduled",
    })),
    ...(event.start_time ? [{ at: event.start_time, title: "Scheduled event window", detail: `${fmtZonedShort(event.start_time, event.timezone)} · start controls gated` }] : []),
    ...(event.end_time ? [{ at: event.end_time, title: "Scheduled end", detail: fmtZonedShort(event.end_time, event.timezone) }] : []),
  ].sort((a, b) => new Date(a.at) - new Date(b.at));

  return (
    <div className="space-y-6">
      <EventSectionHeader
        overview
        title={event.title}
        actions={
          <>
            <Button variant="secondary" size="sm" leftIcon={FiEye} href={`${sectionPath(eventId, "access")}#viewer-preview`}>
              Viewer Preview
            </Button>
            <Button size="sm" href={`${sectionPath(eventId, "preparation")}#readiness`}>Review Readiness</Button>
          </>
        }
      />

      <StageStrip status={event.status} eventId={eventId} />

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
        <div className="space-y-6">
          <Panel
            title="Production readiness"
            action={
              blockers.length ? (
                <PanelLink to={`${sectionPath(eventId, "preparation")}#readiness`}>
                  {blockers.length} blocker{blockers.length === 1 ? "" : "s"} remaining
                </PanelLink>
              ) : (
                <PanelLink to={`${sectionPath(eventId, "preparation")}#readiness`}>Readiness checks</PanelLink>
              )
            }
          >
            {readinessError ? (
              <p className={cx("flex items-center gap-2 text-sm", CONSOLE.muted)}>
                <FiInfo aria-hidden="true" /> Readiness couldn&rsquo;t be checked. Arming stays gated on the server either way.
              </p>
            ) : readinessLoading || !readiness ? (
              <p className={cx("text-sm", CONSOLE.faint)}>Checking…</p>
            ) : (
              <>
                <div className="flex items-center gap-5">
                  <Ring passed={passed} total={gates.length} />
                  <div>
                    <p className={cx("text-[16px] font-semibold", CONSOLE.heading)}>{HEADLINE[readiness.verdict] || "Readiness"}</p>
                    <p className={cx("mt-1 text-[13px] leading-relaxed", CONSOLE.muted)}>
                      Complete the required checks before the event can move to a ready state. Arming is refused on the
                      server while a required check fails; live controls stay in the Producer Console.
                    </p>
                  </div>
                </div>
                <ul className={cx("mt-4 divide-y border-t", CONSOLE.divider, CONSOLE.divideY)}>
                  {rows.map((g) => (
                    <li key={g.key} className="flex items-center justify-between gap-3 py-2.5 text-[13.5px]">
                      <span className="flex items-center gap-3">
                        <span aria-hidden="true" className={cx(
                          "grid h-6 w-6 place-items-center rounded-md text-[12px]",
                          g.passed ? "bg-emerald-50 text-emerald-600 dark:bg-emerald-500/15 dark:text-emerald-300"
                            : "bg-amber-50 text-amber-600 dark:bg-amber-500/15 dark:text-amber-300"
                        )}>
                          {g.passed ? <FiCheck /> : <FiAlertTriangle />}
                        </span>
                        <span className={CONSOLE.body}>{g.label}</span>
                      </span>
                      <span className={cx("text-[12px]", CONSOLE.faint)}>
                        {g.passed ? "Complete" : g.required ? "Action needed" : "Recommended"}
                      </span>
                    </li>
                  ))}
                </ul>
              </>
            )}
          </Panel>

          <Panel title="Operational status" tag="Event scoped" tagTone="violet">
            <div className="grid gap-3 sm:grid-cols-2">
              <div className={cx(CONSOLE.inset, "p-4")}>
                <p className={cx("flex items-center gap-2 text-[12px] font-medium", CONSOLE.muted)}><FiActivity aria-hidden="true" /> Source &amp; ingest</p>
                <p className={cx("mt-1.5 text-[15px] font-semibold", CONSOLE.heading)}>
                  {live.error && !live.data ? "Unavailable" : !live.data ? "…" : inputs.length
                    ? (inputs[0].signal_state === "healthy" ? "Signal healthy" : `Signal ${inputs[0].signal_state}`)
                    : onAir ? "Browser publishing" : "Test pending"}
                </p>
                <p className={cx("text-[12px]", CONSOLE.faint)}>
                  {inputs.length ? `${inputs.length} external input${inputs.length === 1 ? "" : "s"}` : onAir ? "From the Producer Console" : "No live signal asserted"}
                </p>
              </div>
              <div className={cx(CONSOLE.inset, "p-4")}>
                <p className={cx("flex items-center gap-2 text-[12px] font-medium", CONSOLE.muted)}><FiRadio aria-hidden="true" /> Live control</p>
                <p className={cx("mt-1.5 text-[15px] font-semibold", onAir ? "text-rose-600 dark:text-rose-300" : CONSOLE.heading)}>
                  {onAir ? `Live${event.status === "degraded" ? " · degraded" : ""}` : "Not live"}
                </p>
                <p className={cx("text-[12px]", CONSOLE.faint)}>
                  {session ? `On air ${fmtSeconds(session.on_air_seconds)}` : "Safe to review monitoring"}
                </p>
              </div>
            </div>
            {live.data && <div className="mt-3"><Freshness asOf={live.data.as_of} staleAfter={onAir ? 45 : 600} failed={Boolean(live.error)} /></div>}
            <div className="mt-3 rounded-xl border border-violet-200/70 bg-violet-50/60 px-3 py-2 text-[12px] text-violet-800 dark:border-violet-400/25 dark:bg-violet-500/10 dark:text-violet-200">
              <FiInfo aria-hidden="true" className="mr-1.5 inline" />
              Viewer policy controls appear only after approved policy decisions and authorization.
            </div>
          </Panel>
        </div>

        <div className="space-y-6">
          <Panel title="Viewer experience preview" action={<PanelLink to={`${sectionPath(eventId, "access")}#viewer-preview`}>Preview only</PanelLink>}>
            <DarkCard as="div" className="grid min-h-[11rem] place-items-center p-6 text-center">
              <span aria-hidden="true" className="mx-auto grid h-12 w-12 place-items-center rounded-full border border-white/30 bg-white/10">
                <FiPlay />
              </span>
              <p className="mt-3 text-[16px] font-semibold">{event.title || "Untitled event"}</p>
              <p className="text-[12px] text-white/70">
                {onAir ? "The broadcast is live for admitted viewers" : event.status === "ended" ? "The broadcast has ended" : "Playback opens when the event is available"}
              </p>
            </DarkCard>
            <div className="mt-3 flex items-center justify-between gap-2">
              <p className={cx("text-[12px]", CONSOLE.faint)}>Player experience is separate from the staff console</p>
              <Badge tone={st.tone} dot>{st.label}</Badge>
            </div>
          </Panel>

          <Panel title="Upcoming milestones" action={<PanelLink to={`${sectionPath(eventId, "preparation")}#details`}>Run of show</PanelLink>}>
            {rehearsals.error && !milestones.length ? (
              <p className={cx("text-sm", CONSOLE.muted)}>Milestones couldn&rsquo;t be read.</p>
            ) : milestones.length === 0 ? (
              <p className={cx("text-sm", CONSOLE.muted)}>No rehearsal or start time is scheduled yet.</p>
            ) : (
              <ol className="space-y-3">
                {milestones.map((m) => (
                  <li key={`${m.title}-${m.at}`} className="grid grid-cols-[4.5rem_1fr] gap-3">
                    <span className={cx("text-[12px] font-semibold", CONSOLE.muted)}>
                      {new Date(m.at).toLocaleDateString("en-GB", { day: "numeric", month: "short" })}
                    </span>
                    <span className="flex gap-2.5">
                      <span aria-hidden="true" className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-violet-500" />
                      <span>
                        <span className={cx("block text-[13px] font-semibold", CONSOLE.heading)}>{m.title}</span>
                        <span className={cx("block text-[11.5px] capitalize", CONSOLE.faint)}>{m.detail}</span>
                      </span>
                    </span>
                  </li>
                ))}
              </ol>
            )}
          </Panel>
        </div>
      </div>

      <Panel title="Lifecycle" subtitle="Only the moves this event's status allows are offered. The server re-checks each one." tag="Protected actions" tagTone="slate">
        <EventLifecycleActions event={event} onChanged={reload} onBeforeConfirm={reload} />
      </Panel>

      <Panel
        title="Recent activity"
        subtitle="The latest recorded actions and refusals for this event."
        action={<PanelLink to={sectionPath(eventId, "activity")}>Activity &amp; Audit</PanelLink>}
        flush
      >
        {activity.error ? (
          <p className={cx("flex items-center gap-2 px-5 pb-5 text-sm", CONSOLE.muted)}><FiInfo aria-hidden="true" /> Activity couldn&rsquo;t be read.</p>
        ) : activity.loading && !activity.data ? (
          <p className={cx("px-5 pb-5 text-sm", CONSOLE.faint)}>Reading…</p>
        ) : !activity.data?.items?.length ? (
          <p className={cx("px-5 pb-5 text-sm", CONSOLE.muted)}>No activity has been recorded for this event yet.</p>
        ) : (
          <ul className={cx("divide-y border-t", CONSOLE.divider, CONSOLE.divideY)}>
            {activity.data.items.map((item) => (
              <li key={item.id} className="flex flex-wrap items-center justify-between gap-3 px-5 py-3">
                <div className="min-w-0">
                  <p className={cx("truncate text-sm font-medium", CONSOLE.heading)}>{item.title}</p>
                  <p className={cx("text-[12px]", CONSOLE.faint)}>
                    {item.actor?.name} · {fmtZonedShort(item.at, event.timezone)}
                  </p>
                </div>
                <Badge tone={OUTCOME_TONE[item.outcome] || "neutral"}>{item.outcome_label}</Badge>
              </li>
            ))}
          </ul>
        )}
      </Panel>

      <Footnote>
        Stages show where the event&rsquo;s status places it; they are not a claim that any stage&rsquo;s work is automated.
        <Link to={sectionPath(eventId, "activity")} className={cx("ml-1 font-semibold", CONSOLE.link)}>
          <FiCheckCircle aria-hidden="true" className="mr-0.5 inline" />Every change is recorded
        </Link>
      </Footnote>
    </div>
  );
}
