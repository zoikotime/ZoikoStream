// client/src/components/watch/WatchPanel.jsx
// Right rail of the attendee watch page: who is presenting, whether this browser can play
// the stream, and where to get help. Three sibling cards in one file — they share the
// panel chrome and none of them is reused anywhere else.
//
// Nothing here is hardcoded. The organizer comes from the organization record, the checks
// are live browser probes (hooks/usePlaybackCheck), and the help links come from the
// platform settings store. A link the super admin hasn't configured is not rendered.
import { FiCheck, FiX, FiMinus, FiAlertTriangle, FiArrowRight, FiExternalLink, FiRefreshCw } from "react-icons/fi";
import { cx } from "../../ui/tokens";
import { CHECK_STATE } from "../../hooks/usePlaybackCheck";

const panel =
  "rounded-2xl border border-slate-200 bg-white p-5 dark:border-white/10 dark:bg-white/[0.02]";
const eyebrow =
  "text-[11px] font-semibold uppercase tracking-wider text-slate-500 dark:text-neutral-500";

// ── presented by ──────────────────────────────────────────────────────────────

export function OrganizerCard({ organizer }) {
  if (!organizer) return null;
  const initials = (organizer.name || "?").trim().split(/\s+/).slice(0, 2).map((w) => w[0]).join("").toUpperCase();

  return (
    <section className={panel} aria-labelledby="organizer-heading">
      <h2 id="organizer-heading" className={eyebrow}>Presented by</h2>

      <div className="mt-4 flex items-center gap-3">
        {organizer.logo_url ? (
          <img
            src={organizer.logo_url}
            alt=""
            className="h-10 w-10 shrink-0 rounded-lg object-cover"
            loading="lazy"
          />
        ) : (
          // Initials tile rather than a stock placeholder image — it's derived from the
          // real org name, so it can't be mistaken for a logo the organizer uploaded.
          <span className="grid h-10 w-10 shrink-0 place-items-center rounded-lg bg-violet-600 text-sm font-bold text-white">
            {initials}
          </span>
        )}
        <div className="min-w-0">
          <p className="flex items-center gap-1.5 truncate text-sm font-semibold text-slate-900 dark:text-white">
            {organizer.name}
            {organizer.verified && (
              <FiCheck className="shrink-0 text-emerald-500 dark:text-emerald-400" title="Verified organizer" aria-label="Verified organizer" />
            )}
          </p>
          <p className="truncate text-xs text-slate-500 dark:text-neutral-500">
            {organizer.verified ? "Official organizer" : "Organizer"}
          </p>
        </div>
      </div>

      {organizer.description && (
        <p className="mt-4 text-[13px] leading-relaxed text-slate-600 dark:text-neutral-400">
          {organizer.description}
        </p>
      )}

      {(organizer.website || organizer.social_links?.length > 0) && (
        <div className="mt-4 flex flex-wrap gap-x-4 gap-y-2 text-xs">
          {organizer.website && (
            <a
              href={organizer.website}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 font-medium text-violet-600 hover:text-violet-500 dark:text-violet-400"
            >
              Website <FiExternalLink />
            </a>
          )}
          {organizer.social_links?.map((s) => (
            <a
              key={s.url}
              href={s.url}
              target="_blank"
              rel="noopener noreferrer"
              className="font-medium text-slate-500 hover:text-slate-700 dark:text-neutral-400 dark:hover:text-neutral-200"
            >
              {s.label}
            </a>
          ))}
        </div>
      )}
    </section>
  );
}

// ── before you watch ──────────────────────────────────────────────────────────

// One icon + tint per check state. `unknown` deliberately reads as neutral, not green:
// "this browser won't tell us" is not the same claim as "this works".
const MARK = {
  [CHECK_STATE.OK]: { Icon: FiCheck, cls: "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400" },
  [CHECK_STATE.WARN]: { Icon: FiAlertTriangle, cls: "bg-amber-500/15 text-amber-600 dark:text-amber-400" },
  [CHECK_STATE.FAIL]: { Icon: FiX, cls: "bg-rose-500/15 text-rose-600 dark:text-rose-400" },
  [CHECK_STATE.UNKNOWN]: { Icon: FiMinus, cls: "bg-slate-500/15 text-slate-500 dark:text-neutral-400" },
  [CHECK_STATE.CHECKING]: { Icon: FiRefreshCw, cls: "bg-slate-500/10 text-slate-400 dark:text-neutral-500" },
};

export function ReadinessCard({ checks, running, onRerun }) {
  return (
    <section className={panel} aria-labelledby="readiness-heading">
      <h2 id="readiness-heading" className={eyebrow}>Before you watch</h2>

      <ul className="mt-4 space-y-3">
        {checks.map(({ id, label, state, detail }) => {
          const { Icon, cls } = MARK[state] || MARK[CHECK_STATE.UNKNOWN];
          return (
            <li key={id} className="flex items-center gap-2.5 text-sm">
              <span className={cx("grid h-5 w-5 shrink-0 place-items-center rounded", cls)}>
                <Icon className={cx("text-xs", state === CHECK_STATE.CHECKING && "animate-spin motion-reduce:animate-none")} aria-hidden />
              </span>
              <span className="text-slate-700 dark:text-neutral-200">{label}</span>
              {detail && (
                <span className="ml-auto truncate text-xs text-slate-400 dark:text-neutral-500" title={detail}>
                  {detail}
                </span>
              )}
            </li>
          );
        })}
      </ul>

      <button
        onClick={onRerun}
        disabled={running}
        className={cx(
          "mt-5 w-full rounded-lg px-4 py-2.5 text-sm font-medium transition",
          "border border-slate-200 bg-slate-50 text-slate-700 hover:bg-slate-100",
          "dark:border-white/10 dark:bg-white/[0.04] dark:text-neutral-200 dark:hover:bg-white/[0.08]",
          "disabled:cursor-not-allowed disabled:opacity-60",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500"
        )}
      >
        {running ? "Checking…" : "Run check again"}
      </button>
    </section>
  );
}

// ── need help ─────────────────────────────────────────────────────────────────

// label -> [settings key, action text]. A key the super admin hasn't set is simply not
// rendered, so an unconfigured deployment shows a shorter card rather than dead links.
const HELP_ROWS = [
  ["Live chat", "live_chat_url", "Start chat"],
  ["Email support", "support_email", "Contact"],
  ["Help center", "help_center_url", "Browse"],
  ["FAQs", "faq_url", "Read"],
  ["Report an issue", "report_issue_url", "Report"],
];

// Privacy and terms share a row in the design ("Access & privacy details · Review"), so
// they're resolved separately rather than as another HELP_ROWS entry.
const LEGAL = ["privacy_url", "terms_url"];

export function SupportCard({ support, organizer }) {
  // Organizer support address wins over the platform's: an attendee with a problem about
  // THIS event should reach its organizer first.
  const email = organizer?.support_email || support?.support_email;

  const rows = HELP_ROWS.map(([label, key, action]) => {
    const value = key === "support_email" ? email : support?.[key];
    if (!value) return null;
    return { label, action, href: key === "support_email" ? `mailto:${value}` : value, external: key !== "support_email" };
  }).filter(Boolean);

  const legalHref = LEGAL.map((k) => support?.[k]).find(Boolean);
  if (!rows.length && !legalHref) return null;

  return (
    <section className={panel} aria-labelledby="support-heading">
      <h2 id="support-heading" className={eyebrow}>Need help?</h2>

      <ul className="mt-2 divide-y divide-slate-100 dark:divide-white/[0.07]">
        {rows.map(({ label, action, href, external }) => (
          <li key={label} className="flex items-center justify-between gap-3 py-2.5">
            <span className="text-sm text-slate-700 dark:text-neutral-300">{label}</span>
            <a
              href={href}
              {...(external ? { target: "_blank", rel: "noopener noreferrer" } : {})}
              className="inline-flex shrink-0 items-center gap-1 text-sm font-medium text-violet-600 transition hover:text-violet-500 dark:text-violet-400 dark:hover:text-violet-300"
            >
              {action} <FiArrowRight className="text-xs" />
            </a>
          </li>
        ))}
        {legalHref && (
          <li className="flex items-center justify-between gap-3 py-2.5">
            <span className="text-sm text-slate-700 dark:text-neutral-300">Access &amp; privacy details</span>
            <a
              href={legalHref}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex shrink-0 items-center gap-1 text-sm font-medium text-violet-600 transition hover:text-violet-500 dark:text-violet-400 dark:hover:text-violet-300"
            >
              Review <FiArrowRight className="text-xs" />
            </a>
          </li>
        )}
      </ul>
    </section>
  );
}
