import { Link, useLocation } from "react-router-dom";
import { FiBarChart2, FiCheck, FiClock, FiPlay, FiRadio, FiStar } from "react-icons/fi";
import api from "../../../api";
import useApi from "../../../hooks/useApi";
import OrganizationErrorState from "../../../components/organization/OrganizationErrorState";
import OrganizationEmptyState from "../../../components/organization/OrganizationEmptyState";
import { ConsoleButton as Button } from "../../../ui/Button";
import { AreaTrend } from "../../../ui/charts";
import { CONSOLE, cx, focusRing } from "../../../ui/tokens";
import { Banner, FactList, Footnote, KpiRow, KpiTile, Panel, Tag } from "../../../components/organization/console/ConsoleUI";
import EventSectionHeader from "./EventSectionHeader";
import Freshness from "../../../components/organization/Freshness";
import { sectionPath, useEventWorkspace, useHashScroll } from "./workspace";
import { fmtSeconds, fmtUtc, fmtZonedShort, zonedParts } from "../../../data/eventTime";

// EVT-06 Insights (EVT-06 frame): the event's audience, delivery quality and outcomes, from
// /organization/events/{id}/insights (services/event_workspace).
//
// Null is not zero. A figure the platform never measured — peak viewers of an event that never
// went on air, an average over no viewers, a rating nobody gave — is shown as "—" with the
// reason beside it, never as a 0 that would claim a measurement (ZST-ENG-DASH-001 §7.3;
// handoff scenario 9: "no fabricated metrics"). Playback sessions are not people, and the page
// says so where the two could be confused.

const pct = (part, whole) => (whole ? Math.round((100 * part) / whole) : null);
const ratio = (r) => (r == null ? null : r < 0.001 && r > 0 ? "<0.1%" : `${(r * 100).toFixed(1)}%`);

const TABS = [
  ["overview", "Overview"],
  ["viewers", "Viewer activity"],
  ["quality", "Stream quality"],
  ["outcomes", "Event outcomes"],
];
const INSIGHTS_HASH_SCROLL = {
  supportedHashes: [...TABS.map(([anchor]) => anchor), "feedback"],
  fallbackHash: "overview",
};

function Bars({ rows, empty }) {
  if (!rows?.length) return <p className={cx("text-sm", CONSOLE.muted)}>{empty}</p>;
  const colors = ["bg-violet-500", "bg-sky-500", "bg-fuchsia-500", "bg-teal-500", "bg-slate-400"];
  return (
    <ul className="space-y-3">
      {rows.map((r, i) => (
        <li key={r.key}>
          <div className="flex justify-between text-[13px]">
            <span className={CONSOLE.body}>{r.label}</span>
            <span className={cx("font-semibold tabular-nums", CONSOLE.heading)}>{r.value}</span>
          </div>
          <div className="mt-1 h-2 overflow-hidden rounded-full bg-slate-100 dark:bg-white/[0.08]" aria-hidden="true">
            <div className={cx("h-full rounded-full", colors[i % colors.length])} style={{ width: `${r.pct ?? 0}%` }} />
          </div>
        </li>
      ))}
    </ul>
  );
}

function Feedback({ eventId }) {
  const fb = useApi(() => api.get(`/events/${eventId}/feedback`, { params: { role: "viewer" } }).then((r) => r.data));
  const rows = fb.data || [];
  return (
    <Panel id="feedback" title="Viewer feedback" subtitle="Ratings and comments viewers left when they exited.">
      {fb.error ? (
        <OrganizationErrorState error={fb.error} onRetry={fb.reload} title="Couldn't load feedback" />
      ) : !fb.data ? (
        <p className={cx("text-sm", CONSOLE.faint)}>Loading…</p>
      ) : rows.length === 0 ? (
        <OrganizationEmptyState icon={FiStar} title="No viewer feedback yet" description="Feedback appears here once viewers leave the event." />
      ) : (
        <ul className="grid gap-3 sm:grid-cols-2">
          {rows.map((f) => (
            <li key={f.id} className={cx(CONSOLE.inset, "space-y-1.5 p-3")}>
              <div className="flex items-center justify-between gap-2">
                <span aria-label={f.rating ? `${f.rating} out of 5 stars` : "No rating"} className="flex items-center gap-0.5">
                  {f.rating
                    ? [1, 2, 3, 4, 5].map((n) => (
                        <FiStar key={n} aria-hidden="true"
                          className={cx("h-3.5 w-3.5", n <= f.rating ? "fill-amber-400 text-amber-400" : "text-slate-300 dark:text-slate-700")} />
                      ))
                    : <span className={cx("text-xs", CONSOLE.faint)}>No rating</span>}
                </span>
                <span className={cx("text-[11px]", CONSOLE.faint)}>{f.created_at ? new Date(f.created_at).toLocaleString() : ""}</span>
              </div>
              {f.name && <p className={cx("text-xs font-medium", CONSOLE.muted)}>{f.name}</p>}
              {f.comment && <p className={cx("text-sm", CONSOLE.body)}>{f.comment}</p>}
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

export default function EventInsights() {
  const { event, eventId } = useEventWorkspace();
  const { hash } = useLocation();
  const ins = useApi(() => api.get(`/organization/events/${eventId}/insights`).then((r) => r.data));
  // Viewer feedback renders at once, but every panel above it mounts only when this request
  // answers. Scrolling before that put #feedback under them (277px down instead of 96px), so the
  // hash waits for it, as Live Control and Preparation do. Charts keep a fixed height, so nothing
  // above moves afterwards; data/error persist across reloads, so a refresh does not re-scroll.
  useHashScroll({ ...INSIGHTS_HASH_SCROLL, ready: Boolean(ins.data) || Boolean(ins.error) });
  const d = ins.data;
  const total = d?.playback?.total || {};
  const live = d?.playback?.live || {};
  const replay = d?.playback?.replay || {};
  const startRate = pct(total.playback_starts || 0, total.sessions || 0);
  const series = (d?.timeline || []).map((p) => ({ label: zonedParts(p.t, event.timezone).time, value: p.viewers }));
  const peakPoint = series.reduce((best, p) => (p.value > (best?.value ?? -1) ? p : best), null);
  const o = d?.outcomes;

  return (
    <div className="space-y-6">
      <EventSectionHeader
        icon={FiBarChart2}
        eyebrow="Event workspace / 06"
        title="Insights"
        subtitle="Event-scoped audience activity, streaming quality and outcome intelligence."
        actions={
          <>
            {d && <Freshness asOf={d.as_of} staleAfter={300} failed={Boolean(ins.error)} onRefresh={ins.reload} refreshing={ins.loading} />}
            <Button variant="secondary" size="sm" href={`${sectionPath(eventId, "replay")}#reports`}>Reports</Button>
          </>
        }
      />

      {ins.error && !d ? (
        <OrganizationErrorState error={ins.error} onRetry={ins.reload} title="Couldn't load insights" />
      ) : !d ? (
        <p className={cx("text-sm", CONSOLE.faint)}>Loading insights…</p>
      ) : (
        <>
          <div id="overview" className="scroll-mt-24 space-y-6">
            <Banner tone="info" title={d.measured ? "Measured from this event's own sessions — nothing estimated" : "This event has not been broadcast"}
              aside={<Tag tone={d.measured ? "green" : "slate"}>{d.measured ? "Measured" : "No live telemetry"}</Tag>}>
              {d.measured
                ? "Playback sessions are not unique people. Figures nobody measured show as “—” with the reason."
                : "Live audience figures have not been measured, so they show as “—”. Nothing below is estimated."}
            </Banner>

            <KpiRow>
              <KpiTile icon={FiPlay} tone="violet" label="Playback start attempts" value={(total.sessions ?? 0).toLocaleString()}
                caption="Player sessions, live and replay" />
              <KpiTile icon={FiRadio} tone="sky" label="Peak viewers" value={d.audience.peak_viewers}
                caption={d.audience.peak_viewers == null ? "Not broadcast yet" : "Highest concurrent audience"} />
              <KpiTile icon={FiCheck} tone="green" label="Playback start success" value={startRate == null ? null : `${startRate}%`}
                caption={total.sessions ? `${total.playback_starts} successful / ${total.sessions} attempts` : "No playback attempts yet"} />
              <KpiTile icon={FiClock} tone="sky" label="Avg watch time"
                value={d.audience.avg_watch_seconds == null ? null : fmtSeconds(d.audience.avg_watch_seconds)}
                caption={d.audience.avg_watch_seconds == null ? "No viewers yet" : "Per identified viewer"} />
            </KpiRow>
          </div>

          <div className="flex flex-wrap items-center justify-between gap-3">
            <nav aria-label="Insights sections" className={cx("inline-flex rounded-xl border p-1", CONSOLE.divider)}>
              {TABS.map(([anchor, label]) => {
                const on = hash === `#${anchor}` || (!hash && anchor === "overview");
                return (
                  <Link
                    key={label}
                    to={`${sectionPath(eventId, "insights")}#${anchor}`}
                    aria-current={on ? "true" : undefined}
                    className={cx(
                      "rounded-lg px-3.5 py-1.5 text-[13px] font-medium",
                      focusRing,
                      on ? "bg-violet-50 text-violet-700 dark:bg-violet-500/15 dark:text-violet-200" : CONSOLE.muted
                    )}
                  >
                    {label}
                  </Link>
                );
              })}
            </nav>
            <p className={cx("text-[12px]", CONSOLE.faint)}>
              Dataset as of {fmtZonedShort(d.as_of, event.timezone)} · {fmtUtc(d.as_of)}
            </p>
          </div>

          <div className="grid gap-6 xl:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
            <Panel id="viewers" title="Concurrent viewers" subtitle="Active viewers over event time · sampled while live, not a count of distinct people"
              tag={peakPoint ? `Peak ${peakPoint.value}` : undefined} tagTone="violet">
              {series.length < 2 ? (
                <p className={cx(CONSOLE.inset, "px-4 py-8 text-center text-sm", CONSOLE.muted)}>
                  {d.measured ? "Too few live samples were recorded to draw a timeline." : "No live samples were recorded."}
                </p>
              ) : (
                <>
                  <AreaTrend data={series} height={220} showX name="Concurrent viewers" labelName="Time" />
                  <p className={cx("mt-2 text-[11.5px]", CONSOLE.faint)}>
                    Event local time · {event.timezone || "local"} · source: live presence samples
                  </p>
                </>
              )}
            </Panel>

            <Panel id="quality" title="Playback quality & delivery" subtitle="Session-level signals · event scope">
              <FactList rows={[
                ["Successful playback starts", total.sessions ? `${total.playback_starts} · ${startRate}%` : "—"],
                ["Failed sessions", (total.failed_sessions ?? 0).toLocaleString()],
                ["Rebuffering (live)", ratio(live.rebuffer_ratio) ?? "—"],
                ["Median startup (live)", live.startup_ms_median != null ? `${(live.startup_ms_median / 1000).toFixed(1)} s` : "—"],
                ["Replay starts", (replay.playback_starts ?? 0).toLocaleString()],
              ]} />
              <p className="mt-3 rounded-lg border border-violet-200/70 bg-violet-50/60 px-3 py-2 text-[12px] text-violet-800 dark:border-violet-400/25 dark:bg-violet-500/10 dark:text-violet-200">
                <strong>Operational boundary.</strong> Playback quality is reported by viewers&rsquo; players and is distinct from ingest,
                encoder and recording health. No live status is inferred from these figures.
              </p>
            </Panel>
          </div>

          <div className="grid gap-6 xl:grid-cols-3">
            <Panel id="outcomes" title="Viewing outcomes" subtitle={o ? `Among ${o.viewers} identified viewer(s)` : "Counted from recorded watch time"} tag="Session-based" tagTone="violet">
              {!o ? (
                <p className={cx("text-sm", CONSOLE.muted)}>No viewers yet, so there is nothing to count.</p>
              ) : (
                <Bars
                  rows={[
                    { key: "10", label: "Watched at least 10 min", value: `${o.watched_10_min} · ${pct(o.watched_10_min, o.viewers)}%`, pct: pct(o.watched_10_min, o.viewers) },
                    { key: "50", label: "Reached 50% of programme", value: o.reached_half == null ? "—" : `${o.reached_half} · ${pct(o.reached_half, o.viewers)}%`, pct: pct(o.reached_half || 0, o.viewers) },
                    { key: "90", label: "Reached 90% of programme", value: o.reached_ninety == null ? "—" : `${o.reached_ninety} · ${pct(o.reached_ninety, o.viewers)}%`, pct: pct(o.reached_ninety || 0, o.viewers) },
                  ]}
                />
              )}
              {o && !o.programme_seconds && <Footnote>The programme length is the time on air; with nothing broadcast, the programme thresholds are not measured.</Footnote>}
            </Panel>

            <Panel title="Approximate regional reach" subtitle="Viewers who shared a country · no person-level map" tag="Privacy-safe" tagTone="green">
              <Bars
                rows={(d.geography || []).slice(0, 4).map((g) => ({ key: g.country_code, label: g.country_name, value: `${g.percentage ?? 0}%`, pct: g.percentage }))}
                empty="No viewer has shared a country."
              />
              {d.geography_unknown > 0 && <Footnote>{d.geography_unknown} viewer(s) gave no country and are not guessed.</Footnote>}
            </Panel>

            <Panel title="Confidence & reporting" subtitle="Metadata needed to interpret these figures" tag="Governed" tagTone="slate">
              <FactList rows={[
                ["Data state", d.measured ? "Measured · this event" : "Not broadcast"],
                ["Unique viewers", (d.audience.unique_viewers ?? 0).toLocaleString()],
                ["Time on air", d.broadcast.on_air_seconds == null ? "Not broadcast yet" : fmtSeconds(d.broadcast.on_air_seconds)],
                ["Source freshness", `${fmtZonedShort(d.as_of, event.timezone)} / ${fmtUtc(d.as_of).split(" · ")[1]}`],
                ["Viewer identity", "Not inferred from sessions"],
                ["Registered → attended", d.registration.applicable ? `${d.registration.attended} / ${d.registration.registered}` : "Nobody registered"],
                ["Average rating", d.feedback.average_rating == null ? "No ratings yet" : `${d.feedback.average_rating.toFixed(1)} / 5`],
              ]} />
            </Panel>
          </div>

          <div className="grid gap-6 lg:grid-cols-2">
            <Panel title="Devices" subtitle="Share of identified viewers">
              <Bars
                rows={(d.device_mix || []).map((r) => ({ key: r.device_type, label: r.label, value: `${r.viewers} · ${r.percentage ?? 0}%`, pct: r.percentage }))}
                empty="No viewers yet."
              />
            </Panel>
            <Panel title="Join refusals" subtitle="Attempts the server turned away">
              {d.blocked_join_attempts.total === 0 ? (
                <p className={cx("text-sm", CONSOLE.muted)}>No join attempt was refused.</p>
              ) : (
                <FactList rows={d.blocked_join_attempts.reasons.map((r) => [r.label, r.count])} />
              )}
            </Panel>
          </div>
        </>
      )}

      <Feedback eventId={eventId} />
    </div>
  );
}
