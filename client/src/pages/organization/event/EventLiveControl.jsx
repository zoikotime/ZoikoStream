import { useState } from "react";
import {
  FiActivity, FiAlertTriangle, FiBookOpen, FiClock, FiExternalLink, FiFilm, FiGlobe, FiLock, FiPlay,
  FiRadio, FiRepeat, FiShield, FiVideo,
} from "react-icons/fi";
import { Link } from "react-router-dom";
import api from "../../../api";
import useApi from "../../../hooks/useApi";
import useInterval from "../../../hooks/useInterval";
import OrganizationErrorState from "../../../components/organization/OrganizationErrorState";
import { ConsoleButton as Button } from "../../../ui/Button";
import Badge from "../../../ui/Badge";
import { CONSOLE, cx, focusRing } from "../../../ui/tokens";
import { ON_AIR_STATUSES } from "../../../data/events";
import { Banner, DarkCard, Footnote, KpiRow, KpiTile, Panel, PanelLink, Tag } from "../../../components/organization/console/ConsoleUI";
import Freshness from "../../../components/organization/Freshness";
import EventSectionHeader from "./EventSectionHeader";
import EventLifecycleActions from "./EventLifecycleActions";
import { sectionPath, useEventWorkspace, useHashScroll } from "./workspace";
import { ageOf, fmtSeconds, fmtUtc, fmtZoned, fmtZonedShort, secondsSince } from "../../../data/eventTime";

// EVT-04 Live Control (ZST-UX-NAV-001 §9.2; EVT-04 frame) — a monitor, safety first.
//
//  * LIVE / NOT LIVE in words and colour, with the event's clock and UTC side by side.
//  * Contribution, broadcast, delivery and recording are separate signals, each from its own
//    source; none is inferred from another.
//  * Every reading carries its age. Past the threshold, or after a failed refresh, it says
//    STALE and stops showing a healthy colour (§7.3: "do not retain HEALTHY indefinitely").
//  * Nothing here starts, switches or stops a stream by being opened, refreshed or polled.
//    Start, source switching and recording controls live in the Producer Console; the only
//    action on this page is End, behind an explicit confirmation, re-checked by the server.

const POLL_ON_AIR_MS = 10_000;
const POLL_OFF_AIR_MS = 60_000;

const HEALTH = {
  ok: { tone: "green", label: "Healthy" },
  warn: { tone: "amber", label: "Degraded" },
  down: { tone: "rose", label: "Down" },
};
const humanize = (s) => (s || "").replace(/[_.-]+/g, " ").replace(/^\w/, (c) => c.toUpperCase());

function PathRow({ icon: Icon, title, detail, tag, tone }) {
  return (
    <li className="flex items-center gap-3 py-3">
      <span aria-hidden="true" className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-slate-100 text-slate-500 dark:bg-white/[0.08] dark:text-neutral-300">
        <Icon />
      </span>
      <div className="min-w-0 flex-1">
        <p className={cx("text-[13.5px] font-semibold", CONSOLE.heading)}>{title}</p>
        <p className={cx("truncate text-[12px]", CONSOLE.faint)}>{detail}</p>
      </div>
      <Tag tone={tone} plain>{tag}</Tag>
    </li>
  );
}

function GovernedAction({ icon: Icon, label, note, to, danger }) {
  const body = (
    <>
      <span className="flex items-center gap-2">
        <Icon aria-hidden="true" />
        <span className="text-[13px] font-semibold">{label}</span>
      </span>
      <span className="text-[11px]">{note}</span>
    </>
  );
  const cls = cx(
    "flex flex-col items-start gap-1 rounded-xl border px-3.5 py-3 text-left",
    danger
      ? "border-rose-200 bg-rose-50/60 text-rose-700 dark:border-rose-400/25 dark:bg-rose-500/10 dark:text-rose-200"
      : "border-slate-200 bg-slate-50/70 text-slate-500 dark:border-white/[0.12] dark:bg-white/[0.04] dark:text-neutral-300"
  );
  return to ? (
    <Link to={to} className={cx(cls, "hover:border-violet-300", focusRing)}>{body}</Link>
  ) : (
    <div className={cls} aria-disabled="true">{body}</div>
  );
}

export default function EventLiveControl() {
  const { event, eventId, reload, readiness } = useEventWorkspace();
  useHashScroll();
  const onAir = ON_AIR_STATUSES.includes(event.status);
  const live = useApi(() => api.get(`/organization/events/${eventId}/live-status`).then((r) => r.data));
  const [now, setNow] = useState(() => Date.now());
  useInterval(live.reload, onAir ? POLL_ON_AIR_MS : POLL_OFF_AIR_MS);
  useInterval(() => setNow(Date.now()), 1000);

  const data = live.data;
  const staleAfter = onAir ? 30 : 180;
  const stale = Boolean(live.error) || (data && secondsSince(data.as_of, now) > staleAfter);
  // A stale reading keeps its words but loses its colour: an old "Healthy" is not health.
  const toneOf = (tone) => (stale ? "slate" : tone);

  const session = data?.current_session;
  const rec = data?.recording;
  const inputs = data?.inputs || [];
  const blockers = (readiness?.gates || []).filter((g) => g.required && !g.passed);
  const evidence = [...(readiness?.gates || [])].sort((a, b) => Number(a.passed) - Number(b.passed)).slice(0, 3);
  const refresh = () => { reload(); live.reload(); };

  return (
    <div className="space-y-6">
      <EventSectionHeader
        icon={FiActivity}
        eyebrow="Event workspace / 04"
        title="Live Control"
        subtitle="Production-safe monitoring, stream diagnostics and event-scoped operational recovery."
        actions={
          <>
            <Button variant="secondary" size="sm" leftIcon={FiBookOpen} href="#recovery">Incident runbook</Button>
            <Button size="sm" leftIcon={FiExternalLink} href={`/host/dashboard?event=${eventId}`}>Open Producer Console</Button>
          </>
        }
      />

      {onAir ? (
        <div
          role="status"
          aria-live="polite"
          className="flex flex-col gap-4 rounded-2xl border-2 border-rose-500 bg-rose-50 p-5 sm:flex-row sm:items-center sm:justify-between dark:border-rose-500/70 dark:bg-rose-500/10"
        >
          <div>
            <p className="text-[28px] font-black tracking-tight text-rose-600 dark:text-rose-400">
              {event.status === "degraded" ? "LIVE · DEGRADED" : "LIVE"}
            </p>
            <p className={cx("text-sm", CONSOLE.body)}>
              Event time {fmtZoned(new Date(now).toISOString(), event.timezone)} · {fmtUtc(new Date(now).toISOString())}
            </p>
          </div>
          <div className="flex flex-col items-start gap-2 sm:items-end">
            {data && <Freshness asOf={data.as_of} staleAfter={staleAfter} failed={Boolean(live.error)} onRefresh={live.reload} refreshing={live.loading} now={now} />}
            <EventLifecycleActions event={event} only={["end"]} onChanged={refresh} onBeforeConfirm={refresh} />
          </div>
        </div>
      ) : blockers.length > 0 ? (
        <Banner tone="warning" role="status" title={`Launch blocked — ${blockers.length} mandatory readiness check${blockers.length === 1 ? "" : "s"} unresolved`}
          aside={<Tag tone="amber">Not authorized to go live</Tag>}>
          <span className="font-semibold">Cannot go live until:</span> {blockers.map((g) => g.label).join("; ")}. This event stays{" "}
          {event.status.replace(/_/g, " ")}; no broadcast action is available here.
        </Banner>
      ) : (
        <Banner tone="info" role="status" title="NOT LIVE" aside={<Tag tone="slate">Off air</Tag>}>
          Event time {fmtZoned(new Date(now).toISOString(), event.timezone)} · {fmtUtc(new Date(now).toISOString())}. Go-live happens in the
          Producer Console; this page only monitors.
        </Banner>
      )}

      {live.error && !data ? (
        <OrganizationErrorState error={live.error} onRetry={live.reload} title="Live status couldn't be read — this never changes the broadcast" />
      ) : !data ? (
        <p className={cx("text-sm", CONSOLE.faint)}>Reading live status…</p>
      ) : (
        <>
          <KpiRow>
            <KpiTile icon={FiRadio} tone={onAir ? "rose" : "slate"} label="Broadcast state" emphasis="text"
              value={onAir ? (event.status === "degraded" ? "LIVE · degraded" : "LIVE") : "NOT LIVE"}
              caption={session ? `${humanize(session.status)} · on air ${fmtSeconds(session.on_air_seconds)}` : "No active production session"} />
            <KpiTile icon={FiVideo} tone="amber" label="Primary contribution" emphasis="text"
              value={inputs.length ? (inputs[0].signal_state === "healthy" ? "Signal healthy" : `Signal ${inputs[0].signal_state}`) : onAir ? "Browser" : "Unverified"}
              caption={inputs.length ? `${inputs[0].input_type.toUpperCase()} · ${humanize(inputs[0].state)}` : "No source test evidence recorded"} />
            <KpiTile icon={FiShield} tone="violet" label="Viewer delivery" emphasis="text"
              value={data.audience_now ? `${data.audience_now.viewers} watching` : data.audience_unavailable ? "Unavailable" : "Not delivering"}
              caption={data.audience_now ? `${data.audience_now.waiting} waiting · ${data.audience_now.poor_connections} poor connection(s)` : "Shown only while on air"} />
            <KpiTile icon={FiClock} tone="sky" label="Telemetry freshness" emphasis="text"
              value={stale ? "Stale" : ageOf(data.as_of, now)}
              caption={live.error ? "Last refresh failed" : `Threshold ${staleAfter}s ${onAir ? "on air" : "off air"}`} />
          </KpiRow>

          <div className="grid gap-6 xl:grid-cols-[minmax(0,1.35fr)_minmax(0,1fr)]">
            <Panel title="Live output monitor" subtitle="Independent output, source and recording states · never assume a successful broadcast"
              tag={onAir ? "On air" : "Off air"} tagTone={onAir ? "rose" : "slate"}>
              <DarkCard as="div" className="p-0">
                <div className="grid min-h-[10rem] place-items-center px-6 py-6 text-center">
                  <div>
                    <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-fuchsia-200">ZoikoStream · output monitor</p>
                    <p className="mt-2 text-[22px] font-bold">{event.title || "Untitled event"}</p>
                    <p className="mt-1 text-[12px] text-white/70">
                      {onAir ? "On air — watch the programme in the Producer Console" : "Pre-live placeholder · no programme signal"}
                    </p>
                  </div>
                </div>
                <div className="flex items-center justify-between border-t border-white/10 px-4 py-2 text-[10px] font-bold uppercase tracking-[0.14em]">
                  <span className="inline-flex items-center gap-1.5"><FiLock aria-hidden="true" /> {onAir ? "Output not embedded" : "No live output"}</span>
                  <span className="text-white/60">Preview only</span>
                </div>
              </DarkCard>
              <div className={cx("mt-2 flex flex-wrap items-center justify-between gap-2 text-[11.5px]", CONSOLE.faint)}>
                <span>This panel is not a live player or proof of viewer access.</span>
                <span>Source: {inputs.length ? inputs[0].signal_state : "browser"} · Recording: {rec ? rec.status : "idle"}</span>
              </div>
            </Panel>

            <Panel title="Signal & delivery path" subtitle="Separate ingest, encoder, playback and recording conditions"
              tag={stale ? "Data stale" : "Current"} tagTone={stale ? "amber" : "green"}>
              <ul className={cx("divide-y", CONSOLE.divideY)}>
                <PathRow icon={FiVideo} title="Contribution source"
                  detail={inputs.length ? `${inputs[0].title} · ${inputs[0].input_type.toUpperCase()}` : "Browser publishing from the Producer Console"}
                  tag={inputs.length ? (inputs[0].provisioning_failed ? "Not provisioned" : humanize(inputs[0].signal_state)) : onAir ? "Browser" : "Verification required"}
                  tone={toneOf(inputs.length && inputs[0].signal_state === "healthy" ? "green" : "amber")} />
                <PathRow icon={FiActivity} title="Broadcast health"
                  detail={session ? `Session ${humanize(session.status)}${session.health_changed_at ? ` · changed ${fmtZonedShort(session.health_changed_at, event.timezone)}` : ""}` : "No verified signal sample"}
                  tag={session ? (HEALTH[session.health_level] || { label: "Not evaluated" }).label : "Not available"}
                  tone={toneOf(session ? (HEALTH[session.health_level] || {}).tone || "slate" : "slate")} />
                <PathRow icon={FiGlobe} title="Playback delivery"
                  detail={data.audience_now ? `${data.audience_now.viewers} viewer(s) admitted` : "No output or viewer session"}
                  tag={data.audience_now ? "Active" : "Not active"} tone={toneOf(data.audience_now ? "green" : "slate")} />
                <PathRow icon={FiFilm} title="Recording process"
                  detail={rec ? (rec.enforced ? `Started ${fmtZonedShort(rec.started_at, event.timezone)}` : "Capture was not accepted by the recorder") : event.recording_enabled ? "Recording is on for this event" : "Recording is off for this event"}
                  tag={rec ? humanize(rec.status) : "Not started"}
                  tone={toneOf(rec?.status === "recording" ? "green" : rec?.status === "failed" ? "rose" : "slate")} />
              </ul>
              <div className={cx(CONSOLE.inset, "mt-2 grid grid-cols-2 gap-3 px-3.5 py-2.5 text-[12px]")}>
                <div>
                  <p className={CONSOLE.faint}>Latest verified signal</p>
                  <p className={cx("font-semibold", CONSOLE.heading)}>
                    {inputs[0]?.signal_changed_at ? fmtZonedShort(inputs[0].signal_changed_at, event.timezone) : session?.health_changed_at ? fmtZonedShort(session.health_changed_at, event.timezone) : "None recorded"}
                  </p>
                </div>
                <div>
                  <p className={CONSOLE.faint}>Current event phase</p>
                  <p className={cx("font-semibold capitalize", CONSOLE.heading)}>{event.status.replace(/_/g, " ")}</p>
                </div>
              </div>
            </Panel>
          </div>

          <div className="grid gap-6 xl:grid-cols-3">
            <Panel title="Pre-live readiness evidence" subtitle="Checks sourced from the event's preparation"
              action={<PanelLink to={`${sectionPath(eventId, "preparation")}#readiness`}>View checks</PanelLink>}>
              {evidence.length === 0 ? (
                <p className={cx("text-sm", CONSOLE.muted)}>Readiness couldn&rsquo;t be read.</p>
              ) : (
                <ol className={cx("divide-y", CONSOLE.divideY)}>
                  {evidence.map((g, i) => (
                    <li key={g.key} className="flex items-center gap-3 py-2.5">
                      <span className={cx("grid h-7 w-7 shrink-0 place-items-center rounded-lg text-[11px] font-bold", CONSOLE.inset)}>0{i + 1}</span>
                      <span className="min-w-0 flex-1">
                        <span className={cx("block truncate text-[13px] font-semibold", CONSOLE.heading)}>{g.label}</span>
                        <span className={cx("block text-[11.5px]", CONSOLE.faint)}>{g.required ? "Required" : "Recommended"}</span>
                      </span>
                      <Tag plain tone={g.passed ? "green" : g.required ? "rose" : "amber"}>{g.passed ? "Passed" : g.required ? "Unresolved" : "Open"}</Tag>
                    </li>
                  ))}
                </ol>
              )}
            </Panel>

            <Panel id="incidents" title="Incident & recovery desk" subtitle="Delays and holds announced for this event"
              action={<PanelLink to="#recovery">Open runbook</PanelLink>}>
              {data.open_incidents.length === 0 ? (
                <p className={cx("text-sm", CONSOLE.muted)}>No open incident.</p>
              ) : (
                <ul className={cx("divide-y", CONSOLE.divideY)}>
                  {data.open_incidents.map((i) => (
                    <li key={i.id} className="flex items-start gap-3 py-2.5">
                      <FiAlertTriangle aria-hidden="true" className="mt-0.5 shrink-0 text-amber-500" />
                      <span className="min-w-0 flex-1">
                        <span className={cx("block text-[13px] font-semibold", CONSOLE.heading)}>{humanize(i.reason_category)}</span>
                        <span className={cx("block text-[11.5px]", CONSOLE.faint)}>
                          Since {fmtZonedShort(i.started_at, event.timezone)}{i.customer_summary ? ` · ${i.customer_summary}` : ""}
                        </span>
                      </span>
                      <Tag tone="amber">{humanize(i.operational_state)}</Tag>
                    </li>
                  ))}
                </ul>
              )}
              <Footnote>Operational changes carry a request reference, a scoped privilege and an audit receipt.</Footnote>
            </Panel>

            <Panel title="Governed production actions" subtitle="Protected operations · not executable from this page" tag="Restricted" tagTone="slate">
              <div className="grid grid-cols-2 gap-2.5">
                <GovernedAction icon={FiPlay} label="Start broadcast" note="In the Producer Console" to={`/host/dashboard?event=${eventId}`} />
                <GovernedAction icon={FiRepeat} label="Switch source" note="In the Producer Console" to={`/host/dashboard?event=${eventId}`} />
                <GovernedAction icon={FiFilm} label="Recording controls" note="In the Producer Console" to={`/host/dashboard?event=${eventId}`} />
                <GovernedAction icon={FiAlertTriangle} label="End broadcast" note={onAir ? "Use End Event above" : "Only while on air"} danger />
              </div>
              <Footnote>Server authorization, explicit confirmation and a durable audit receipt are mandatory for every state-changing action.</Footnote>
            </Panel>
          </div>

          <Panel id="sessions" title="Stream health & sessions" subtitle={`Broadcast sessions for this event (${data.session_count} on record).`} flush={data.sessions.length > 0}>
            {data.sessions.length === 0 ? (
              <p className={cx("text-sm", CONSOLE.muted)}>No broadcast has started for this event.</p>
            ) : (
              <div className="overflow-x-auto border-t border-slate-100 dark:border-white/[0.08]">
                <table className="w-full min-w-[640px] text-left text-sm">
                  <thead className={cx("bg-slate-50/70 text-[11px] uppercase tracking-wider dark:bg-white/[0.03]", CONSOLE.muted)}>
                    <tr>
                      <th scope="col" className="px-5 py-2.5 font-semibold">Started (event time)</th>
                      <th scope="col" className="px-3 py-2.5 font-semibold">Status</th>
                      <th scope="col" className="px-3 py-2.5 font-semibold">On air</th>
                      <th scope="col" className="px-3 py-2.5 font-semibold">Peak viewers</th>
                      <th scope="col" className="px-3 py-2.5 font-semibold">Health</th>
                      <th scope="col" className="px-5 py-2.5 font-semibold">Ended</th>
                    </tr>
                  </thead>
                  <tbody className={cx("divide-y", CONSOLE.divideY)}>
                    {data.sessions.map((s) => (
                      <tr key={s.id}>
                        <td className={cx("px-5 py-2.5", CONSOLE.body)}>{fmtZonedShort(s.started_at, event.timezone)}</td>
                        <td className="px-3 py-2.5"><Badge tone={s.status === "live" ? "brand" : "neutral"} size="sm">{humanize(s.status)}</Badge></td>
                        <td className={cx("px-3 py-2.5", CONSOLE.body)}>{fmtSeconds(s.on_air_seconds)}</td>
                        <td className={cx("px-3 py-2.5", CONSOLE.body)}>{s.peak_viewers ?? "—"}</td>
                        <td className={cx("px-3 py-2.5", CONSOLE.body)}>{(HEALTH[s.health_level] || { label: "Not evaluated" }).label}</td>
                        <td className={cx("px-5 py-2.5", CONSOLE.body)}>
                          {s.ended_at ? `${fmtZonedShort(s.ended_at, event.timezone)}${s.ended_reason ? ` · ${humanize(s.ended_reason)}` : ""}` : "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Panel>
        </>
      )}

      <Panel id="recovery" title="Recovery procedures" subtitle="If something goes wrong on air.">
        <ol className={cx("list-decimal space-y-1.5 pl-5 text-sm", CONSOLE.body)}>
          <li>Check the Producer Console: it shows what the host&rsquo;s browser is actually publishing.</li>
          <li>If an external encoder dropped, restart it — the input reconnects to the same endpoint.</li>
          <li>Announce a delay or hold to viewers from the Producer Console rather than ending the event.</li>
          <li>Contact support from the Support page with this event&rsquo;s ID. Media delivery does not depend on this page being open.</li>
        </ol>
      </Panel>
    </div>
  );
}
