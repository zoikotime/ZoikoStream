import Badge from "../../ui/Badge";
import { INVITATION_STATUS, DELIVERY } from "../../data/invitations";

// ONE status pill for every invitation surface. The LABEL comes from the server
// (`status_label`), so the stored `rejected` reads as "Declined" in exactly one place — the
// API boundary — and no screen re-derives the vocabulary from string literals.
export function InvitationStatusBadge({ invitation, size = "sm" }) {
  const meta = INVITATION_STATUS[invitation.status] || { tone: "neutral" };
  return (
    <Badge tone={meta.tone} dot={meta.dot} size={size}>
      {invitation.status_label || invitation.status}
    </Badge>
  );
}

// Delivery is reported separately from lifecycle, because they answer different questions:
// status is "has this person responded", delivery is "did the email arrive". A `delivered`
// state only ever appears when the Resend webhook confirmed it.
export function DeliveryBadge({ invitation, size = "sm" }) {
  const meta = DELIVERY[invitation.delivery] || DELIVERY.not_sent;
  return (
    <Badge tone={meta.tone} size={size} className={meta.tone === "neutral" ? "opacity-80" : ""}>
      {meta.label}
    </Badge>
  );
}
