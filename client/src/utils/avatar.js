// Profile photos, in one place: how a stored avatar path becomes an image URL, what initials
// stand in when there is no photo, what the uploader accepts, and how the rest of the app
// hears that the signed-in user's photo changed.
//
// The server (server/app/services/avatars.py) decides what is actually accepted; the checks
// here only spare the user a round trip for a file that would certainly be refused.
import { API_BASE } from "../api";

// Only a path the platform itself serves becomes an <img src>. Anything else — a typo'd
// value, an external URL — is treated as "no photo" rather than loaded from wherever it points.
const OURS = /^\/api\/avatars\/[0-9a-f-]{36}\/[A-Za-z0-9_-]{32}\.webp$/;

/** The image URL for a stored avatar path, or null. API_BASE is read only when there is one. */
export const avatarSrc = (path) =>
  path && OURS.test(path) ? `${String(API_BASE).replace(/\/$/, "")}${path}` : null;

/** Up to two initials, or null when the name has none (the caller shows a generic icon). */
export const initialsOf = (name) => {
  const words = String(name || "").trim().split(/\s+/).filter(Boolean);
  return words.slice(0, 2).map((w) => w[0]).join("").toUpperCase() || null;
};

export const ACCEPTED_TYPES = ["image/png", "image/jpeg", "image/webp"];
export const MAX_PHOTO_BYTES = 5 * 1024 * 1024;

/** A user-facing reason this file will not be accepted, or null. */
export function photoFileError(file) {
  if (!file) return "Choose an image to upload.";
  if (!ACCEPTED_TYPES.includes(file.type)) return "Upload a PNG, JPG or WebP image.";
  if (file.size > MAX_PHOTO_BYTES) return "That image is larger than 5 MB.";
  if (file.size === 0) return "That file is empty.";
  return null;
}

// Fired after the signed-in user's photo changes, so chrome that reads identity from another
// payload (the organization console's /console-state) refreshes at once instead of on its timer.
export const PROFILE_UPDATED_EVENT = "zoiko:profile-updated";
export const announceProfileUpdated = (user) =>
  window.dispatchEvent(new CustomEvent(PROFILE_UPDATED_EVENT, { detail: { user } }));
