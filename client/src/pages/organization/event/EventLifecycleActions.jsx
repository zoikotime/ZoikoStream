import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  FiArchive, FiLink, FiPhoneOff, FiShield, FiTrash2, FiUploadCloud, FiXCircle,
} from "react-icons/fi";
import api, { errMsg } from "../../../api";
import { notify } from "../../../ui/Toast";
import { ConsoleButton as Button } from "../../../ui/Button";
import ConfirmDialog from "../../../ui/ConfirmDialog";
import {
  ARCHIVABLE_STATUSES, ON_AIR_STATUSES, PRE_LIVE_STATUSES, displayStatusOf, statusMeta,
} from "../../../data/events";
import { copyViewerLink } from "../../../utils/viewerLink";

// The event's lifecycle moves, as an ActionGuard (ZST-ENG-DASH-001 §3.1): only the moves the
// backend's transition table allows from the current status are offered (server/app/crud/
// event.py EVENT_TRANSITIONS — the server re-checks every one), high-impact moves ask for
// explicit intent first, and a second click while one is in flight does nothing.
//
// The in-flight guard is a ref, not the `busy` state: two clicks inside one frame both read
// `busy === false` before React re-renders, and both would have been sent.
//
// A success says what the SERVER returned, with its request reference, so it can be found in
// Activity & Audit; a refusal shows the server's own reason (for Arm, the readiness gate's
// blocker list) and the receipt of that refusal is in the ledger too.

const reference = (res) => res?.headers?.["x-request-id"] || null;

export default function EventLifecycleActions({ event, onChanged, onBeforeConfirm, only = null }) {
  const navigate = useNavigate();
  const inFlight = useRef(false);
  const [busy, setBusy] = useState(null); // key of the action in flight
  const [confirm, setConfirm] = useState(null);

  // `only` narrows the set for a section that owns one move (Live Control offers End alone).
  const show = (key) => !only || only.includes(key);
  const name = event.title || "this event";
  const st = statusMeta(displayStatusOf(event));
  const startsInFuture = Boolean(event.start_time) && new Date(event.start_time) > new Date();

  async function run(key, request, success, after) {
    if (inFlight.current) return false;
    inFlight.current = true;
    setBusy(key);
    try {
      const res = await request();
      const ref = reference(res);
      notify.success(ref ? `${success} · Ref ${ref}` : success);
      setConfirm(null);
      (after || onChanged)?.();
      return true;
    } catch (e) {
      notify.error(errMsg(e));
      setConfirm(null);
      onChanged?.();
      return false;
    } finally {
      inFlight.current = false;
      setBusy(null);
    }
  }

  const setStatus = (key, status, success) =>
    run(key, () => api.patch(`/events/${event.id}`, { status }), success);

  // Ask first, and re-read the event while the question is on screen: the decision is made
  // against the event as it is now, not as it was when this page loaded.
  const ask = (spec) => {
    onBeforeConfirm?.();
    setConfirm(spec);
  };

  const canPublish = show("publish") && event.status === "draft";
  const publishTarget = startsInFuture ? "scheduled" : "published";
  const canEnd = show("end") && ON_AIR_STATUSES.includes(event.status);
  const canCancel = show("cancel") && PRE_LIVE_STATUSES.includes(event.status);
  const canArchive = show("archive") && ARCHIVABLE_STATUSES.includes(event.status);
  const canUnarchive = show("archive") && event.status === "archived";

  const ARM_STEP = {
    published: { label: "Mark Ready to Arm", next: "ready_to_arm", toast: "Marked ready to arm" },
    scheduled: { label: "Mark Ready to Arm", next: "ready_to_arm", toast: "Marked ready to arm" },
    ready_to_arm: { label: "Arm Event", next: "armed", toast: "Event armed" },
  };
  const BACK_STEP = {
    ready_to_arm: { label: "Mark Not Ready", next: startsInFuture ? "scheduled" : "published" },
    armed: { label: "Disarm", next: "ready_to_arm" },
  };
  const armStep = show("arm") ? ARM_STEP[event.status] : null;
  const backStep = show("arm") ? BACK_STEP[event.status] : null;

  const copyLink = async () => {
    if (await copyViewerLink(event)) notify.success("Viewer link copied");
    else notify.error("Unable to copy viewer link.");
  };

  const disabled = busy !== null;

  return (
    <>
      <div className="flex flex-wrap gap-2">
        {canPublish && (
          <Button size="sm" leftIcon={FiUploadCloud} loading={busy === "publish"} disabled={disabled}
            onClick={() => setStatus("publish", publishTarget, publishTarget === "scheduled" ? "Event scheduled" : "Event published")}>
            Publish
          </Button>
        )}
        {armStep && (
          <Button variant="secondary" size="sm" leftIcon={FiShield} loading={busy === "arm"} disabled={disabled}
            onClick={() =>
              armStep.next === "armed"
                ? ask({
                    key: "arm", tone: "primary", title: "Arm this event?", confirmLabel: "Yes, arm it",
                    body: (
                      <>
                        Arming checks the event&rsquo;s readiness gate on the server. If a required check is
                        failing it will be refused and nothing changes. <strong>{name}</strong> is
                        currently <strong>{st.label}</strong>.
                      </>
                    ),
                    exec: () => setStatus("arm", "armed", armStep.toast),
                  })
                : setStatus("arm", armStep.next, armStep.toast)
            }>
            {armStep.label}
          </Button>
        )}
        {backStep && (
          <Button variant="secondary" size="sm" loading={busy === "back"} disabled={disabled}
            onClick={() => setStatus("back", backStep.next, "Event updated")}>
            {backStep.label}
          </Button>
        )}
        {canEnd && (
          <Button variant="danger" size="sm" leftIcon={FiPhoneOff} loading={busy === "end"} disabled={disabled}
            onClick={() =>
              ask({
                key: "end", title: "End this event now?", confirmLabel: "Yes, end it now",
                body: (
                  <>
                    <strong>{name}</strong> is <strong>{st.label}</strong>. Ending it stops the broadcast and
                    any recording, and disconnects every viewer immediately. This cannot be undone.
                  </>
                ),
                exec: () => run("end", () => api.post(`/events/${event.id}/end`), "Event ended"),
              })
            }>
            End Event
          </Button>
        )}
        {canCancel && (
          <Button variant="secondary" size="sm" leftIcon={FiXCircle} loading={busy === "cancel"} disabled={disabled}
            onClick={() =>
              ask({
                key: "cancel", title: "Cancel this event?", confirmLabel: "Yes, cancel it",
                body: <>Cancel <strong>{name}</strong>? It can&rsquo;t go live afterwards, and new registrations are refused.</>,
                exec: () => setStatus("cancel", "cancelled", "Event cancelled"),
              })
            }>
            Cancel Event
          </Button>
        )}
        {canArchive && (
          <Button variant="secondary" size="sm" leftIcon={FiArchive} loading={busy === "archive"} disabled={disabled}
            onClick={() => run("archive", () => api.post(`/events/${event.id}/archive`), "Event archived")}>
            Archive
          </Button>
        )}
        {canUnarchive && (
          <Button variant="secondary" size="sm" leftIcon={FiArchive} loading={busy === "unarchive"} disabled={disabled}
            onClick={() => run("unarchive", () => api.post(`/events/${event.id}/unarchive`), "Event restored")}>
            Unarchive
          </Button>
        )}
        {show("link") && (
          <Button variant="secondary" size="sm" leftIcon={FiLink} onClick={copyLink}>Copy Viewer Link</Button>
        )}
        {show("delete") && (
        <Button variant="danger" size="sm" leftIcon={FiTrash2} loading={busy === "delete"} disabled={disabled}
          onClick={() =>
            ask({
              key: "delete", title: "Delete this event?", confirmLabel: "Yes, delete it",
              body: <>Delete <strong>{name}</strong>? It is removed from every list. This cannot be undone.</>,
              exec: () =>
                run("delete", () => api.delete(`/events/${event.id}`), "Event deleted", () => navigate("/organization/events")),
            })
          }>
          Delete
        </Button>
        )}
      </div>

      <ConfirmDialog
        open={Boolean(confirm)}
        onClose={() => setConfirm(null)}
        onConfirm={() => confirm?.exec()}
        title={confirm?.title}
        body={confirm?.body}
        confirmLabel={confirm?.confirmLabel}
        tone={confirm?.tone || "danger"}
        busy={busy !== null && busy === confirm?.key}
      />
    </>
  );
}
