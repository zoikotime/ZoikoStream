import { useMemo, useState } from "react";
import { FiCopy, FiLink, FiMail, FiSend, FiUsers } from "react-icons/fi";
import api from "../../api";
import useApi from "../../hooks/useApi";
import useMutation from "../../hooks/useMutation";
import Modal from "../../ui/Modal";
import Badge from "../../ui/Badge";
import { ConsoleButton as Button } from "../../ui/Button";
import { Input, Label, Select, Switch, Textarea, Note } from "../../ui/forms";
import { notify } from "../../ui/Toast";
import { cx, focusRing } from "../../ui/tokens";
import { EVENT_ROLES, PLATFORM_ROLES } from "../../data/invitations";

// Send invitations. One dialog covers all five requested shapes:
//   existing user / new user  — the SERVER decides which, from the address (the invitee sees
//                               a password form or a "sign in" prompt accordingly)
//   single / bulk             — one address or a pasted list
//   email / manual            — "Send email" off creates the row and hands you the link
//
// The event fields are hidden entirely when the dialog is opened from an event page, because
// the event is already decided there.

const isEmail = (v) => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v.trim());

/** Split a pasted blob on commas, semicolons, whitespace and newlines. Admins paste from a
 *  spreadsheet or a mail client; insisting on one separator just makes them edit it. */
function parseEmails(raw) {
  const parts = String(raw).split(/[\s,;]+/).map((s) => s.trim()).filter(Boolean);
  return [...new Set(parts.map((s) => s.toLowerCase()))];
}

/** The one-time link reveal. Only the hash is stored server-side, so this is the single moment
 *  the raw link exists — the same discipline as event access links. */
function LinkReveal({ invitation, onClose }) {
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(invitation.invite_url);
      notify.success("Invitation link copied");
    } catch {
      notify.error("Couldn't copy — select the link and copy it manually");
    }
  };
  return (
    <Modal
      open={!!invitation}
      onClose={onClose}
      title="Invitation link"
      size="lg"
      footer={
        <>
          <Button variant="secondary" size="sm" onClick={onClose}>Done</Button>
          <Button size="sm" leftIcon={FiCopy} onClick={copy}>Copy link</Button>
        </>
      }
    >
      <p>
        Send this link to <strong className="font-semibold text-slate-800 dark:text-slate-100">{invitation?.email}</strong>.
        It is shown <strong className="font-semibold text-slate-800 dark:text-slate-100">only now</strong> — the
        platform stores a hash, not the link, so it cannot be shown again. Resend the invitation
        to issue a fresh one.
      </p>
      <label className="mt-4 block">
        <span className="sr-only">Invitation link</span>
        <input
          readOnly
          value={invitation?.invite_url || ""}
          onFocus={(e) => e.target.select()}
          className={cx(
            "w-full rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 font-mono text-xs text-slate-700",
            "dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
          )}
        />
      </label>
      <p className="mt-2 text-xs text-slate-500 dark:text-slate-400">
        Single use, and it expires with the invitation.
      </p>
    </Modal>
  );
}

export default function InviteModal({ open, onClose, onInvited, event = null }) {
  const eventLocked = !!event;
  const [bulk, setBulk] = useState(false);
  const [email, setEmail] = useState("");
  const [emailList, setEmailList] = useState("");
  const [eventId, setEventId] = useState(event?.id || "");
  const [eventRole, setEventRole] = useState(eventLocked ? "speaker" : "");
  const [platformRole, setPlatformRole] = useState("");
  const [message, setMessage] = useState("");
  const [sendEmail, setSendEmail] = useState(true);
  const [issued, setIssued] = useState(null);

  // Only needed for the standalone page's event picker. `page_size` is deliberately small —
  // this is a convenience selector, not a browsing surface.
  const { data: events } = useApi(() =>
    api
      .get("/events", { params: { page_size: 100, sort_by: "start_time", order: "desc" } })
      .then((r) => r.data.items)
  );

  const invite = useMutation({ onDone: onInvited });

  const emails = useMemo(() => (bulk ? parseEmails(emailList) : []), [bulk, emailList]);
  const invalid = useMemo(() => emails.filter((e) => !isEmail(e)), [emails]);
  const targetEvent = eventLocked ? event : (events || []).find((e) => e.id === eventId);

  const reset = () => {
    setEmail("");
    setEmailList("");
    setMessage("");
    setBulk(false);
    if (!eventLocked) {
      setEventId("");
      setEventRole("");
    }
    setPlatformRole("");
    setSendEmail(true);
  };

  const close = () => {
    reset();
    onClose();
  };

  const body = () => ({
    ...(eventId ? { event_id: eventId, event_role: eventRole } : {}),
    ...(platformRole ? { role: platformRole } : {}),
    ...(message.trim() ? { message: message.trim() } : {}),
    send_email: sendEmail,
  });

  const submit = async () => {
    if (bulk) {
      const res = await invite.run(
        () => api.post("/organization/invitations/bulk", { emails, ...body() }),
        { success: null }
      );
      if (!res) return;
      const { succeeded = [], failed = [] } = res.data || {};
      if (succeeded.length) {
        notify.success(`${succeeded.length} invitation${succeeded.length === 1 ? "" : "s"} created`);
      }
      if (failed.length) notify.error(`${failed.length} skipped — ${failed[0].reason}`);
      if (succeeded.length) close();
      return;
    }
    const res = await invite.run(
      () => api.post("/organization/invitations", { email: email.trim().toLowerCase(), ...body() }),
      { success: sendEmail ? `Invitation sent to ${email.trim()}` : "Invitation created" }
    );
    if (!res) return;
    // A manual invitation has no email behind it, so the link is the deliverable — show it.
    if (!sendEmail && res.data?.invite_url) setIssued(res.data);
    else close();
  };

  const canSubmit = bulk
    ? emails.length > 0 && invalid.length === 0 && (!eventId || !!eventRole)
    : isEmail(email) && (!eventId || !!eventRole);

  return (
    <>
      <Modal
        open={open}
        onClose={invite.busy ? () => {} : close}
        title={eventLocked ? `Invite to "${event.title || "event"}"` : "Invite people"}
        size="xl"
        footer={
          <>
            <Button variant="secondary" size="sm" onClick={close} disabled={invite.busy}>Cancel</Button>
            <Button
              size="sm"
              leftIcon={sendEmail ? FiSend : FiLink}
              loading={invite.busy}
              disabled={!canSubmit || invite.busy}
              onClick={submit}
            >
              {sendEmail
                ? bulk ? `Send ${emails.length || ""} invitation${emails.length === 1 ? "" : "s"}`.trim() : "Send invitation"
                : "Create link"}
            </Button>
          </>
        }
      >
        <div className="max-h-[65vh] space-y-5 overflow-y-auto pr-1">
          {/* single vs bulk */}
          <div role="tablist" aria-label="Invitation mode" className="flex gap-1 rounded-lg bg-slate-100 p-1 dark:bg-slate-800">
            {[
              { key: false, label: "One person", icon: FiMail },
              { key: true, label: "Several people", icon: FiUsers },
            ].map(({ key, label, icon: Icon }) => (
              <button
                key={String(key)}
                role="tab"
                aria-selected={bulk === key}
                onClick={() => setBulk(key)}
                className={cx(
                  "inline-flex flex-1 items-center justify-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition",
                  focusRing,
                  bulk === key
                    ? "bg-white text-slate-900 shadow-sm dark:bg-slate-900 dark:text-white"
                    : "text-slate-500 hover:text-slate-700 dark:text-slate-400 dark:hover:text-slate-200"
                )}
              >
                <Icon aria-hidden="true" /> {label}
              </button>
            ))}
          </div>

          {bulk ? (
            <div>
              <Label variant="console" htmlFor="zk-inv-emails">Email addresses</Label>
              <Textarea
                id="zk-inv-emails"
                variant="console"
                rows={4}
                value={emailList}
                onChange={(e) => setEmailList(e.target.value)}
                placeholder="one@company.com, two@company.com&#10;three@company.com"
              />
              <Note
                error={invalid.length ? `Not a valid address: ${invalid.slice(0, 3).join(", ")}` : null}
                hint={
                  !invalid.length &&
                  `${emails.length} address${emails.length === 1 ? "" : "es"} — separate with commas, spaces or new lines. Up to 100.`
                }
              />
            </div>
          ) : (
            <div>
              <Label variant="console" htmlFor="zk-inv-email">Email address</Label>
              <Input
                id="zk-inv-email"
                variant="console"
                type="email"
                autoComplete="off"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="teammate@company.com"
              />
              <Note hint="If they already have a ZoikoStream account, accepting adds the access to it." />
            </div>
          )}

          {/* event scope */}
          {!eventLocked && (
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <div>
                <Label variant="console" htmlFor="zk-inv-event">Event (optional)</Label>
                <Select
                  id="zk-inv-event"
                  variant="console"
                  value={eventId}
                  onChange={(e) => {
                    setEventId(e.target.value);
                    if (e.target.value && !eventRole) setEventRole("speaker");
                    if (!e.target.value) setEventRole("");
                  }}
                >
                  <option value="">Organization only — no event role</option>
                  {(events || []).map((ev) => (
                    <option key={ev.id} value={ev.id}>{ev.title || "Untitled event"}</option>
                  ))}
                </Select>
              </div>
              <div>
                <Label variant="console" htmlFor="zk-inv-erole">Event role</Label>
                <Select
                  id="zk-inv-erole"
                  variant="console"
                  value={eventRole}
                  disabled={!eventId}
                  onChange={(e) => setEventRole(e.target.value)}
                >
                  <option value="">—</option>
                  {EVENT_ROLES.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
                </Select>
                <Note hint={EVENT_ROLES.find((r) => r.value === eventRole)?.hint} />
              </div>
            </div>
          )}

          {eventLocked && (
            <div>
              <Label variant="console" htmlFor="zk-inv-erole-locked">Event role</Label>
              <Select
                id="zk-inv-erole-locked"
                variant="console"
                value={eventRole}
                onChange={(e) => setEventRole(e.target.value)}
              >
                {EVENT_ROLES.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
              </Select>
              <Note hint={EVENT_ROLES.find((r) => r.value === eventRole)?.hint} />
            </div>
          )}

          {/* platform role */}
          <div>
            <Label variant="console" htmlFor="zk-inv-prole">Organization role</Label>
            <Select
              id="zk-inv-prole"
              variant="console"
              value={platformRole}
              onChange={(e) => setPlatformRole(e.target.value)}
            >
              <option value="">
                {targetEvent ? "Recommended for this event role" : "Viewer"}
              </option>
              {PLATFORM_ROLES.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
            </Select>
            <Note
              hint={
                platformRole
                  ? PLATFORM_ROLES.find((r) => r.value === platformRole)?.hint
                  : targetEvent
                    ? "Left blank, the event role picks a conservative default — an event role never silently raises someone's organization-wide access."
                    : "What they can reach across the whole organization."
              }
            />
          </div>

          <div>
            <Label variant="console" htmlFor="zk-inv-msg">Personal note (optional)</Label>
            <Textarea
              id="zk-inv-msg"
              variant="console"
              rows={2}
              maxLength={1000}
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              placeholder="Shown in the invitation email."
            />
          </div>

          <div className="space-y-2">
            <Switch
              accent="violet"
              checked={sendEmail}
              onChange={setSendEmail}
              label="Send the invitation email"
            />
            {!sendEmail && (
              <p className="flex items-start gap-2 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-700 dark:bg-amber-500/10 dark:text-amber-400">
                <FiLink className="mt-0.5 shrink-0" aria-hidden="true" />
                No email is sent. You'll get the link once, to deliver yourself.
              </p>
            )}
          </div>

          {targetEvent && (
            <p className="flex flex-wrap items-center gap-2 border-t border-slate-100 pt-4 text-xs text-slate-500 dark:border-slate-800 dark:text-slate-400">
              <Badge tone="brand" size="sm">Event invitation</Badge>
              Accepting adds them to your organization and assigns the {eventRole || "event"} role
              on <strong className="font-semibold text-slate-700 dark:text-slate-200">{targetEvent.title || "this event"}</strong>.
              For an audience member, use a viewer access link instead.
            </p>
          )}
        </div>
      </Modal>

      <LinkReveal
        invitation={issued}
        onClose={() => {
          setIssued(null);
          close();
        }}
      />
    </>
  );
}
