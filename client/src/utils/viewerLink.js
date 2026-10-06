// The attendee viewer link, in one place.
//
// It lives here because three screens offer a copy action, and three inline template
// literals is three chances for one of them to drift from the route the router serves.
//
// ── COPY ONLY, DELIBERATELY ──────────────────────────────────────────────────────────────
// This module exposes no way to OPEN the viewer page, and callers must not render the
// returned string. The organizer console's job is to hand the link to someone else; an
// organizer following it themselves lands in the attendee experience for their own event,
// which is not what that console is for. Keeping the URL out of the DOM also keeps it out of
// screenshots and screen shares of the console.
//
// `/e/:id` is a separate, fully-mocked marketing page — the real, backend-wired viewer page
// is /events/:eventId/watch, which is what this builds.

// ── WHICH ADDRESS ────────────────────────────────────────────────────────────────────────
// The server is the authority: every event payload carries `public_watch_url`
// (server/app/services/public_urls.py), which is the organization's ACTIVE custom domain when
// it has one and the platform address otherwise. Building the link from
// window.location.origin instead shared whatever host the organizer happened to be on —
// localhost:5173, a preview URL — and could never use a custom domain. The origin is only the
// fallback for an older API that does not send the field.

/** The attendee watch URL. Pass the event (preferred) or, for older callers, its id. */
export const viewerLinkFor = (eventOrId) => {
  if (eventOrId && typeof eventOrId === "object") {
    if (eventOrId.public_watch_url) return eventOrId.public_watch_url;
    return `${window.location.origin}/events/${eventOrId.id}/watch`;
  }
  return `${window.location.origin}/events/${eventOrId}/watch`;
};

/**
 * Copy the viewer link to the clipboard.
 *
 * Resolves true on success, false when the clipboard is unavailable or refuses (insecure
 * origin, permission denied, older browser). Callers surface a short, safe message on
 * false — never the underlying error, which says nothing a user can act on.
 */
export async function copyViewerLink(eventOrId) {
  try {
    if (!navigator.clipboard?.writeText) return false;
    await navigator.clipboard.writeText(viewerLinkFor(eventOrId));
    return true;
  } catch {
    return false;
  }
}
