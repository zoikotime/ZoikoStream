import {
  FiActivity, FiArchive, FiCheck, FiClock, FiFilm, FiLink, FiLock, FiPlay, FiShield, FiX,
} from "react-icons/fi";
import api from "../../../api";
import useApi from "../../../hooks/useApi";
import RecordingPanel from "../../../components/organization/RecordingPanel";
import { ConsoleButton as Button } from "../../../ui/Button";
import { CONSOLE, cx } from "../../../ui/tokens";
import { Banner, DarkCard, Footnote, KpiRow, KpiTile, Panel, PanelLink, Tag } from "../../../components/organization/console/ConsoleUI";
import EventSectionHeader from "./EventSectionHeader";
import ReportsPanel from "./ReportsPanel";
import { sectionPath, useEventWorkspace, useHashScroll } from "./workspace";
import { fmtSeconds, fmtZonedShort } from "../../../data/eventTime";

// EVT-05 Replay & Assets (EVT-05 frame): every recording attempt for this event with its real
// state, what processing and publication gates it has passed, and whether viewers may watch a
// replay — which is a SEPARATE decision (the replay entitlement), never inferred from a
// recording having finished. Retention, legal hold and governed export are operated from the
// organization's Media & Recordings library, where those controls already live.

const STATE = {
  ready: { label: "Ready internally", tone: "green", caption: "Captured; playback still policy-gated" },
  processing: { label: "Processing", tone: "sky", caption: "No replay until complete" },
  in_progress: { label: "Recording", tone: "rose", caption: "Capture ongoing; asset not ready" },
  failed: { label: "Failed", tone: "rose", caption: "Diagnostics needed" },
  storage_unavailable: { label: "Storage unavailable", tone: "amber", caption: "The file is not in storage" },
};
const PUBLISH = {
  published: "Published",
  validating: "Validating",
  not_available: "Not published",
  expired: "Expired",
  withdrawn: "Withdrawn",
};
const humanize = (s) => (s || "").replace(/[_.-]+/g, " ").replace(/^\w/, (c) => c.toUpperCase());

function Gate({ title, detail, done, state, tone }) {
  return (
    <li className="flex items-center gap-3 py-2.5">
      <span aria-hidden="true" className={cx(
        "grid h-7 w-7 shrink-0 place-items-center rounded-full text-[12px]",
        done ? "bg-emerald-50 text-emerald-600 dark:bg-emerald-500/15 dark:text-emerald-300"
          : tone === "rose" ? "bg-rose-50 text-rose-600 dark:bg-rose-500/15 dark:text-rose-300"
            : "bg-slate-100 text-slate-500 dark:bg-white/[0.08] dark:text-neutral-300"
      )}>
        {done ? <FiCheck /> : tone === "rose" ? <FiX /> : <FiClock />}
      </span>
      <span className="min-w-0 flex-1">
        <span className={cx("block text-[13px] font-semibold", CONSOLE.heading)}>{title}</span>
        <span className={cx("block text-[11.5px]", CONSOLE.faint)}>{detail}</span>
      </span>
      <span className={cx("text-[10.5px] font-bold uppercase tracking-wide",
        done ? "text-emerald-600 dark:text-emerald-300" : tone === "rose" ? "text-rose-600 dark:text-rose-300" : CONSOLE.faint)}>{state}</span>
    </li>
  );
}

export default function EventReplay() {
  const { event, eventId } = useEventWorkspace();
  useHashScroll();
  // `.catch(() => null)`: RecordingPanel distinguishes "could not read" (null) from "none"
  // ([]), so a failed request never reads as "no recording exists".
  const recordings = useApi(() =>
    api.get(`/organization/events/${eventId}/recordings`).then((r) => r.data).catch(() => null)
  );
  const entitlements = useApi(() =>
    api.get(`/commercial/events/${eventId}/replay-entitlements`).then((r) => r.data).catch(() => null)
  );

  const rows = recordings.data || [];
  const master = rows.find((r) => r.role === "primary") || rows[0] || null;
  const st = master ? STATE[master.state] || STATE.processing : null;
  const audience = (entitlements.data || []).find((e) => e.scope === "audience") || null;
  const published = audience?.publish_state === "published";
  const held = rows.some((r) => r.legal_hold);

  return (
    <div id="overview" data-section="overview" className="scroll-mt-24 space-y-6">
      <EventSectionHeader
        icon={FiFilm}
        eyebrow="Event workspace / 05"
        title="Replay & Assets"
        subtitle="Recording lifecycle, protected playback and governed media preservation."
        actions={
          <>
            <Button variant="secondary" size="sm" leftIcon={FiActivity} href={`${sectionPath(eventId, "activity")}?category=evidence`}>View activity</Button>
            <Button size="sm" leftIcon={FiArchive} href="/organization/recordings">Media library</Button>
          </>
        }
      />

      {published ? (
        <Banner tone="info" title="Replay published to the event audience" aside={<Tag tone="green">Viewer replay on</Tag>}>
          Viewers allowed by the event&rsquo;s access settings can watch the replay{audience?.expires_at ? ` until ${fmtZonedShort(audience.expires_at, event.timezone)}` : ""}.
        </Banner>
      ) : (
        <Banner tone="warning" title="Public replay not published — internal access only" aside={<Tag tone="amber">Internal access only</Tag>}>
          An internal recording may be ready, but publication to viewers is a separate, gated decision. Viewer permission is never
          inferred from a recording completing.
        </Banner>
      )}

      <KpiRow>
        <KpiTile icon={FiFilm} tone="violet" label="Master recording" emphasis="text"
          value={recordings.loading && recordings.data === null ? "…" : recordings.data === null ? null : st ? st.label : "None"}
          caption={recordings.data === null && !recordings.loading ? "Recordings couldn't be read" : st ? st.caption : event.recording_enabled ? "Recording is on; nothing captured yet" : "Recording is off for this event"} />
        <KpiTile icon={FiActivity} tone="amber" label="Additional processing" emphasis="text"
          value={master ? (master.validation_status ? humanize(master.validation_status) : "None recorded") : "—"}
          caption="Validation of the captured file" />
        <KpiTile icon={FiShield} tone="sky" label="Public replay access" emphasis="text"
          value={entitlements.data === null && !entitlements.loading ? null : audience ? PUBLISH[audience.publish_state] || humanize(audience.publish_state) : "Not set up"}
          caption={audience ? `Watermark ${humanize(audience.watermark_status)}` : "No replay entitlement for viewers"} />
        <KpiTile icon={FiClock} tone="green" label="Retention & export" emphasis="text"
          value={held ? "Legal hold" : "Library-governed"} caption="Managed in Media & Recordings" to="/organization/recordings" />
      </KpiRow>

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1.35fr)_minmax(0,1fr)]">
        <Panel title="Master programme recording" subtitle="Event-scoped master asset · as the recorder reported it"
          tag={st ? st.label : "No recording"} tagTone={st ? st.tone : "slate"}>
          {master ? (
            <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
              <DarkCard as="div" className="flex min-h-[11rem] flex-col justify-between p-5">
                <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-fuchsia-200">ZoikoStream · private preview</p>
                <div className="text-center">
                  <p className="text-[18px] font-bold">{event.title || "Untitled event"}</p>
                  <span aria-hidden="true" className="mx-auto mt-3 grid h-11 w-11 place-items-center rounded-full border border-white/30 bg-white/10"><FiPlay /></span>
                </div>
                <div className="flex items-center justify-between text-[10px] font-bold uppercase tracking-[0.14em]">
                  <span className="inline-flex items-center gap-1.5"><FiLock aria-hidden="true" /> Internal only</span>
                  <span>{master.duration_seconds != null ? fmtSeconds(master.duration_seconds) : "—"}</span>
                </div>
              </DarkCard>
              <div>
                <p className="text-[11px] font-bold uppercase tracking-[0.12em] text-violet-600 dark:text-violet-300">
                  {master.role ? `${humanize(master.role)} recording` : "Master recording"} · {master.quality || "—"}
                </p>
                <p className={cx("mt-1 text-[16px] font-semibold", CONSOLE.heading)}>{event.title || "Untitled event"}</p>
                <dl className="mt-3 grid grid-cols-2 gap-3 text-[12.5px]">
                  {[
                    ["Recording state", st?.label || "—"],
                    ["Public playback", audience ? PUBLISH[audience.publish_state] || humanize(audience.publish_state) : "Not published"],
                    ["Asset scope", "This event only"],
                    ["Legal hold", master.legal_hold ? "Held" : "None"],
                  ].map(([k, v]) => (
                    <div key={k}>
                      <dt className={CONSOLE.faint}>{k}</dt>
                      <dd className={cx("font-semibold", CONSOLE.heading)}>{v}</dd>
                    </div>
                  ))}
                </dl>
                <div className="mt-4 flex flex-wrap gap-2">
                  <Button size="sm" leftIcon={FiPlay} href="#recordings">Internal preview</Button>
                  <Button variant="secondary" size="sm" leftIcon={FiLock} href="/organization/recordings">Export in library</Button>
                </div>
              </div>
            </div>
          ) : (
            <p className={cx("text-sm", CONSOLE.muted)}>
              {recordings.data === null && !recordings.loading ? "Recordings couldn't be read." : event.recording_enabled ? "Recording is on for this event. Nothing has been captured yet." : "Recording is off for this event."}
            </p>
          )}
          <Footnote>Internal readiness is not public replay authorization. Every media action stays server-scoped and audited.</Footnote>
        </Panel>

        <Panel title="Processing & publication gates" subtitle="Technical readiness kept apart from viewer authorization" tag="From the server" tagTone="slate">
          <ul className={cx("divide-y", CONSOLE.divideY)}>
            <Gate title="Capture ingested" detail={master ? `Started ${fmtZonedShort(master.started_at, event.timezone)}` : "No capture recorded"}
              done={Boolean(master && master.enforced)} state={master ? (master.enforced ? "Done" : "Not accepted") : "Not started"} tone={master && !master.enforced ? "rose" : undefined} />
            <Gate title="Integrity / metadata checks" detail="Validation of the captured file"
              done={master?.validation_status === "valid"} state={master?.validation_status ? humanize(master.validation_status) : "Not run"}
              tone={master?.validation_status === "failed" ? "rose" : undefined} />
            <Gate title="Master file available" detail={st ? st.caption : "No file"}
              done={master?.state === "ready"} state={st ? st.label : "—"} tone={master?.state === "failed" ? "rose" : undefined} />
            <Gate title="Viewer replay entitlement" detail={audience ? `Scope: ${audience.scope}` : "Not set up for this event"}
              done={Boolean(audience)} state={audience ? "Set" : "Pending"} />
            <Gate title="Public replay publication" detail={audience ? `Watermark ${humanize(audience.watermark_status)}` : "Not authorized"}
              done={published} state={audience ? PUBLISH[audience.publish_state] || humanize(audience.publish_state) : "Blocked"} tone={published ? undefined : "rose"} />
          </ul>
        </Panel>
      </div>

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1.35fr)_minmax(0,1fr)]">
        <Panel id="recordings" title="Asset inventory & recordings" subtitle="Every attempt, including failures, with the server's own reason." flush>
          <div className="border-t border-slate-100 dark:border-white/[0.08]">
            {recordings.loading && recordings.data === null ? (
              <p className={cx("p-5 text-sm", CONSOLE.faint)}>Loading recordings…</p>
            ) : (
              <RecordingPanel event={event} recordings={recordings.data} />
            )}
          </div>
        </Panel>
        <Panel id="retention" title="Replay access & preservation" subtitle="Policy decisions require explicit approval" tag="Governed" tagTone="violet">
          <ul className={cx("divide-y", CONSOLE.divideY)}>
            {[
              [FiShield, "Viewer access profile", "Follows the event's access settings", "Enforced", "violet"],
              [FiLink, "Shareable replay link", published ? "Available to the event audience" : "Not available until published", published ? "Live" : "Blocked", published ? "green" : "rose"],
              [FiClock, "Retention & legal hold", held ? "A recording is under legal hold" : "Retention runs per the library's policy", held ? "Held" : "Library", held ? "amber" : "slate"],
            ].map(([Icon, title, detail, tag, tone]) => (
              <li key={title} className="flex items-center gap-3 py-3">
                <Icon aria-hidden="true" className={cx("shrink-0", CONSOLE.faint)} />
                <span className="min-w-0 flex-1">
                  <span className={cx("block text-[13px] font-semibold", CONSOLE.heading)}>{title}</span>
                  <span className={cx("block text-[11.5px]", CONSOLE.faint)}>{detail}</span>
                </span>
                <Tag tone={tone}>{tag}</Tag>
              </li>
            ))}
          </ul>
          <p className={cx("mt-2 text-[12px]", CONSOLE.muted)}>
            Retention windows, legal holds and controlled exports are managed in{" "}
            <PanelLink to="/organization/recordings">Media &amp; Recordings</PanelLink>. Export links expire, and creating,
            downloading or revoking one is recorded in Activity &amp; Audit.
          </p>
        </Panel>
      </div>

      <div id="reports" className="scroll-mt-24">
        <ReportsPanel event={event} />
      </div>
    </div>
  );
}
