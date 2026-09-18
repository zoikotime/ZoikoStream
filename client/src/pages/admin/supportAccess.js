// The ORG-009 support-access gate, as the console needs to read it.
//
// Editing a tenant's member is support access to that tenant, so routers/admin.py gates every
// write behind ctx.authorize(org_id, CAP_MEMBERS_WRITE) — an approved, live, scoped session.
// That gate is deliberate and is not touched here. What was missing was any way to SEE it:
// Edit, Activate/Deactivate and Delete were rendered as ordinary buttons and every one of
// them returned 403 "This Organization has not approved an active support session", with no
// surface saying what state you were in or what to do next.
//
// This module only READS and REQUESTS. Approval belongs to the organization (an org admin
// decides, through the organization's own surface) and starting a session is a separate
// explicit step. Nothing here approves, auto-approves or bypasses anything.

// Capability the member-editing routes demand. Mirrors tenant_access.CAP_MEMBERS_WRITE.
export const CAP_MEMBERS_WRITE = "tenant.members.write";

// The model's own status vocabulary (models/support_access.py SUPPORT_*). Not UI inventions —
// if the backend adds a status, it shows up here as `unknown` rather than being mislabelled.
export const SUPPORT_STATUS = {
  requested: {
    label: "Awaiting organization approval",
    detail: "The organization has been asked and has not answered yet.",
    canRequest: false,
  },
  approved: {
    label: "Approved — not started",
    detail: "The organization approved this request. Start the session to make changes.",
    canRequest: false,
  },
  active: {
    label: "Active",
    detail: "Account changes are enabled for the duration of this session.",
    canRequest: false,
  },
  denied: {
    label: "Denied",
    detail: "The organization declined this request.",
    canRequest: true,
  },
  expired: {
    label: "Expired",
    detail: "The session ran out of time. Request access again to continue.",
    canRequest: true,
  },
  ended: {
    label: "Ended",
    detail: "The session was closed. Request access again to continue.",
    canRequest: true,
  },
};

/**
 * Turn GET /admin/support-access/state into the one question the modal asks:
 * may this admin write to this tenant right now, and if not, what is true instead.
 *
 * `canWrite` comes from the server, which derives it from the same predicates the gate uses —
 * never from the status string alone, because "approved" is not yet "live" and a live session
 * held by another engineer is not this admin's authority.
 */
export function readSupportState(state) {
  if (!state) return { canWrite: false, status: null, label: null, detail: null, canRequest: true };

  const mine = state.mine || null;
  const status = mine?.status || null;
  const known = status ? SUPPORT_STATUS[status] : null;

  if (state.can_write_members) {
    return {
      canWrite: true,
      status: "active",
      label: SUPPORT_STATUS.active.label,
      detail: SUPPORT_STATUS.active.detail,
      expiresAt: mine?.expires_at || null,
      canRequest: false,
    };
  }

  if (state.other_engineer_active) {
    return {
      canWrite: false,
      status: "other_engineer",
      label: "Another engineer holds the active session",
      detail: "Only the engineer a session was approved for may use it.",
      canRequest: true,
    };
  }

  if (!status) {
    return {
      canWrite: false,
      status: "none",
      label: "No support access requested",
      detail: "The organization must approve access before this account can be changed.",
      canRequest: true,
    };
  }

  return {
    canWrite: false,
    status,
    // An unrecognised status is reported as itself rather than guessed at.
    label: known?.label || `Support access: ${status}`,
    detail: known?.detail || "This request is in a state this console does not recognise.",
    expiresAt: mine?.expires_at || null,
    canRequest: known ? known.canRequest : true,
  };
}
