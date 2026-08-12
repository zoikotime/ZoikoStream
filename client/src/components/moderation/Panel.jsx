// client/src/components/moderation/Panel.jsx
// Shared panel shell + compact action button for the live-event utility panel, so every
// surface (participants, chat, Q&A, polls, announcements, feed) looks identical.
//
// Used by BOTH consoles: the host console composes these as tabs (components/host/HostPanel)
// and the moderator console as side-by-side columns (pages/moderator/Dashboard). One
// implementation, two layouts — so the design tokens below live here rather than in either
// page, and changing a border or a count badge changes it in exactly one place.
//
// COLOUR NOTE: index.css remaps `emerald-*` to violet, so `bg-emerald-600` renders PURPLE.
// Everything here names the colour it actually wants — `violet-*` for the ZoikoStream
// accent, `green-*` for genuinely-green positive states.
import { cx, focusRing } from "../../ui/tokens";
import { PANEL } from "./panelTokens";

const TONE = {
  // `violet` is the accent; `emerald` is kept as an alias because callers across both
  // consoles already pass it and it resolves to violet anyway after the index.css remap.
  violet: "text-violet-600 hover:bg-violet-50 dark:text-violet-400 dark:hover:bg-violet-500/10",
  emerald: "text-violet-600 hover:bg-violet-50 dark:text-violet-400 dark:hover:bg-violet-500/10",
  green: "text-green-600 hover:bg-green-50 dark:text-green-400 dark:hover:bg-green-500/10",
  blue: "text-blue-600 hover:bg-blue-50 dark:text-blue-400 dark:hover:bg-blue-500/10",
  amber: "text-amber-600 hover:bg-amber-50 dark:text-amber-400 dark:hover:bg-amber-500/10",
  rose: "text-rose-600 hover:bg-rose-50 dark:text-rose-400 dark:hover:bg-rose-500/10",
  slate: "text-slate-500 hover:bg-slate-100 hover:text-slate-700 dark:text-slate-400 dark:hover:bg-slate-800 dark:hover:text-slate-200",
};

// Icon (+ optional lg-only label) button used for every moderation action. Always carries an
// accessible name, since most instances render as icon-only.
export function ActionButton({ icon: Icon, label, title, tone = "slate", active = false, onClick, disabled = false }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={title || label}
      aria-label={title || label}
      aria-pressed={active}
      className={cx(
        "inline-flex h-7 shrink-0 items-center gap-1 rounded-lg px-1 text-[11px] font-medium @lg:px-1.5",
        active
          ? "bg-violet-100 text-violet-700 dark:bg-violet-500/20 dark:text-violet-300"
          : TONE[tone] || TONE.slate,
        "disabled:cursor-not-allowed disabled:opacity-45",
        PANEL.t150,
        focusRing
      )}
    >
      <Icon className="shrink-0 text-sm" aria-hidden="true" />
      {/* Container query, not `lg:`. These panels render at ~380px in the host console's
          rail and at ~700px in the moderator console's centre column, from the SAME viewport
          — so a viewport breakpoint showed labels in both and wrapped the action row into
          three lines in the rail. `@lg` (32rem) is the panel's own inline size. Requires an
          ancestor marked `@container`; Panel does that below, and the two consoles mark their
          tab bodies. */}
      {label && <span className="hidden @lg:inline">{label}</span>}
    </button>
  );
}

// `scroll` (default) makes the body a flex-1 internal scroll area — right for a
// column that fills its height. Pass scroll={false} for cards inside a scrolling column.
// `toolbar` is an optional row pinned between the header and the scroll area — search /
// filter controls belong there, not inside the body, where they'd scroll out of reach.
export default function Panel({ title, count, badge, action, toolbar, scroll = true, className = "", children }) {
  return (
    <section className={cx("@container flex min-h-0 flex-col overflow-hidden", PANEL.surface, className)}>
      <div className={cx("flex shrink-0 items-center justify-between gap-2 border-b px-3 py-2.5", PANEL.divider)}>
        <div className="flex min-w-0 items-center gap-2">
          {/* The truncating text sits in a <span>, not directly on the <h2>. index.css declares
              `h1,h2,h3 { text-wrap: balance }` and `p { text-wrap: pretty }` OUTSIDE any
              @layer, and unlayered CSS outranks @layer utilities regardless of specificity —
              so Tailwind's `truncate` loses its white-space:nowrap on those elements and the
              text wraps and then gets clipped. A span is not targeted by those selectors. */}
          <h2 className={cx("min-w-0 text-[13px] font-semibold", PANEL.heading)}>
            <span className="block truncate">{title}</span>
          </h2>
          {count != null && <span className={PANEL.count}>{count}</span>}
          {badge}
        </div>
        {action && <div className="flex shrink-0 items-center gap-0.5">{action}</div>}
      </div>
      {toolbar && (
        <div className={cx("shrink-0 space-y-2 border-b px-3 py-2", PANEL.divider)}>{toolbar}</div>
      )}
      <div className={cx("p-2.5", scroll && "flex-1 overflow-y-auto")}>{children}</div>
    </section>
  );
}
