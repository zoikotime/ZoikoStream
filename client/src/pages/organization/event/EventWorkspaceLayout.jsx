import { useMemo } from "react";
import { Link, useParams } from "react-router-dom";
import { FiArrowLeft, FiSearch } from "react-icons/fi";
import api from "../../../api";
import useApi from "../../../hooks/useApi";
import { PageSpinner } from "../../../ui/Spinner";
import { CONSOLE, cx, focusRing, panelSurface } from "../../../ui/tokens";
import OrganizationErrorState from "../../../components/organization/OrganizationErrorState";
import OrganizationLayout from "../../../layouts/OrganizationLayout";
import EventRail from "./EventRail";

// /organization/events/:id/* — the Event Workspace (ZST-UX-NAV-001 §6).
//
// The same organization shell, with the event rail in place of the organization rail. The
// event is resolved here, ONCE, and handed to whichever section is routed; until it resolves
// the section is not rendered at all, so no section ever has to cope with a missing event.
//
// Keyed by the event id: moving from one event to another remounts the workspace and re-reads
// everything, so nothing from the previous event — its readiness, its status — is ever shown
// against the next one (ZST-ENG-DASH-001 §6.3, "revalidate on event switch").
export default function EventWorkspaceLayout() {
  const { id } = useParams();
  return <Workspace key={id} id={id} />;
}

function NotFound() {
  // Non-disclosing: the same words whether the event never existed, was deleted, or belongs
  // to another organization — the API answers all three with the same 404.
  return (
    <div className={cx(panelSurface, "mx-auto mt-8 max-w-lg p-8 text-center")}>
      <span className="mx-auto grid h-11 w-11 place-items-center rounded-full bg-slate-100 text-slate-400 dark:bg-slate-800">
        <FiSearch aria-hidden="true" />
      </span>
      <h1 className={cx("mt-3 text-lg font-semibold", CONSOLE.heading)}>Event not found</h1>
      <p className={cx("mt-1 text-sm", CONSOLE.muted)}>
        It may have been deleted, or it isn&rsquo;t part of your organization.
      </p>
      <Link
        to="/organization/events"
        className={cx("mt-4 inline-flex items-center gap-1.5 text-sm font-semibold", CONSOLE.link, focusRing)}
      >
        <FiArrowLeft aria-hidden="true" /> All events
      </Link>
    </div>
  );
}

function Workspace({ id }) {
  const ev = useApi(() => api.get(`/events/${id}`).then((r) => r.data));
  // The verdict behind Preparation's blocker count and the Overview's readiness panel. A
  // failure here never blocks the workspace: the panels that need it say it is unavailable.
  const readiness = useApi(() => api.get(`/organization/events/${id}/readiness`).then((r) => r.data));

  const event = ev.data;
  const status = ev.error?.response?.status;
  const { reload: reloadEvent } = ev;
  const { reload: reloadReadiness } = readiness;

  const context = useMemo(
    () => ({
      event,
      eventId: id,
      reload: () => {
        reloadEvent();
        reloadReadiness();
      },
      readiness: readiness.data,
      readinessError: readiness.error,
      readinessLoading: readiness.loading && !readiness.data,
      reloadReadiness,
    }),
    [event, id, reloadEvent, reloadReadiness, readiness.data, readiness.error, readiness.loading]
  );

  // A reload keeps the event on screen; only the first load, or a failure with nothing to
  // show, replaces the section.
  let gate = null;
  if (!event) {
    if (status === 404 || status === 422) gate = <NotFound />;
    else if (ev.error) gate = <OrganizationErrorState error={ev.error} onRetry={reloadEvent} title="Couldn't load this event" />;
    else gate = <PageSpinner label="Loading event…" />;
  }

  return (
    <OrganizationLayout
      outletContext={context}
      content={gate}
      renderNav={({ state, collapsed, onNavigate }) => (
        <EventRail
          event={event}
          eventId={id}
          readiness={readiness.data}
          state={state}
          collapsed={collapsed}
          onNavigate={onNavigate}
        />
      )}
    />
  );
}
