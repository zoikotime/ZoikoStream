import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  FiSearch, FiPlay, FiMonitor, FiShield, FiAlignLeft,
  FiTrendingUp, FiCheckSquare, FiChevronRight,
} from "react-icons/fi";
import api from "../../api";
import useApi from "../../hooks/useApi";
import useInterval from "../../hooks/useInterval";
import { ACCENT, CONSOLE, STAGE_COLOR, cx, focusRing, type } from "../../ui/tokens";
import { ConsoleButton } from "../../ui/Button";
import { Input } from "../../ui/forms";
import HealthDot from "../../components/admin/HealthDot";
import StatRow from "../../components/admin/StatRow";

// Developer Platform — the org console's build surface: what ZoikoStream is, the docs
// entry points, and whether the platform is currently in a state worth building against.
//
// The catalogue below is DOCUMENTATION, so it is static by definition. The two panels at
// the foot are posture, so they read from /organization/overview — the same payload the
// Overview page already polls. Figures with no producer in this stack render "—" with the
// reason (the convention DeveloperOps/SecuritySupport established), never a zero that
// would read as a clean bill of health.
const REFRESH_MS = 60_000;

// Published API version and deprecation notice — release facts, not telemetry.
const API_VERSION = "2026-06-01";
const LATEST_DEPRECATION = "Node SDK v2 · Oct 2026";

// The lifecycle, left to right, as a broadcast travels it. Doubles as the docs filter:
// picking a stage narrows the catalogue to the guides that touch it.
const STAGES = [
  ["contribute", "Contribute"],
  ["ingest", "Ingest"],
  ["produce", "Produce"],
  ["secure", "Secure"],
  ["deliver", "Deliver"],
  ["understand", "Understand"],
  ["preserve", "Preserve"],
  ["platform", "Platform"],
];

// Tinted icon tiles. Blue/violet/amber come from the shared ACCENT map; green has no ACCENT
// entry (and `emerald` is remapped to violet in index.css), so it is spelled out here.
const GREEN_TILE = "bg-green-100 text-green-600 dark:bg-green-500/15 dark:text-green-400";

// Each quickstart links to the console surface it is about, so the card is a real
// destination rather than a placeholder for docs that don't exist yet.
const QUICKSTARTS = [
  {
    icon: FiMonitor, tile: ACCENT.blue.chip, to: "/organization/live-inputs",
    title: "Send your first stream",
    desc: "Create a live input, connect an encoder over RTMP or SRT, and confirm ingest health.",
    crumb: "Contribute → Ingest", stages: ["contribute", "ingest"],
  },
  {
    icon: FiShield, tile: ACCENT.violet.chip, to: "/organization/playback",
    title: "Secure playback with signed tokens",
    desc: "Issue short-lived access grants and verify sessions before serving protected media.",
    crumb: "Secure", stages: ["secure"],
  },
  {
    icon: FiAlignLeft, tile: GREEN_TILE, to: "/organization/recordings",
    title: "Publish a replay",
    desc: "Turn a finished session into a governed on-demand asset with captions intact.",
    crumb: "Preserve", stages: ["preserve"],
  },
  {
    icon: FiTrendingUp, tile: ACCENT.blue.chip, to: "/organization/analytics",
    title: "Read analytics via the API",
    desc: "Pull QoE, concurrency, and usage metering for a session, event, or workspace.",
    crumb: "Understand", stages: ["understand"],
  },
  {
    icon: FiCheckSquare, tile: ACCENT.violet.chip, to: "/organization/events",
    title: "Integrate a managed Live Event",
    desc: "Understand the request, readiness, and status contract for operator-run events.",
    crumb: "Deliver", stages: ["deliver"],
  },
];

// Footer, in the design's order. `null` = no destination exists yet, so it renders as text.
const FOOTER_LINKS = [
  ["Feedback", null],
  ["Accessibility", null],
  ["Security", "/organization/settings?tab=security"],
  ["Status", "/organization/support"],
  ["Terms", null],
];

function FootPanel({ eyebrow, children }) {
  return (
    <section className={cx(CONSOLE.panel, "px-5 py-4")}>
      <p className={cx("mb-2 text-[10px] font-semibold uppercase tracking-[0.14em]", CONSOLE.faint)}>
        {eyebrow}
      </p>
      {children}
    </section>
  );
}

export default function DeveloperPlatform() {
  const [query, setQuery] = useState("");
  const [stage, setStage] = useState(null);

  // Posture only — the catalogue above renders immediately, so there is no page skeleton
  // to wait behind. Same endpoint the Overview polls; no new API surface.
  const { data, reload } = useApi(() =>
    api.get("/organization/overview", { params: { range: "24h" } }).then((r) => r.data)
  );
  useInterval(reload, REFRESH_MS);

  const health = data?.service_health || {};
  const openIncidents = useMemo(
    () => (data?.lifecycle || []).reduce((n, s) => n + (s.open_incidents || 0), 0),
    [data]
  );

  const guides = useMemo(() => {
    const q = query.trim().toLowerCase();
    return QUICKSTARTS.filter(
      (g) =>
        (!stage || g.stages.includes(stage)) &&
        (!q || `${g.title} ${g.desc} ${g.crumb}`.toLowerCase().includes(q))
    );
  }, [query, stage]);

  return (
    <div className="mx-auto max-w-[1500px] space-y-8">
      {/* ── Hero ───────────────────────────────────────────────────────────── */}
      <section
        className={cx(
          "relative isolate overflow-hidden rounded-2xl border px-4 py-12 text-center sm:px-8 sm:py-16",
          "border-slate-200 bg-white dark:border-white/10 dark:bg-black"
        )}
      >
        {/* Ambient glow. Literal violet/green (not `emerald`, which index.css remaps). */}
        <div aria-hidden="true" className="pointer-events-none absolute inset-0 -z-10">
          <div className="absolute -left-32 -top-24 h-72 w-72 rounded-full bg-violet-500/10 blur-3xl dark:bg-violet-500/20" />
          <div className="absolute -bottom-32 -right-24 h-72 w-72 rounded-full bg-green-500/[0.07] blur-3xl dark:bg-green-500/15" />
        </div>

        <p className={cx("text-[11px] uppercase tracking-[0.22em]", type.mono, CONSOLE.faint)}>
          Build on ZoikoStream
        </p>

        <h1
          className={cx(
            "mx-auto mt-4 max-w-[760px] text-[28px] font-bold leading-[1.15] tracking-tight sm:text-[36px] lg:text-[44px]",
            CONSOLE.heading
          )}
        >
          Ship secure live and on-demand{" "}
          <span className="bg-gradient-to-r from-violet-500 to-indigo-400 bg-clip-text text-transparent">
            video, end to end.
          </span>
        </h1>

        <p className={cx("mx-auto mt-5 max-w-[626px] text-[14px] leading-[25px]", CONSOLE.muted)}>
          ZoikoStream is the API platform for contribution, ingest, production, secure delivery,
          and playback — with managed Live Events for the moments that can’t be repeated.
        </p>

        <div className="mt-7 flex flex-wrap items-center justify-center gap-3">
          <ConsoleButton href="/organization/live-inputs" leftIcon={FiPlay}>
            Start the quickstart
          </ConsoleButton>
          <ConsoleButton href="/organization/events" variant="secondary">
            Request a Live Event
          </ConsoleButton>
        </div>

        {/* Filters the catalogue below — a live control, not decoration. */}
        <div className="relative mx-auto mt-7 max-w-[440px]">
          <FiSearch
            className={cx("pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-[15px]", CONSOLE.faint)}
            aria-hidden="true"
          />
          <Input
            variant="console"
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            aria-label="Search the developer guides"
            placeholder='Search "create live input", "verify webhook signature", "playback token"…'
            /* Side-only padding override, the same way PasswordField clears room for its
               eye icon — safe against Tailwind's shorthand ordering (no tailwind-merge here). */
            className="pl-10 text-center text-[13px]"
          />
        </div>
      </section>

      {/* ── Docs by lifecycle stage ────────────────────────────────────────── */}
      <section className={cx(CONSOLE.panel, "overflow-hidden")}>
        <p
          className={cx(
            "px-4 pt-4 text-center text-[10px] uppercase tracking-[0.14em] sm:px-6",
            type.mono,
            CONSOLE.faint
          )}
        >
          Find docs by lifecycle stage — Contribute → Preserve
        </p>
        <div className="overflow-x-auto">
          <ol
            className="grid min-w-[46rem] px-4 pb-6 pt-6 sm:px-8"
            style={{ gridTemplateColumns: `repeat(${STAGES.length}, minmax(0, 1fr))` }}
          >
            {STAGES.map(([key, label], i) => {
              const color = STAGE_COLOR[key];
              const next = STAGES[i + 1];
              const on = stage === key;
              return (
                <li key={key} className="relative flex flex-col items-center">
                  {/* Connector drawn from this cell so it never overflows the last one. */}
                  {next && (
                    <span
                      aria-hidden="true"
                      className="absolute left-1/2 top-[5px] h-[2px] w-full"
                      style={{
                        background: `linear-gradient(90deg, ${color}, ${STAGE_COLOR[next[0]]})`,
                        opacity: 0.55,
                      }}
                    />
                  )}
                  <button
                    type="button"
                    onClick={() => setStage(on ? null : key)}
                    aria-pressed={on}
                    className={cx("group relative z-10 flex flex-col items-center rounded-md px-2 py-1", focusRing)}
                  >
                    <span
                      className={cx(
                        "h-2.5 w-2.5 rounded-full transition-transform duration-150 motion-reduce:transition-none",
                        on ? "scale-150 ring-2 ring-offset-2 ring-offset-white dark:ring-offset-black" : "group-hover:scale-125"
                      )}
                      style={{ backgroundColor: color, "--tw-ring-color": color }}
                    />
                    <span
                      className={cx(
                        "mt-3 text-[12px] font-medium transition-colors duration-150 motion-reduce:transition-none",
                        on ? CONSOLE.heading : cx(CONSOLE.body, "group-hover:text-slate-900 dark:group-hover:text-white")
                      )}
                    >
                      {label}
                    </span>
                  </button>
                </li>
              );
            })}
          </ol>
        </div>
      </section>

      {/* ── Quickstarts ────────────────────────────────────────────────────── */}
      <section>
        <div className="flex items-center justify-between gap-4">
          <h2 className={cx("text-[20px] font-semibold tracking-tight", CONSOLE.heading)}>Quickstarts</h2>
          <button
            type="button"
            onClick={() => {
              setStage(null);
              setQuery("");
            }}
            className={cx("shrink-0 rounded text-[12px] font-semibold", CONSOLE.link, focusRing)}
          >
            All guides →
          </button>
        </div>

        {guides.length === 0 ? (
          <p className={cx("mt-5 rounded-xl border border-dashed px-5 py-10 text-center text-[13px]", CONSOLE.divider, CONSOLE.muted)}>
            No guide matches that search.
          </p>
        ) : (
          <div className="mt-5 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {guides.map(({ icon: Icon, tile, title, desc, crumb, to }) => (
              <Link
                key={title}
                to={to}
                className={cx(
                  "group flex flex-col rounded-xl border p-5 transition-colors duration-150 motion-reduce:transition-none",
                  CONSOLE.panel,
                  CONSOLE.panelHover,
                  focusRing
                )}
              >
                <span className={cx("grid h-9 w-9 shrink-0 place-items-center rounded-lg", tile)} aria-hidden="true">
                  <Icon className="text-[17px]" />
                </span>
                <h3 className={cx("mt-4 text-[15px] font-semibold tracking-tight", CONSOLE.heading)}>{title}</h3>
                <p className={cx("mt-1.5 flex-1 text-[13px] leading-[20px]", CONSOLE.muted)}>{desc}</p>
                <span className={cx("mt-4 inline-flex items-center gap-0.5 text-[12px] font-semibold", CONSOLE.link)}>
                  {crumb}
                  <FiChevronRight
                    className="text-[13px] transition-transform duration-150 group-hover:translate-x-0.5 motion-reduce:transition-none"
                    aria-hidden="true"
                  />
                </span>
              </Link>
            ))}
          </div>
        )}
      </section>

      {/* ── Platform posture ───────────────────────────────────────────────── */}
      <div className="grid gap-4 lg:grid-cols-2">
        <FootPanel eyebrow="Platform status">
          <StatRow
            label="All monitored services"
            value={<HealthDot status={health.status || "neutral"} label={health.status === "ok" ? "Operational" : health.label} />}
          />
          <StatRow
            label="Active incidents"
            value={data ? openIncidents : null}
            reason="Waiting for the first overview poll"
            tone={openIncidents ? "text-rose-600 dark:text-rose-400" : CONSOLE.heading}
          />
          <StatRow
            label="Last 90 days uptime"
            value={null}
            reason="The availability probe retains 30 days at most — no 90-day window to report"
          />
        </FootPanel>

        <FootPanel eyebrow="Version & updates">
          <StatRow label="Current API version" value={API_VERSION} mono />
          <StatRow
            label="SDK updates this week"
            value={null}
            reason="Needs an SDK release feed (not integrated)"
          />
          <StatRow
            label="Latest deprecation"
            value={LATEST_DEPRECATION}
            mono
            tone="text-amber-600 dark:text-amber-400"
          />
        </FootPanel>
      </div>

      {/* ── Footer ─────────────────────────────────────────────────────────── */}
      <footer className={cx("flex flex-col gap-3 border-t pt-5 sm:flex-row sm:items-center sm:justify-between", CONSOLE.divider)}>
        <p className={cx("text-[12px]", CONSOLE.faint)}>
          © {new Date().getFullYear()} Zoiko Group. All rights reserved.
        </p>
        {/* Only destinations that exist are links; the rest render as text, not 404s. */}
        <nav className="flex flex-wrap items-center gap-x-6 gap-y-2">
          {FOOTER_LINKS.map(([label, to]) =>
            to ? (
              <Link
                key={label}
                to={to}
                className={cx("rounded text-[12px]", CONSOLE.faint, "hover:text-slate-900 dark:hover:text-white", focusRing)}
              >
                {label}
              </Link>
            ) : (
              <span key={label} className={cx("text-[12px]", CONSOLE.faint)}>
                {label}
              </span>
            )
          )}
        </nav>
      </footer>
    </div>
  );
}
