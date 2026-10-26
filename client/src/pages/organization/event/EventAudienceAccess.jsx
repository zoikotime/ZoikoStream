import { useState } from "react";
import {
  FiActivity, FiAlertCircle, FiEye, FiFileText, FiLink, FiLock, FiMail, FiShield, FiUserCheck, FiUsers,
} from "react-icons/fi";
import api from "../../../api";
import useApi from "../../../hooks/useApi";
import { notify } from "../../../ui/Toast";
import OrganizationErrorState from "../../../components/organization/OrganizationErrorState";
import OrganizationEmptyState from "../../../components/organization/OrganizationEmptyState";
import EventAccessLinks from "../../../components/organization/EventAccessLinks";
import { ConsoleButton as Button } from "../../../ui/Button";
import Badge from "../../../ui/Badge";
import { CONSOLE, cx } from "../../../ui/tokens";
import { ON_AIR_STATUSES, VISIBILITY_HELP, visLabel } from "../../../data/events";
import { copyViewerLink } from "../../../utils/viewerLink";
import { Banner, DarkCard, Footnote, KpiRow, KpiTile, Panel, PanelLink, Tag } from "../../../components/organization/console/ConsoleUI";
import InviteViewersModal from "../InviteViewersModal";
import EventSectionHeader from "./EventSectionHeader";
import { sectionPath, useEventWorkspace, useHashScroll } from "./workspace";
import { dateBlock } from "../../../data/eventTime";

// EVT-03 Audience Access (ZST-UX-NAV-001 §10; EVT-03 frame). Shows the access posture the
// server enforces today and the tools that already exist for it — access links (issue, rotate,
// revoke), registrations and invitations, and the join refusals viewers actually hit.
//
// It does NOT edit the access policy. The viewer-access policy is an approval-gated decision
// (§10: "support eventual access governance without treating the draft as approved"), so the
// posture is read-only here and says so, instead of offering a toggle that would quietly
// decide policy.

// What a viewer meets, derived from the event's own fields — never from a guess about policy.
function previewSteps(event) {
  const steps = [];
  if (event.status === "draft") steps.push("The event is a draft: nobody outside the organization can open it yet.");
  if (event.status === "cancelled") steps.push("The event is cancelled: viewers are told so and cannot join.");
  steps.push(VISIBILITY_HELP[event.visibility] || "Access follows the event's visibility.");
  if (event.registration_required) {
    steps.push(
      event.registration_limit
        ? `Viewers register first; registration closes at ${event.registration_limit}.`
        : "Viewers register first, then receive their watch link."
    );
  }
  if (event.waiting_room_enabled) steps.push("Viewers wait in a waiting room until the host admits the audience.");
  steps.push(
    ON_AIR_STATUSES.includes(event.status)
      ? "The event is live now: admitted viewers see the broadcast."
      : "Before go-live, viewers see the event page and its start time."
  );
  return steps;
}

function GovernanceRow({ title, detail, tag, tone }) {
  return (
    <li className="flex items-center gap-3 py-3">
      <span aria-hidden="true" className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-slate-100 text-slate-500 dark:bg-white/[0.08] dark:text-neutral-300">
        <FiLock />
      </span>
      <div className="min-w-0 flex-1">
        <p className={cx("text-[13.5px] font-semibold", CONSOLE.heading)}>{title}</p>
        <p className={cx("text-[12px]", CONSOLE.faint)}>{detail}</p>
      </div>
      <Tag tone={tone}>{tag}</Tag>
    </li>
  );
}

function Registrations({ event }) {
  const regs = useApi(() => api.get(`/events/${event.id}/registrations`).then((r) => r.data));
  const [inviteOpen, setInviteOpen] = useState(false);
  const rows = regs.data || [];
  return (
    <Panel
      id="registrations"
      title="Registrations & invitations"
      subtitle="People registered for, or invited to, this event."
      action={<Button size="sm" leftIcon={FiMail} onClick={() => setInviteOpen(true)}>Invite viewers</Button>}
      flush={rows.length > 0}
    >
      {regs.error ? (
        <OrganizationErrorState error={regs.error} onRetry={regs.reload} title="Couldn't load registrations" />
      ) : !regs.data ? (
        <p className={cx("text-sm", CONSOLE.faint)}>Loading…</p>
      ) : rows.length === 0 ? (
        <OrganizationEmptyState
          icon={FiUsers}
          title="No viewers yet"
          description={
            event.visibility === "private"
              ? "Invite people by email to let them into this private event."
              : event.registration_required
                ? "Registered attendees will appear here."
                : "Invite people by email, or share the event link — registration isn't required."
          }
        />
      ) : (
        <ul className={cx("divide-y border-t", CONSOLE.divider, CONSOLE.divideY)}>
          {rows.map((v) => (
            <li key={v.id} className="flex items-center justify-between gap-3 px-5 py-3">
              <div className="min-w-0">
                <p className={cx("truncate text-sm font-medium", CONSOLE.heading)}>{v.name}</p>
                <p className={cx("truncate text-[12px]", CONSOLE.faint)}>{v.email}</p>
              </div>
              {v.invited_by ? <Badge tone="info" size="sm">Invited</Badge> : <Badge tone="neutral" size="sm">Registered</Badge>}
            </li>
          ))}
        </ul>
      )}
      <InviteViewersModal
        open={inviteOpen}
        onClose={() => setInviteOpen(false)}
        eventId={event.id}
        eventVisibility={event.visibility}
        onInvited={regs.reload}
      />
    </Panel>
  );
}

export default function EventAudienceAccess() {
  const { event, eventId, readiness } = useEventWorkspace();
  useHashScroll();
  const insights = useApi(() => api.get(`/organization/events/${eventId}/insights`).then((r) => r.data));
  const links = useApi(() => api.get(`/events/${eventId}/access-links`).then((r) => r.data));
  const copy = async () => {
    if (await copyViewerLink(event)) notify.success("Viewer link copied");
    else notify.error("Unable to copy viewer link.");
  };

  const blocked = insights.data?.blocked_join_attempts;
  const reg = insights.data?.registration;
  const linkRows = links.data || [];
  const activeLinks = linkRows.filter((l) => !l.revoked_at);
  const expiring = activeLinks.filter((l) => l.expires_at);
  const blockers = (readiness?.gates || []).filter((g) => g.required && !g.passed);
  const block = dateBlock(event.start_time, event.timezone);

  return (
    <div className="space-y-6">
      <EventSectionHeader
        icon={FiShield}
        eyebrow="Event workspace / 03"
        title="Audience Access"
        subtitle="Define and verify how viewers may enter this event. Access settings remain governed by the approved event policy."
        actions={
          <>
            <Button variant="secondary" size="sm" leftIcon={FiEye} href="#viewer-preview">Viewer preview</Button>
            <Button size="sm" leftIcon={FiLink} onClick={copy}>Copy viewer link</Button>
          </>
        }
      />

      <Banner tone="warning" title="Viewer-access policy is approval-gated — this profile is read-only"
        aside={<Tag tone="amber">Read-only here</Tag>}>
        Who may watch is enforced from the event&rsquo;s visibility and registration settings. Editing that policy is not offered
        here until it is approved; issuing and revoking links and invitations below are audited.
      </Banner>

      <KpiRow>
        <KpiTile icon={FiShield} tone="violet" label="Policy approval" emphasis="text" value="Approval-gated" caption="No approved policy editor in use" />
        <KpiTile icon={FiLock} tone="amber" label="Active access profile" emphasis="text" value={visLabel(event.visibility)}
          caption={event.registration_required ? "Registration required · enforced" : "No registration · enforced"} />
        <KpiTile icon={FiEye} tone="sky" label="Viewer preview" emphasis="text" value="Read-only" caption="No playback entitlement implied" />
        <KpiTile icon={FiActivity} tone="green" label="Change evidence" emphasis="text" value="Audited"
          caption="Link and invitation changes are recorded" to={`${sectionPath(eventId, "activity")}?category=access`} />
      </KpiRow>

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1.25fr)_minmax(0,1fr)]">
        <Panel title="Viewer access governance" subtitle="What the server enforces for this event · no policy mode assumed" tag="Approval-gated" tagTone="violet">
          <p className={cx("mb-1 rounded-lg border px-3 py-2 text-[12.5px]", CONSOLE.divider, CONSOLE.muted)}>
            Registration is <strong className={CONSOLE.heading}>{event.registration_required ? "required" : "not required"}</strong> for this
            event. Identity, authentication and access authorization are evaluated independently on every join.
          </p>
          <ul className={cx("divide-y", CONSOLE.divideY)}>
            <GovernanceRow title="Audience eligibility" detail={VISIBILITY_HELP[event.visibility] || "Follows the event's visibility"} tag={visLabel(event.visibility)} tone="violet" />
            <GovernanceRow title="Authentication & authorization"
              detail={event.registration_required ? "Viewers register; playback authorizes every request" : "Playback authorizes every request"}
              tag={event.registration_required ? "Registration" : "Link"} tone="sky" />
            <GovernanceRow title="Link sharing & preview safety"
              detail={links.data ? `${activeLinks.length} active access link${activeLinks.length === 1 ? "" : "s"}` : "Reading links…"}
              tag={activeLinks.length ? "Links issued" : "None issued"} tone={activeLinks.length ? "amber" : "slate"} />
            <GovernanceRow title="Expiry, revocation & recovery"
              detail={links.data ? `${expiring.length} link${expiring.length === 1 ? "" : "s"} with an expiry · revocable below` : "Reading links…"}
              tag="Revocable" tone="green" />
          </ul>
          <Footnote>Changes require scoped permission and leave a durable audit receipt.</Footnote>
        </Panel>

        <Panel id="viewer-preview" title="Viewer experience preview" subtitle="Event-branded read-only preview · no published viewer link"
          action={<PanelLink to="#access-links">Access links</PanelLink>}>
          <DarkCard as="div" className="p-0">
            <div className="flex items-center justify-between border-b border-white/10 px-4 py-2 text-[10px] font-bold uppercase tracking-[0.14em]">
              <span>ZoikoStream</span><span className="text-white/60">Viewer experience · preview</span>
            </div>
            <div className="px-6 py-8 text-center">
              <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-fuchsia-200">{event.title || "Untitled event"}</p>
              <p className="mt-2 text-[20px] font-bold">One event. One audience.</p>
              <p className="mt-1 text-[12px] text-white/70">{block ? `${block.date} · ${block.local}` : "Date to be announced"}</p>
            </div>
          </DarkCard>
          <div className={cx("mt-3 flex items-center justify-between gap-3 rounded-xl border px-3 py-2.5", CONSOLE.divider)}>
            <div>
              <p className={cx("text-[13px] font-semibold", CONSOLE.heading)}>Viewer access: {visLabel(event.visibility)}</p>
              <p className={cx("text-[11.5px]", CONSOLE.faint)}>The console never opens the attendee page; copy the link instead.</p>
            </div>
            <Tag tone="violet">Preview only</Tag>
          </div>
          <ol className="mt-4 space-y-2.5">
            {previewSteps(event).map((step, i) => (
              <li key={step} className="flex gap-3 text-[13px]">
                <span className="grid h-5 w-5 shrink-0 place-items-center rounded-full bg-violet-100 text-[11px] font-bold text-violet-700 dark:bg-violet-500/15 dark:text-violet-300">{i + 1}</span>
                <span className={CONSOLE.body}>{step}</span>
              </li>
            ))}
          </ol>
        </Panel>
      </div>

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1.25fr)_minmax(0,1fr)]">
        <Panel id="diagnostics" title="Access readiness & diagnostics" subtitle="From the server's own records · no invented viewer counts or identities">
          {insights.error ? (
            <OrganizationErrorState error={insights.error} onRetry={insights.reload} title="Couldn't load diagnostics" />
          ) : !insights.data ? (
            <p className={cx("text-sm", CONSOLE.faint)}>Loading…</p>
          ) : (
            <>
              <ul className={cx("divide-y", CONSOLE.divideY)}>
                {[
                  ["Policy approval", "Policy editing is approval-gated", "Gated", "violet"],
                  ["Viewer entry flow", event.registration_required ? "Register, then watch" : "Open link, then watch", event.registration_required ? "Registration" : "Open", "sky"],
                  ["Revocation behaviour", `${activeLinks.length} active link${activeLinks.length === 1 ? "" : "s"} can be revoked`, "Ready", "green"],
                  ["Refused join attempts", `${blocked?.total ?? 0} refused by the server`, blocked?.total ? "Review" : "None", blocked?.total ? "amber" : "slate"],
                  ["Registered → attended", reg?.applicable ? `${reg.attended} of ${reg.registered} attended` : "Nobody has registered", reg?.applicable ? `${reg.rate ?? 0}%` : "—", "slate"],
                ].map(([title, detail, tag, tone]) => (
                  <li key={title} className="flex items-center gap-3 py-2.5">
                    <FiShield aria-hidden="true" className={CONSOLE.faint} />
                    <span className={cx("text-[13px] font-semibold", CONSOLE.heading)}>{title}</span>
                    <span className={cx("flex-1 truncate text-[12px]", CONSOLE.faint)}>{detail}</span>
                    <Tag tone={tone}>{tag}</Tag>
                  </li>
                ))}
              </ul>
              {blocked?.reasons?.length > 0 && (
                <ul className={cx("mt-2 divide-y border-t text-sm", CONSOLE.divider, CONSOLE.divideY)}>
                  {blocked.reasons.map((r) => (
                    <li key={r.reason} className="flex justify-between gap-3 py-2">
                      <span className={CONSOLE.body}><FiAlertCircle aria-hidden="true" className="mr-1.5 inline" />{r.label}</span>
                      <span className={cx("font-semibold", CONSOLE.heading)}>{r.count}</span>
                    </li>
                  ))}
                </ul>
              )}
              <Footnote>Diagnostics are event-scoped and report their freshness; playback checks happen independently on every request.</Footnote>
            </>
          )}
        </Panel>

        <Panel title="Policy decisions & handoff" subtitle="Accountability and blockers for this event">
          <div className="grid gap-3 sm:grid-cols-2">
            {[
              [FiUserCheck, "Decision owner", "Organization admins"],
              [FiFileText, "Viewer-access policy", "Approval-gated"],
              [FiActivity, "Preparation handoff", blockers.length ? "Blocked" : readiness ? "Ready" : "—", blockers.length ? "warn" : null],
              [FiFileText, "Audit record", "Kept for every change"],
            ].map(([Icon, label, value, tone]) => (
              <div key={label} className={cx(CONSOLE.inset, "p-3.5")}>
                <p className={cx("flex items-center gap-1.5 text-[11.5px]", CONSOLE.faint)}><Icon aria-hidden="true" /> {label}</p>
                <p className={cx("mt-1 text-[14px] font-semibold", tone === "warn" ? "text-amber-700 dark:text-amber-300" : CONSOLE.heading)}>{value}</p>
              </div>
            ))}
          </div>
          {blockers.length > 0 && (
            <p className="mt-3 rounded-lg border border-amber-200 bg-amber-50/70 px-3 py-2 text-[12px] text-amber-800 dark:border-amber-400/30 dark:bg-amber-500/10 dark:text-amber-200">
              Event launch remains blocked until the required readiness checks pass: {blockers.map((g) => g.label).join(", ")}.
            </p>
          )}
        </Panel>
      </div>

      <Panel id="access-links" title="Access links" subtitle="Shareable links you can expire, rotate or revoke. Every change is audited.">
        <EventAccessLinks event={event} canManage />
      </Panel>

      <Registrations event={event} />
    </div>
  );
}
