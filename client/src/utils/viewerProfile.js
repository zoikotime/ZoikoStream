// The remembered viewer profile: a name and, if they gave one, a country, for this browser,
// for convenience.
//
// ── WHAT THIS IS NOT ────────────────────────────────────────────────────────────────────
// This is deliberately NOT an access credential, and the two must never be confused:
//
//   device profile (here)          the viewer's name and optional ISO country code. One key
//                                  for the whole browser. Grants NOTHING — it only pre-fills
//                                  a form.
//   event credential (EventWatch)  `zk_reg_<eventId>`, an opaque server-signed JWT bound
//                                  to ONE event and verified per request. Grants access.
//
// So arriving at a second event with a saved profile shows "Continue as Naveen" and still
// makes that viewer register for THAT event: a token minted for event A is never presented
// to event B, and the server would refuse it if it were
// (security.decode_registration_payload checks the event id).
//
// ── PRIVACY ─────────────────────────────────────────────────────────────────────────────
// localStorage on this origin, and nothing else. No fingerprinting, no device id, no
// network call, nothing derived from the browser itself. It cannot follow anyone to another
// browser or another machine, and a private/incognito window gets its own empty store and
// so behaves exactly like a fresh device. clearViewerProfile() is the viewer's off switch
// and is wired to "Not you? / Change details" in the registration form.
//
// Only written when the viewer ticks "Remember me for this event" — the same consent that
// decides whether the event credential persists. Unticking it on a later registration
// clears what was saved before, so the choice is revocable rather than one-way.

import { countryName } from "./countries";

const KEY = "zk_viewer_profile";

/**
 * The saved profile, or null when there is none, storage is unavailable (private mode with
 * cookies blocked), or what is stored no longer looks like a name. A bad record reads as
 * "no profile" rather than throwing — a corrupt key must not be able to take the
 * registration form down with it.
 *
 * A record written before the viewer gate stopped collecting an address still has an `email`
 * field. It is IGNORED rather than rejected: the name in it is still the viewer's name, and
 * failing the whole profile would have logged out every returning viewer on upgrade. The
 * address is simply never read and never written again, so it ages out on the next save.
 *
 * `country` is an ISO code from the same list the server accepts, or null — a record saved
 * before the field existed, or one holding something unrecognised, simply has no country.
 *
 * @returns {{name: string, country: string | null} | null}
 */
export function readViewerProfile() {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return null;
    const { name, country } = JSON.parse(raw) || {};
    if (typeof name !== "string" || !name.trim()) return null;
    const code = typeof country === "string" && countryName(country) ? country.toUpperCase() : null;
    return { name: name.trim(), country: code };
  } catch {
    return null;
  }
}

/**
 * Remember this viewer on this device. Called ONLY after a registration the server
 * accepted, and only when "Remember me" was ticked — never from the form as it is typed,
 * so a half-finished or rejected attempt leaves nothing behind.
 */
export function saveViewerProfile({ name, country = null }) {
  if (!name?.trim()) return;
  const code = country && countryName(country) ? country.toUpperCase() : null;
  try {
    localStorage.setItem(KEY, JSON.stringify({ name: name.trim(), ...(code ? { country: code } : {}) }));
  } catch { /* storage unavailable; this visit still works, nothing is remembered */ }
}

/** Forget this viewer. Their event credentials are untouched — those are per-event and
 *  expire on their own terms; this only removes the convenience. */
export function clearViewerProfile() {
  try {
    localStorage.removeItem(KEY);
  } catch { /* nothing to clear if storage was never reachable */ }
}
