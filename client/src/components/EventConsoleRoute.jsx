import { useEffect, useRef, useState } from "react";
import { Navigate, Outlet, useLocation, useSearchParams } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { accountHome, fetchConsoleAccess } from "../auth/destination";
import { PageSpinner } from "../ui/Spinner";
import { notify } from "../ui/Toast";

// Gate an event console on a REAL, server-confirmed assignment.
//
// RoleRoute answers "is this account allowed to use consoles at all" — a coarse question,
// and the right one for it. This answers the specific one it cannot: "does this person run
// THIS event?" Being a host-persona account is not an answer; only an EventAssignment is,
// and only the server knows.
//
// Two refusals, both ending at the account's own home rather than in a console:
//
//   no ?event=<id>        a Producer Console with no event is not a destination. The old
//                         behaviour let useLiveEvent silently pick one from the org's list.
//   assignment refused    the backend says this user has no host/moderator/contributor
//                         claim on that event.
//
// Nothing here reads storage. `capability` names the field(s) the backend must confirm —
// can_host, can_moderate or can_contribute — computed by the same rule that grants broadcast
// control on the live socket.
//
// It accepts an ARRAY because the host console now serves moderation too: the separate
// moderator console was retired and its URL forwards here (App.jsx's
// LegacyModeratorRedirect). Demanding can_host alone would lock a legacy moderator out of
// the only console left to them. Any one of the listed capabilities admits the page shell;
// every control inside is still gated individually on canHost/canModerate from the server's
// own snapshot, so admitting a moderator grants them nothing a host has.
export default function EventConsoleRoute({ capability }) {
  const required = Array.isArray(capability) ? capability : [capability];
  const { user, loading } = useAuth();
  const location = useLocation();
  const [params] = useSearchParams();
  const eventId = params.get("event");
  // "checking" until the server answers. Never optimistic: the console must not mount for
  // even one frame on an event this user may not run.
  const [access, setAccess] = useState("checking");
  const told = useRef(false);

  useEffect(() => {
    if (loading || !user || !eventId) return undefined;
    let cancelled = false;
    // Reset before asking: changing the ?event= must not leave the previous event's verdict
    // standing for even one render.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setAccess("checking");
    fetchConsoleAccess(eventId).then((result) => {
      if (cancelled) return;
      setAccess(result && required.some((c) => result[c]) ? "granted" : "denied");
    });
    return () => { cancelled = true; };
    // `required` is derived from `capability` each render; depending on the prop keeps
    // the effect stable without an extra memo.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loading, user, eventId, capability]);

  // Say why, once, rather than bouncing the user somewhere with no explanation.
  useEffect(() => {
    if (access === "denied" && !told.current) {
      told.current = true;
      notify.error("You aren't assigned to run this event.");
    }
  }, [access]);

  if (loading) return <PageSpinner />;
  if (!user) return <Navigate to="/login" state={{ from: location }} replace />;

  // No event context at all. Not an error the user caused — send them to pick one.
  if (!eventId) return <Navigate to={accountHome(user.role) || "/"} replace />;

  if (access === "checking") return <PageSpinner />;
  if (access === "denied") return <Navigate to={accountHome(user.role) || "/"} replace />;
  return <Outlet />;
}
