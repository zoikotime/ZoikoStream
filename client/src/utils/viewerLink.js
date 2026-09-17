// The attendee viewer link, in one place.
//
// The URL itself is unchanged — `${origin}/events/${eventId}/watch` is exactly what
// EventDetails already built inline. It lives here because three screens now offer a copy
// action, and three inline template literals is three chances for one of them to drift from
// the route the router actually serves.
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

/** The attendee watch URL for an event. Absolute, because it is meant to be pasted. */
export const viewerLinkFor = (eventId) =>
  `${window.location.origin}/events/${eventId}/watch`;

/**
 * Copy the viewer link to the clipboard.
 *
 * Resolves true on success, false when the clipboard is unavailable or refuses (insecure
 * origin, permission denied, older browser). Callers surface a short, safe message on
 * false — never the underlying error, which says nothing a user can act on.
 */
export async function copyViewerLink(eventId) {
  try {
    if (!navigator.clipboard?.writeText) return false;
    await navigator.clipboard.writeText(viewerLinkFor(eventId));
    return true;
  } catch {
    return false;
  }
}
