import { Link, useLocation } from "react-router-dom";
import { FiArrowLeft, FiCalendar, FiChevronRight, FiEye, FiLifeBuoy } from "react-icons/fi";
import { cx, focusRing } from "../../../ui/tokens";
import { ON_AIR_STATUSES, displayStatusOf, statusMeta, visLabel } from "../../../data/events";
import { RailChildren, RailLink, RailSection } from "../../../components/Dashboard/Sidebar";
import { SECTIONS, sectionPath } from "./workspace";

// The event-local rail (ZST-UX-NAV-001 §6, Figure C; ZST-ENG-DASH-001 EVT-01..07):
//
//   ← Back to All Events
//   the event: organization · EVENT WORKSPACE, title, date in its own zone, status
//   EVENT WORKSPACE — the seven sections; the open one lists its parts
//   Viewer preview · Event support
//
// It replaces the organization rail inside the Sidebar rather than sitting beside it, so the
// shell (brand, identity, service status) is unchanged and nothing here duplicates the org
// navigation. Section links never act: following one only reads.

const sectionFor = (pathname, eventId) => {
  const base = sectionPath(eventId);
  const rest = pathname.startsWith(base) ? pathname.slice(base.length).replace(/^\//, "") : "";
  const slug = rest.split("/")[0];
  return SECTIONS.find((s) => s.slug === slug)?.id || "event.overview";
};

const dateLine = (event) => {
  if (!event?.start_time) return "Date not set";
  const d = new Date(event.start_time);
  let zone;
  try {
    zone = event.timezone ? new Intl.DateTimeFormat("en-GB", { timeZone: event.timezone }).resolvedOptions().timeZone : undefined;
  } catch {
    zone = undefined;
  }
  const date = d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: zone });
  return `${date} · ${event.timezone || "local time"}`;
};

// The status chip under the title: the event's status, and whether it is on air — the one
// question the frames make unmissable ("PREPARING · NOT LIVE").
function StatusChip({ event }) {
  const st = statusMeta(displayStatusOf(event));
  const onAir = ON_AIR_STATUSES.includes(event.status);
  return (
    <span
      className={cx(
        "inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-[10.5px] font-bold uppercase tracking-[0.06em]",
        onAir ? "border-rose-300/40 bg-rose-500/20 text-rose-100" : "border-violet-300/30 bg-violet-500/20 text-violet-100"
      )}
    >
      <span aria-hidden="true" className={cx("h-1.5 w-1.5 rounded-full", onAir ? "bg-rose-400 motion-safe:animate-pulse" : "bg-violet-300")} />
      {st.label}
      {onAir ? " · Live" : " · Not live"}
    </span>
  );
}

export default function EventRail({ event, eventId, readiness, state, collapsed, onNavigate }) {
  const { pathname, hash, search } = useLocation();
  const active = sectionFor(pathname, eventId);
  const onAir = event && ON_AIR_STATUSES.includes(event.status);
  // Blockers are the REQUIRED gates that fail — the same rule the readiness verdict uses.
  const blockers = (readiness?.gates || []).filter((g) => g.required && !g.passed).length;

  const signal = {
    "event.prep": { count: blockers },
    "event.access": { hint: event ? visLabel(event.visibility) : null },
    "event.live": { hint: onAir ? "Live" : null },
  };
  const params = new URLSearchParams(search);

  return (
    <nav
      aria-label="Event workspace"
      className={cx(
        "zk-scroll-thin mt-3 flex flex-1 flex-col overflow-y-auto overflow-x-hidden pb-3",
        collapsed ? "px-3 lg:px-2.5" : "px-3"
      )}
    >
      <Link
        to="/organization/events"
        onClick={onNavigate}
        aria-label={collapsed ? "All events" : undefined}
        className={cx(
          "group relative mb-3 flex items-center gap-2 rounded-lg px-3 py-2 text-[13px] font-semibold text-white/85 hover:bg-white/[0.07] hover:text-white",
          focusRing,
          collapsed && "lg:justify-center lg:px-0"
        )}
      >
        <FiArrowLeft aria-hidden="true" />
        <span className={cx("flex-1", collapsed && "lg:hidden")}>All events</span>
        {/* Where the link goes back to — a visual label, kept out of the link's name. */}
        <span aria-hidden="true" className={cx("text-[10px] font-semibold uppercase tracking-[0.14em] text-white/55", collapsed && "lg:hidden")}>
          Organization
        </span>
      </Link>

      <div className={cx("mb-4 rounded-xl border border-white/10 bg-white/[0.05] p-3.5", collapsed && "lg:hidden")}>
        <p className="text-[10px] font-semibold uppercase leading-snug tracking-[0.16em] text-white/50">
          {state?.organization?.name ? `${state.organization.name} · Event workspace` : "Event workspace"}
        </p>
        {event ? (
          <>
            <p className="mt-1.5 line-clamp-2 text-[16px] font-bold leading-snug text-white">{event.title || "Untitled event"}</p>
            <p className="mt-1.5 flex items-center gap-1.5 text-[11.5px] text-white/60">
              <FiCalendar aria-hidden="true" /> {dateLine(event)}
            </p>
            <div className="mt-2.5">
              <StatusChip event={event} />
            </div>
          </>
        ) : (
          <div aria-hidden="true" className="mt-2 space-y-2">
            <div className="zk-skeleton h-4 w-40 rounded bg-white/10" />
            <div className="zk-skeleton h-3 w-24 rounded bg-white/10" />
          </div>
        )}
      </div>

      <RailSection collapsed={collapsed}>Event workspace</RailSection>
      <ul className="space-y-1">
        {SECTIONS.map((s) => {
          const on = active === s.id;
          const children = on && !collapsed && s.children ? s.children : null;
          // Which part of the open section the URL points at: a hash, a filter, or the top.
          const childCurrent = (c) => {
            if (c.hash) return hash === `#${c.hash}` || (!hash && c.hash === "overview");
            if (c.query) {
              const [k, v] = c.query.split("=");
              return params.get(k) === v;
            }
            return !hash && !(s.children || []).some((o) => o.query && params.get(o.query.split("=")[0]));
          };
          return (
            <li key={s.id}>
              <div className="flex">
                <RailLink
                  to={sectionPath(eventId, s.slug)}
                  label={s.label}
                  icon={s.icon}
                  active={on}
                  current={on}
                  count={signal[s.id]?.count || 0}
                  countTone="warn"
                  hint={signal[s.id]?.hint}
                  collapsed={collapsed}
                  onClick={onNavigate}
                />
              </div>
              {children && (
                <RailChildren
                  id={`rail-${s.slug || "overview"}`}
                  items={children.map((c) => ({
                    key: c.key,
                    label: c.label,
                    gated: c.gated,
                    to: `${sectionPath(eventId, s.slug)}${c.query ? `?${c.query}` : ""}${c.hash ? `#${c.hash}` : ""}`,
                    current: childCurrent(c),
                    onClick: onNavigate,
                  }))}
                />
              )}
            </li>
          );
        })}
      </ul>

      <div className="mt-auto space-y-1 border-t border-white/[0.07] pt-3">
        {/* Preview, not open: the console never navigates to the attendee page itself — the
            Audience Access section explains what a viewer sees and holds the link. */}
        <RailLink
          to={`${sectionPath(eventId, "access")}#viewer-preview`}
          label="Viewer preview"
          icon={FiEye}
          collapsed={collapsed}
          onClick={onNavigate}
        />
        <RailLink
          to="/organization/support"
          label="Event support"
          icon={FiLifeBuoy}
          collapsed={collapsed}
          onClick={onNavigate}
          chevron={<FiChevronRight aria-hidden="true" className={cx("shrink-0 text-white/50", collapsed && "lg:hidden")} />}
        />
      </div>
    </nav>
  );
}
