// Where a signed-in account lands in the MOBILE build.
//
// ── THE BUG THIS LOCKS OUT ───────────────────────────────────────────────────────────────
// The store build ships no /admin/* and no /organization/* routes (the HAS_CONSOLES gate in
// App.jsx). accountHome() previously answered "/organization/dashboard" for every non-platform
// role and "/admin/dashboard" for super_admin — both of which are, in that build, paths the
// router does not define.
//
// The consequence is worse than a 404 and is the reason this file exists. App.jsx sends an
// unmatched path to RootRedirect, RootRedirect asks accountHome() where to go, accountHome()
// answers with the same undefined path, and the router unmatches it again. That is an infinite
// redirect loop, and it would have caught EVERY account on its first launch — org_admin,
// billing_admin, viewer and super_admin alike. A phone showing a blank spinning page is what
// it would have looked like.
//
// The web build is asserted in auth/destination.test.jsx and must not change; these are the
// same functions under the other build flag.
import { describe, expect, it, vi } from "vitest";

vi.mock("../api", () => ({
  default: { get: vi.fn(), post: vi.fn() },
  AUTH_EXPIRED_EVENT: "zoiko:auth-expired",
  errMsg: (e) => e?.message ?? "Something went wrong.",
  errCode: () => null,
}));

// The whole point of the file: HAS_CONSOLES false, as `vite build --mode mobile` produces.
vi.mock("../platform", () => ({
  IS_NATIVE: true,
  HAS_CONSOLES: false,
  SUPPORTS_SCREEN_SHARE: false,
  HAS_BRIDGE: () => true,
  WEB_APP_URL: "https://get.zoikostream.com",
}));

import { accountHome, resolvePostLogin } from "../auth/destination";
import { roleHome } from "../auth/roleHome";

// Every role the backend can put on an account (models/user.ROLES), plus the retired
// "moderator" a session minted before retirement can still present.
const ROLES = [
  "super_admin", "org_admin", "billing_admin",
  "host", "moderator", "speaker", "viewer",
];

describe("accountHome in the mobile build", () => {
  it.each(ROLES)("sends %s to a route this build actually defines", (role) => {
    expect(accountHome(role)).toBe("/events/mine");
  });

  it("never answers with a console path", () => {
    // Stated as its own assertion because this is the loop condition, not a preference: a
    // console path here is a path the mobile router cannot match, and an unmatched path comes
    // straight back to this function.
    for (const role of [...ROLES, undefined, null, "something_new"]) {
      const home = accountHome(role);
      expect(home.startsWith("/admin/")).toBe(false);
      expect(home.startsWith("/organization/")).toBe(false);
    }
  });

  it("answers for an unknown role too", () => {
    // A role the backend adds later must not produce `undefined`, which RootRedirect would
    // hand to <Navigate to={undefined}>.
    expect(accountHome("role_that_does_not_exist_yet")).toBe("/events/mine");
    expect(accountHome(undefined)).toBe("/events/mine");
  });

  it("is what roleHome re-exports, so the dozen call sites agree", () => {
    expect(roleHome("org_admin")).toBe(accountHome("org_admin"));
  });
});

describe("resolvePostLogin in the mobile build", () => {
  it("falls back to the mobile home when there is no saved destination", async () => {
    expect(await resolvePostLogin({ role: "org_admin" }, null))
      .toEqual({ to: "/events/mine", reason: null });
  });

  it("still honours an ordinary saved page", async () => {
    // A deep link into the app's own surfaces survives a sign-in, exactly as on the web —
    // which is the case that matters most here, because on mobile the saved page is usually
    // the invitation that launched the app.
    const saved = { pathname: "/events/abc/watch", search: "" };

    expect(await resolvePostLogin({ role: "viewer" }, saved))
      .toEqual({ to: "/events/abc/watch", reason: null });
  });

  it("refuses an event console with no event id, as on the web", async () => {
    // The rule this shares with the web build: a console is not a destination without the
    // event it is a console FOR.
    const saved = { pathname: "/host/dashboard", search: "" };

    expect(await resolvePostLogin({ role: "host" }, saved))
      .toEqual({ to: "/events/mine", reason: null });
  });
});
