import { FiActivity, FiDownload, FiSearch } from "react-icons/fi";
import ActivityLedger from "../../../components/organization/ActivityLedger";
import { ConsoleButton as Button } from "../../../ui/Button";
import EventSectionHeader from "./EventSectionHeader";
import { useEventWorkspace, useHashScroll } from "./workspace";

// EVT-07 Activity & Audit (EVT-07 frames): this event's ledger — management changes, lifecycle
// moves, live console actions, access-link changes, refusals and evidence actions — scoped by
// the server to this event of this organization. Times are shown in the event's own zone and
// in UTC.
export default function EventActivity() {
  const { event, eventId } = useEventWorkspace();
  useHashScroll();
  return (
    <div id="timeline" data-section="timeline" className="scroll-mt-24 space-y-6">
      <EventSectionHeader
        icon={FiActivity}
        eyebrow="Event workspace / 07"
        title="Activity & Audit"
        subtitle="Event actions, authorization outcomes and traceable request references."
        actions={
          <>
            <Button variant="secondary" size="sm" leftIcon={FiSearch} onClick={() => document.getElementById("activity-search")?.focus()}>
              Search activity
            </Button>
            <Button variant="secondary" size="sm" leftIcon={FiDownload} disabled title="Audit evidence export is not offered yet">
              Request evidence export · Gated
            </Button>
          </>
        }
      />
      <ActivityLedger
        listUrl={`/organization/events/${eventId}/activity`}
        detailUrl={(entryId) => `/organization/events/${eventId}/activity/${entryId}`}
        timezone={event.timezone}
        exportInHeader
        emptyText="Actions taken on this event — edits, status changes, access links, live controls — will be recorded here. Nothing is shown that did not happen."
      />
    </div>
  );
}
