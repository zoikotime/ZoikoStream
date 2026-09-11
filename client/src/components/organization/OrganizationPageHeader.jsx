import { cx } from "../../ui/tokens";

// Page-level header for every Organization Admin screen: title (+ optional subtitle)
// on the left, an actions slot (buttons, filters) on the right. Replaces the
// hand-rolled <h1>+<p> block that was duplicated across all 7 org pages.
//
//   <OrganizationPageHeader title="Events" subtitle="Manage your streams"
//     actions={<Button>New event</Button>} />
//
// `sticky` pins the header (with the actions) to the top on scroll — the brief's
// "sticky page actions". Off by default.
export default function OrganizationPageHeader({
  title,
  subtitle,
  actions,
  sticky = false,
  className = "",
}) {
  return (
    <div
      className={cx(
        "flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between",
        sticky &&
          "sticky top-0 z-20 -mx-4 border-b border-slate-200 bg-white/80 px-4 py-3 backdrop-blur sm:-mx-6 sm:px-6 dark:border-slate-800 dark:bg-slate-950/70",
        className
      )}
    >
      <div className="min-w-0">
        {/* Matched to the dashboard's welcome heading (26/30px bold) so a page title looks
            the same size whichever organization screen you are on. `type.h1` is 24px
            semibold and is still what the rest of the app uses — changing it here rather
            than in the token keeps this to the organization console. */}
        <h1 className="truncate text-[26px] font-bold leading-tight tracking-tight text-slate-900 sm:text-[30px] dark:text-white">
          {title}
        </h1>
        {subtitle && (
          <p className="mt-1.5 text-[14px] text-slate-500 dark:text-neutral-400">{subtitle}</p>
        )}
      </div>
      {actions && (
        <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>
      )}
    </div>
  );
}
