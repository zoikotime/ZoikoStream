// Where a signed-in account lands in the MOBILE build.
//
// ── THE BUG THIS LOCKS OUT ───────────────────────────────────────────────────────────────
// accountHome() must never name a path the RUNNING BUILD does not define. Which paths those
// are has changed — the organization console now ships natively (HAS_ORG_CONSOLE), while the
// platform console still does not (HAS_ADMIN_CONSOLE) — but the rule has not, and it is the
// rule rather than the path list that this file exists to hold.
//
// The consequence is worse than a 404 and is the reason this file exists. App.jsx sends an
// unmatched path to RootRedirect, RootRedirect asks accountHome() where to go, accountHome()
// answers with the same undefined path, and the router unmatches it again. That is an infinite
// redirect loop, and it would have caught EVERY account on its first launch — org_admin,
// billing_admin, viewer and super_admin alike. A phone showing a blank spinning page is what
// it would have looked like.
//
// The web build is asserted in auth/destination.test.jsx and must not change; these are the
// same functions under the other build flags.
import { describe, expect, it, vi } from "vitest";

vi.mock("../api", () => ({
  default: { get: vi.fn(), post: vi.fn() },
  AUTH_EXPIRED_EVENT: "zoiko:auth-expired",
  errMsg: (e) => e?.message ?? "Something went wrong.",
  errCode: () => null,
}));

// The flags exactly as `vite build --mode mobile` produces them. Both consoles now ship, so
// the destinations match the web build; SUPPORTS_IN_APP_CHECKOUT is the one that still
// differs, and it is a payments-policy decision rather than a routing one.
//
// Mocked literally rather than approximately. A test that keeps asserting a flag combination
// the build no longer produces stays green while guarding nothing, which is worse than having
// no test at all — it reports that a build was checked when it was not.
vi.mock("../platform", () => ({
  IS_NATIVE: true,
  HAS_ORG_CONSOLE: true,
  HAS_ADMIN_CONSOLE: true,
  HAS_CONSOLES: true,
  SUPPORTS_IN_APP_CHECKOUT: false,
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
  it.each(ROLES.filter((r) => r !== "super_admin"))(
    "sends %s to a route this build actually defines", (role) => {
      expect(accountHome(role)).toBe("/organization/dashboard");
    });

  it("sends super_admin to the platform console, which this build now carries", () => {
    // The assertion that changed when /admin/* started shipping natively. It previously read
    // /organization/dashboard, because the platform console was absent and sending an operator
    // to a route the router could not match was an infinite redirect rather than a 404.
    expect(accountHome("super_admin")).toBe("/admin/dashboard");
  });

  it("only ever answers with a route the native router defines", () => {
    // The rule this file exists for, and the only form of it that survives both consoles
    // shipping. It is stated as a whitelist rather than as a list of forbidden prefixes
    // because the failure is "names something absent", and a blacklist can only ever rule out
    // the absences somebody already thought of.
    //
    // App.jsx defines both of these natively now. If a build target ever drops one again,
    // this is what should fail.
    const ROUTABLE = new Set(["/admin/dashboard", "/organization/dashboard"]);
    for (const role of [...ROLES, undefined, null, "something_new"]) {
      expect(ROUTABLE.has(accountHome(role))).toBe(true);
    }
  });

  it("answers for an unknown role too", () => {
    // A role the backend adds later must not produce `undefined`, which RootRedirect would
    // hand to <Navigate to={undefined}>.
    // An unknown role is not a platform operator, so it falls to the organization console.
    expect(accountHome("role_that_does_not_exist_yet")).toBe("/organization/dashboard");
    expect(accountHome(undefined)).toBe("/organization/dashboard");
  });

  it("is what roleHome re-exports, so the dozen call sites agree", () => {
    expect(roleHome("org_admin")).toBe(accountHome("org_admin"));
  });
});

describe("resolvePostLogin in the mobile build", () => {
  it("falls back to the account's console when there is no saved destination", async () => {
    expect(await resolvePostLogin({ role: "org_admin" }, null))
      .toEqual({ to: "/organization/dashboard", reason: null });
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
      .toEqual({ to: "/organization/dashboard", reason: null });
  });
});
