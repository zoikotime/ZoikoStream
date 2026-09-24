import { useCallback, useState } from "react";
import { useOutletContext } from "react-router-dom";
import api from "../../api";
import useInterval from "../../hooks/useInterval";

// Client-side view of the server's elevation gate (security.require_elevation).
//
// This exists so an elevation-gated control can show its REAL state instead of a button
// that looks available and 403s. Three rules it has to keep:
//
//  1. It is a mirror, never the authority. The server decides; this only stops the console
//     from offering an action it already knows will be refused. Callers must still handle a
//     403 from the request itself — the grant can lapse or be ended from another tab between
//     the check and the click.
//  2. It reads `granted_scopes`, the union across every live grant, which is the same set
//     require_elevation checks. Reading the badge's single `scope` would wrongly block an
//     operator holding a second grant (services/ops.active_elevation_scopes).
//  3. Unknown is not "not elevated". If /admin/console-state failed we cannot claim the
//     operator is unelevated, and must not offer to elevate against an API we can't reach —
//     the same distinction the rail's Elevation widget draws.
//
// Expiry is enforced locally between the shell's 30s refreshes: the countdown ticks down and
// the control blocks itself the moment it hits zero, rather than staying enabled for up to
// half a minute after the grant has actually lapsed.
export default function useElevation(scope) {
  const ctx = useOutletContext() || {};
  const { state, unknown, reload } = ctx;
  const elevation = state?.elevation || null;

  const [seconds, setSeconds] = useState(elevation?.seconds_remaining ?? 0);
  const [seed, setSeed] = useState(elevation?.id);
  if (elevation?.id !== seed) {
    setSeed(elevation?.id);
    setSeconds(elevation?.seconds_remaining ?? 0);
  }
  useInterval(() => setSeconds((s) => Math.max(0, s - 1)), 1000, Boolean(elevation) && seconds > 0);

  const granted = elevation?.granted_scopes || [];
  const covers = granted.length ? granted.includes(scope) : elevation?.scope === scope;

  // "expired" is its own state, not a flavour of "none": the operator did elevate, it simply
  // ran out, so the console says so instead of pretending nothing happened.
  let status = "none";
  if (unknown || !ctx.state) status = "unknown";
  else if (elevation && seconds <= 0) status = "expired";
  else if (elevation && covers) status = "active";
  else if (elevation) status = "wrong_scope";

  const [elevating, setElevating] = useState(false);
  // Resolves to true when the operator now holds `scope`, false when the request succeeded
  // and they still do not.
  //
  // That second case is real, not defensive padding: POST /admin/elevation returns the
  // EXISTING grant rather than opening a second one, and services/support_access opens its
  // own elevation scoped to a support case. An engineer inside a support session therefore
  // clicks Elevate, gets 201 back, and is exactly as blocked as before. Reporting that as
  // success is how a control becomes a button that does nothing, so the caller is told.
  const elevate = useCallback(
    async (reason) => {
      setElevating(true);
      try {
        const { data } = await api.post("/admin/elevation", {
          scope,
          reason: reason || `${scope} action from the console`,
        });
        await reload?.();
        const now = data?.granted_scopes || [data?.scope, ...(data?.scopes || [])].filter(Boolean);
        return now.includes(scope);
      } finally {
        setElevating(false);
      }
    },
    [scope, reload]
  );

  return { status, ok: status === "active", seconds, scope, elevate, elevating, reload };
}
