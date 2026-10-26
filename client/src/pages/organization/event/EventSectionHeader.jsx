import { Fragment } from "react";
import { Link } from "react-router-dom";
import { FiChevronRight } from "react-icons/fi";
import { CONSOLE, cx, focusRing } from "../../../ui/tokens";
import { ON_AIR_STATUSES, displayStatusOf, statusMeta, visLabel } from "../../../data/events";
import { useOrgScope } from "../../../components/organization/orgScope";
import { CARD } from "../../../components/organization/console/consoleStyles";
import { sectionPath, useEventWorkspace } from "./workspace";
import { dateBlock, fmtZoned } from "../../../data/eventTime";
import AskGuideButton from "../../../operator-guide/AskGuideButton";

// The context every Event Workspace section opens with (ZST-UX-NAV-001 §3.1; EVT-02..07
// frames): where you are (Organization › All Events › event › section), WHICH event — its
// type, organization and reference — its status, and its start in its own zone AND in UTC, so
// a live operation is never ambiguous about which event or which clock.
//
// `overview` is the EVT-01 form: the event's own name is the title, with its status beside it.

function Breadcrumb({ items }) {
  return (
    <nav aria-label="Breadcrumb">
      <ol className="flex flex-wrap items-center gap-1.5 text-[12.5px] text-slate-500 dark:text-neutral-400">
        {items.map(([label, to], i) => (
          <Fragment key={`${label}-${i}`}>
            {i > 0 && <li aria-hidden="true"><FiChevronRight /></li>}
            <li
              aria-current={i === items.length - 1 ? "page" : undefined}
              className={cx("max-w-[18rem] truncate", i === items.length - 1 && "font-medium text-violet-600 dark:text-violet-300")}
            >
              {to ? <Link to={to} className={cx("rounded hover:underline", focusRing)}>{label}</Link> : label}
            </li>
          </Fragment>
        ))}
      </ol>
    </nav>
  );
}

function StatusTag({ event }) {
  const st = statusMeta(displayStatusOf(event));
  const onAir = ON_AIR_STATUSES.includes(event.status);
  return (
    <span
      className={cx(
        "inline-flex items-center gap-1.5 whitespace-nowrap rounded-lg px-2.5 py-1.5 text-[11px] font-bold uppercase tracking-[0.06em]",
        onAir
          ? "bg-rose-50 text-rose-700 ring-1 ring-inset ring-rose-200 dark:bg-rose-500/15 dark:text-rose-200 dark:ring-rose-400/30"
          : "bg-violet-50 text-violet-700 ring-1 ring-inset ring-violet-200 dark:bg-violet-500/15 dark:text-violet-200 dark:ring-violet-400/30"
      )}
    >
      {onAir && <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-rose-500 motion-safe:animate-pulse" />}
      {st.label} · {onAir ? "Live" : "Not live"}
    </span>
  );
}

export default function EventSectionHeader({ title, subtitle, actions, eyebrow, icon: Icon, overview = false }) {
  const { event } = useEventWorkspace();
  const { state } = useOrgScope();
  const name = event.title || "Untitled event";
  const orgName = state?.organization?.name;
  const block = dateBlock(event.start_time, event.timezone);
  const ref = String(event.id).slice(0, 8).toUpperCase();

  if (overview) {
    const st = statusMeta(displayStatusOf(event));
    return (
      <header className="space-y-2">
        <Breadcrumb items={[[orgName || "Organization", "/organization/manage"], ["Events", "/organization/events"], [name]]} />
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="min-w-0">
            <p className="mb-1 text-[11.5px] font-bold uppercase tracking-[0.16em] text-violet-600 dark:text-violet-300">
              Event workspace / Event overview
            </p>
            <div className="flex flex-wrap items-center gap-3">
              <h1 className={cx("truncate text-[26px] font-bold leading-tight tracking-tight sm:text-[30px]", CONSOLE.heading)}>{name}</h1>
              <span className="rounded-md bg-violet-50 px-2.5 py-1 text-[11px] font-bold uppercase tracking-[0.08em] text-violet-700 ring-1 ring-inset ring-violet-200 dark:bg-violet-500/15 dark:text-violet-200 dark:ring-violet-400/30">
                {st.label}
              </span>
            </div>
            <p className={cx("mt-1.5 text-[14px]", CONSOLE.muted)}>
              {event.start_time ? fmtZoned(event.start_time, event.timezone) : "No start time set"} · All actions scoped to this event
            </p>
          </div>
          <div className="flex shrink-0 flex-wrap items-center gap-2">
            <AskGuideButton question="Is this event ready?" eventId={event.id} />
            {actions}
          </div>
        </div>
      </header>
    );
  }

  return (
    <header className="space-y-4">
      <Breadcrumb
        items={[
          [orgName || "Organization", "/organization/manage"],
          ["All Events", "/organization/events"],
          [name, sectionPath(event.id)],
          [title],
        ]}
      />

      <div className={cx(CARD, "flex flex-col gap-4 p-4 sm:flex-row sm:items-center")}>
        <div className="flex min-w-0 flex-1 items-center gap-4">
          {Icon && (
            <span aria-hidden="true" className="grid h-12 w-12 shrink-0 place-items-center rounded-xl bg-gradient-to-br from-violet-500 to-fuchsia-500 text-[22px] text-white shadow-[0_8px_18px_-8px_rgba(168,85,247,0.8)]">
              <Icon />
            </span>
          )}
          <div className="min-w-0">
            <p className={cx("truncate text-[16px] font-semibold", CONSOLE.heading)}>{name}</p>
            <p className={cx("truncate text-[12.5px]", CONSOLE.faint)}>
              {visLabel(event.visibility)} event{orgName ? ` · ${orgName}` : ""} · Event reference: EVT-{ref}
            </p>
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-4">
          <StatusTag event={event} />
          <div className={cx("border-l pl-4 text-right", CONSOLE.divider)}>
            {block ? (
              <>
                <p className={cx("text-[13px] font-semibold", CONSOLE.heading)}>{block.date}</p>
                <p className={cx("text-[11.5px]", CONSOLE.muted)}>{block.local}</p>
                <p className={cx("text-[11.5px]", CONSOLE.faint)}>{block.utc}</p>
              </>
            ) : (
              <p className={cx("text-[12px]", CONSOLE.faint)}>No start time set</p>
            )}
          </div>
        </div>
      </div>

      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div className="min-w-0">
          {eyebrow && (
            <p className="mb-1 text-[11.5px] font-bold uppercase tracking-[0.16em] text-violet-600 dark:text-violet-300">{eyebrow}</p>
          )}
          <h1 className={cx("truncate text-[26px] font-bold leading-tight tracking-tight sm:text-[30px]", CONSOLE.heading)}>{title}</h1>
          {subtitle && <p className={cx("mt-1.5 text-[14px]", CONSOLE.muted)}>{subtitle}</p>}
        </div>
        {actions && <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>}
      </div>
    </header>
  );
}
