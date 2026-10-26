// WF-12 first-use "Privacy in Guide" notice: shown until the visitor continues or asks a first
// question, once per page load. Memory only — never written to browser storage.
let seen = false;
export const privacyNoticeSeen = () => seen;
export const markPrivacyNoticeSeen = () => {
  seen = true;
};
/** Test helper: a fresh page load. */
export const resetPrivacyNotice = () => {
  seen = false;
};
