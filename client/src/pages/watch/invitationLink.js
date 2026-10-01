// Invitation links on the viewer page (ZST-SPEC-VAP-001 §5.1, server side:
// server/app/services/invitation_links.py).
//
//   /events/<id>/watch#invite=<secret>   personal emailed invitation
//   /events/<id>/watch#link=<secret>     host-issued shareable access link
//
// The secret lives in the FRAGMENT, which the browser never sends with the page request.
// The page reads it here, POSTs it once (POST /events/:id/invitation), stores the credential
// it gets back exactly where the existing flow keeps one (zk_reg_<id> / zk_link_<id>), and
// then removes the secret from the address bar with history.replaceState. From then on it is
// the same registration/watch flow as before.
//
// Links sent before this change still work: ?reg=<token> is used as it always was, and
// ?link=<token> goes through the same exchange. Both are then stripped from the address bar
// too, so a re-shared screenshot, the Share button or the browser history never carries them.
import api from "../../api";

const parse = (text) => new URLSearchParams(String(text || "").replace(/^[#?]/, ""));

/** What the current URL carries, read without changing anything. */
export function readInvitation(location = window.location) {
  const hash = parse(location.hash);
  const query = parse(location.search);
  const fragment = hash.get("invite")
    ? { kind: "invite", secret: hash.get("invite") }
    : hash.get("link") ? { kind: "link", secret: hash.get("link") } : null;
  return { fragment, legacyReg: query.get("reg"), legacyLink: query.get("link") };
}

/** Remove every credential from the address bar, leaving anything else in place. */
export function stripInvitation(location = window.location, history = window.history) {
  const query = parse(location.search);
  query.delete("reg");
  query.delete("link");
  const hash = parse(location.hash);
  hash.delete("invite");
  hash.delete("link");
  const search = query.toString();
  const rest = hash.toString();
  // history.state is React Router's own bookkeeping; replacing the URL must keep it.
  history.replaceState(history.state, "", `${location.pathname}${search ? `?${search}` : ""}${rest ? `#${rest}` : ""}`);
}

/** The exchange. Resolves to { credential: "reg" | "link", token }. */
export async function redeemInvitation(eventId, { kind, secret }) {
  const { data } = await api.post(`/events/${eventId}/invitation`, { kind, secret });
  return data;
}
