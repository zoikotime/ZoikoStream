import { Link } from "react-router-dom";
import { FiAlertTriangle, FiChevronRight, FiInfo } from "react-icons/fi";
import { CONSOLE, cx, focusRing } from "../../../ui/tokens";
import { CARD } from "./consoleStyles";

// The organization console's dashboard vocabulary, matched to the ZST-ENG-DASH-001 frames
// (ORG-01..08, EVT-01..07): a KPI tile with its icon chip, a titled panel with an optional
// badge and action, an explanatory banner, an uppercase tag, and the navy feature card.
// Presentation only — every figure is passed in by a page that read it from the API, and a
// figure that was not measured is passed as null and rendered as "—" with its reason.

const CHIP = {
  violet: "bg-violet-50 text-violet-600 dark:bg-violet-500/15 dark:text-violet-300",
  pink: "bg-pink-50 text-pink-600 dark:bg-pink-500/15 dark:text-pink-300",
  sky: "bg-sky-50 text-sky-600 dark:bg-sky-500/15 dark:text-sky-300",
  amber: "bg-amber-50 text-amber-600 dark:bg-amber-500/15 dark:text-amber-300",
  green: "bg-emerald-50 text-emerald-600 dark:bg-emerald-500/15 dark:text-emerald-300",
  rose: "bg-rose-50 text-rose-600 dark:bg-rose-500/15 dark:text-rose-300",
  slate: "bg-slate-100 text-slate-500 dark:bg-white/[0.08] dark:text-neutral-300",
};


export function IconChip({ icon: Icon, tone = "violet", size = "md" }) {
  return (
    <span
      aria-hidden="true"
      className={cx(
        "grid shrink-0 place-items-center rounded-xl",
        size === "lg" ? "h-12 w-12 text-[20px]" : size === "sm" ? "h-8 w-8 text-[15px]" : size === "kpi" ? "h-9 w-9 text-[16px]" : "h-11 w-11 text-[18px]",
        CHIP[tone] || CHIP.violet
      )}
    >
      <Icon />
    </span>
  );
}

// A count under ten, set two-wide as the frames do ("05"). Only whole numbers: a measured
// "1.7 GB" or "98.1%" is shown exactly as given.
const twoWide = (value) => (/^\d$/.test(String(value)) ? `0${value}` : null);

// One KPI tile. `value === null` means unmeasured and renders "—"; `caption` says why.
export function KpiTile({ icon, label, value, caption, tone = "violet", to, emphasis = "number", className = "" }) {
  const missing = value === null || value === undefined;
  const padded = !missing && emphasis !== "text" ? twoWide(value) : null;
  const body = (
    <div className="flex items-start gap-3.5">
      {icon && <IconChip icon={icon} tone={tone} size="kpi" />}
      <div className="min-w-0">
        <p className={cx("text-[11.5px] font-medium", CONSOLE.muted)}>{label}</p>
        <p
          className={cx(
            "truncate font-bold tracking-tight tabular-nums",
            emphasis === "text" ? "mt-0.5 text-[18px] leading-7" : "text-[26px] leading-8",
            missing ? CONSOLE.faint : CONSOLE.heading
          )}
        >
          {missing ? "—" : padded ? (
            <>
              <span aria-hidden="true">{padded}</span>
              <span className="sr-only">{value}</span>
            </>
          ) : value}
        </p>
        {/* Captions, subtitles, row details and footnotes carry information, so they take the
            muted step (4.76:1 on white), not faint (2.63:1 — under WCAG AA at 11–12px). */}
        {caption && <p className={cx("text-[11px] leading-snug", CONSOLE.muted)}>{caption}</p>}
      </div>
    </div>
  );
  const shell = cx(CARD, "block px-[18px] py-4", className);
  if (!to) return <div className={shell} role="group" aria-label={label}>{body}</div>;
  return (
    <Link to={to} className={cx(shell, "transition-colors hover:border-violet-300 dark:hover:border-violet-400/40", focusRing)}>
      {body}
    </Link>
  );
}

export function KpiRow({ children, className = "" }) {
  return (
    <section aria-label="At a glance" className={cx("grid gap-4 sm:grid-cols-2 xl:grid-cols-4", className)}>
      {children}
    </section>
  );
}

const TAG = {
  violet: "border-violet-200 bg-violet-50 text-violet-700 dark:border-violet-400/30 dark:bg-violet-500/10 dark:text-violet-200",
  amber: "border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-400/30 dark:bg-amber-500/10 dark:text-amber-200",
  green: "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-400/30 dark:bg-emerald-500/10 dark:text-emerald-200",
  rose: "border-rose-200 bg-rose-50 text-rose-700 dark:border-rose-400/30 dark:bg-rose-500/10 dark:text-rose-200",
  sky: "border-sky-200 bg-sky-50 text-sky-700 dark:border-sky-400/30 dark:bg-sky-500/10 dark:text-sky-200",
  slate: "border-slate-200 bg-slate-50 text-slate-600 dark:border-white/15 dark:bg-white/[0.06] dark:text-neutral-300",
};

// Small uppercase tag: "SERVER-RESOLVED SCOPE", "GATED", "2 ITEMS". `plain` sets a status pill
// in sentence case instead ("Ready", "Verification required"), as the frames do for states.
export function Tag({ children, tone = "violet", className = "", plain = false }) {
  return (
    <span
      className={cx(
        "inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-md border px-2 py-0.5",
        plain ? "text-[11px] font-semibold" : "text-[10px] font-bold uppercase tracking-[0.08em]",
        TAG[tone] || TAG.violet,
        className
      )}
    >
      {children}
    </span>
  );
}

// A titled panel: title + subtitle on the left, a tag or an action link on the right.
export function Panel({ id, title, subtitle, eyebrow, tag, tagTone, action, children, className = "", bodyClass = "", flush = false }) {
  return (
    <section id={id} className={cx(CARD, "flex scroll-mt-24 flex-col", className)} aria-label={typeof title === "string" ? title : undefined}>
      {(title || action || tag) && (
        <header className={cx("flex items-start justify-between gap-3 px-5 pt-5", flush ? "pb-4" : "pb-3")}>
          <div className="min-w-0">
            {eyebrow && (
              <p className="text-[10px] font-bold uppercase tracking-[0.16em] text-violet-600 dark:text-violet-300">{eyebrow}</p>
            )}
            {title && <h2 className={cx("text-[14.5px] font-bold tracking-[-0.01em]", CONSOLE.heading)}>{title}</h2>}
            {subtitle && <p className={cx("mt-0.5 text-[12px]", CONSOLE.muted)}>{subtitle}</p>}
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {tag && <Tag tone={tagTone}>{tag}</Tag>}
            {action}
          </div>
        </header>
      )}
      <div className={cx(flush ? "" : "px-5 pb-5", "flex-1", bodyClass)}>{children}</div>
    </section>
  );
}

export function PanelLink({ to, children, external = false }) {
  return (
    <Link
      to={to}
      className={cx("inline-flex items-center gap-1 rounded text-[12px] font-semibold", CONSOLE.link, focusRing)}
      target={external ? "_blank" : undefined}
      rel={external ? "noreferrer" : undefined}
    >
      {children}
      <FiChevronRight aria-hidden="true" />
    </Link>
  );
}

const BANNER = {
  info: {
    box: "border-violet-200/80 bg-violet-50/70 dark:border-violet-400/25 dark:bg-violet-500/[0.08]",
    chip: "bg-white text-violet-600 dark:bg-white/10 dark:text-violet-300",
    title: "text-violet-900 dark:text-violet-100",
    icon: FiInfo,
  },
  warning: {
    box: "border-amber-200 bg-amber-50/80 dark:border-amber-400/30 dark:bg-amber-500/[0.08]",
    chip: "bg-white text-amber-600 dark:bg-white/10 dark:text-amber-300",
    title: "text-amber-900 dark:text-amber-100",
    icon: FiAlertTriangle,
  },
};

// The explanatory strip under a page header: what the numbers mean, or what blocks go-live.
export function Banner({ tone = "info", icon, title, children, aside, role }) {
  const t = BANNER[tone] || BANNER.info;
  const Icon = icon || t.icon;
  return (
    <div className={cx("flex flex-col gap-3 rounded-2xl border px-4 py-3.5 sm:flex-row sm:items-center", t.box)} role={role}>
      <span className={cx("grid h-9 w-9 shrink-0 place-items-center rounded-lg", t.chip)} aria-hidden="true">
        <Icon />
      </span>
      <div className="min-w-0 flex-1">
        {title && <p className={cx("text-[14px] font-semibold", t.title)}>{title}</p>}
        {/* Body copy, not muted: slate-500 on the tinted strip measures 4.42:1, under AA. */}
        {children && <div className={cx("text-[12.5px] leading-relaxed", CONSOLE.body)}>{children}</div>}
      </div>
      {aside && <div className="shrink-0">{aside}</div>}
    </div>
  );
}

// The navy feature card (ORG-01 next priority event, ORG-03 access governance, ORG-04 replay
// governance, the workspace previews).
export function DarkCard({ children, className = "", as: Tag_ = "section", ...rest }) {
  return (
    <Tag_
      className={cx(
        "relative overflow-hidden rounded-2xl bg-gradient-to-br from-[#121a3f] via-[#1b1f4d] to-[#2b1a52] p-6 text-white",
        "shadow-[0_10px_30px_-12px_rgba(30,27,75,0.6)]",
        className
      )}
      {...rest}
    >
      <span aria-hidden="true" className="pointer-events-none absolute -right-24 -top-24 h-72 w-72 rounded-full bg-fuchsia-500/20 blur-3xl" />
      <span aria-hidden="true" className="pointer-events-none absolute -bottom-28 right-24 h-64 w-64 rounded-full bg-violet-500/25 blur-3xl" />
      <div className="relative">{children}</div>
    </Tag_>
  );
}

// A list row used across the side panels: icon chip, title + detail, a trailing node.
export function ListRow({ icon, tone, title, detail, trailing, to }) {
  const inner = (
    <>
      {icon && <IconChip icon={icon} tone={tone} size="sm" />}
      <div className="min-w-0 flex-1">
        <p className={cx("truncate text-[13px] font-semibold", CONSOLE.heading)}>{title}</p>
        {detail && <p className={cx("truncate text-[12px]", CONSOLE.muted)}>{detail}</p>}
      </div>
      {trailing && <div className="shrink-0">{trailing}</div>}
    </>
  );
  return (
    <li className={cx("border-t first:border-t-0", CONSOLE.divider)}>
      {to ? (
        <Link to={to} className={cx("flex items-center gap-3 py-3 hover:bg-slate-50/60 dark:hover:bg-white/[0.03]", focusRing)}>{inner}</Link>
      ) : (
        <div className="flex items-center gap-3 py-3">{inner}</div>
      )}
    </li>
  );
}

// Tabular "label · value" facts in a side panel.
export function FactList({ rows }) {
  return (
    <dl className={cx("divide-y text-[13px]", CONSOLE.divideY)}>
      {rows.map(([label, value, tone]) => (
        <div key={label} className="flex items-center justify-between gap-4 py-2.5">
          <dt className={CONSOLE.muted}>{label}</dt>
          <dd className={cx("text-right font-semibold", tone === "warn" ? "text-amber-700 dark:text-amber-300" : CONSOLE.heading)}>
            {value}
          </dd>
        </div>
      ))}
    </dl>
  );
}

export function Footnote({ children }) {
  return <p className={cx("mt-1 flex items-start gap-1.5 text-[11.5px]", CONSOLE.muted)}><FiInfo aria-hidden="true" className="mt-0.5 shrink-0" />{children}</p>;
}
