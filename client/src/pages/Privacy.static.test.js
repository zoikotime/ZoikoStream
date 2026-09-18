import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

/** Static guards on the public privacy pages.
 *
 *  Same spirit as Trust.static.test.js and Status.static.test.js: these lock out whole
 *  classes of defect rather than the one instance a rendering test happens to drive.
 *  What is guarded here is the CONTRACT the privacy emails and the Play listing depend
 *  on:
 *
 *    • every privacy email CTA is a real, public route (a dead link in a live email),
 *    • the policy page is reachable without authentication (a Play requirement),
 *    • the Privacy Center never displays another request's data and never renders an
 *      operator action,
 *    • the platform's honest-limitation stance is not quietly edited away (no invented
 *      statutory deadline, no claimed total erasure).
 */
const read = (name) => readFileSync(resolve(process.cwd(), `src/pages/${name}`), "utf8");

// Strip comments first: these files explain in prose what they must NOT do, and a bare
// substring search would flag the explanation rather than a defect.
const strip = (source) => source
  .replace(/\/\*[\s\S]*?\*\//g, "")
  .replace(/\{\/\*[\s\S]*?\*\//g, "")
  .replace(/^\s*\/\/.*$/gm, "");

const POLICY = strip(read("PrivacyPolicy.jsx"));
const CENTER = strip(read("organization/PrivacyCenter.jsx"));
const APP = readFileSync(resolve(process.cwd(), "src/App.jsx"), "utf8");
const SERVER_ROUTER = readFileSync(
  resolve(process.cwd(), "../server/app/routers/privacy.py"), "utf8");
const SERVER_MODELS = readFileSync(
  resolve(process.cwd(), "../server/app/models/privacy.py"), "utf8");

describe("routes the privacy emails actually link to", () => {
  it("every emailed CTA path is a real route", () => {
    // services/privacy_comms.py builds: the Center itself, the verify link, the export
    // download, the notice page and the subprocessor list. A missing route here is a
    // dead link in a live email about somebody's own data.
    for (const path of [
      'path="/organization/privacy"',
      'path="/organization/privacy/requests/:requestId/verify"',
      'path="/organization/privacy/exports/:exportId/download"',
    ]) {
      expect(APP).toContain(path);
    }
  });

  it("the privacy policy is a real public route", () => {
    // Google Play requires a publicly reachable privacy policy for any app that ships;
    // the route must exist and must not be behind authentication.
    expect(APP).toContain('path="/privacy"');
  });

  it("none of them sits behind authentication", () => {
    // The public block in App.jsx is everything before the first login-gated route. A
    // Privacy Center that needs a session cannot serve the accountless requester the
    // service layer was deliberately written for.
    const publicBlock = APP.slice(APP.indexOf('path="/contact"'),
                                  APP.indexOf('path="/login"'));
    for (const path of ['path="/privacy"', 'path="/organization/privacy"']) {
      expect(publicBlock).toContain(path);
    }
  });

  it("the Privacy Center survives the mobile build too", () => {
    // HAS_CONSOLES strips the /organization/* CONSOLE routes from the store build. The
    // Privacy Center is not a console: emails point accountless requesters at it, and
    // the app itself must be able to answer a data request. Asserted on the build-flag
    // guard so moving the route inside the gate fails here.
    const gate = APP.indexOf("HAS_CONSOLES && (");
    const privacyIndex = APP.indexOf('path="/organization/privacy"');
    expect(gate).toBeGreaterThan(-1);
    expect(privacyIndex).toBeGreaterThan(-1);
    expect(privacyIndex).toBeLessThan(gate);
  });
});

describe("Privacy Center page", () => {
  it("requests are submitted to the public API surface", () => {
    expect(CENTER).toContain('api.post("/privacy/requests"');
    expect(CENTER).toContain('api.post("/privacy/requests/verify"');
    // The status lookup and the export download hit the GET routes, with the
    // customer-supplied part encoded.
    expect(CENTER).toContain("encodeURIComponent(reference.trim())");
    expect(CENTER).toContain("encodeURIComponent(exportId)");
    expect(CENTER).toContain('api.get(');
  });

  it("offers exactly the rights the platform can service", () => {
    // models/privacy.py REQUEST_TYPES — nothing more is promised. `restriction` and
    // `objection` are included because the platform can receive and answer them.
    for (const kind of ["access", "export", "deletion", "correction",
                        "restriction", "objection"]) {
      expect(CENTER).toContain(`"${kind}"`);
    }
    for (const invented of ["erasure", "portability", "gdpr_delete", "be-forgotten"]) {
      expect(CENTER).not.toContain(`"${invented}"`);
    }
  });

  it("never renders an operator action", () => {
    // Approve/extend/deny/execute are staff-side by design (routers/privacy.py has no
    // such route). The Center must not render a control the API would refuse.
    for (const action of ["Approve", "Deny request", "Extend deadline", "Execute deletion",
                          "Mark completed", "Force verify"]) {
      expect(CENTER).not.toContain(action);
    }
  });

  it("does not display the request's own free-text details", () => {
    // The status response has no `details` field (test_privacy_api.py asserts the schema);
    // the page must not try to render one either.
    expect(CENTER).not.toContain("found.details");
    expect(CENTER).not.toContain("request.details");
  });

  it("the server exposes no operator route on the public surface", () => {
    // The cross-check from the other side: the public router may not gain a route that
    // advances a request or executes a deletion.
    for (const forbidden in { "advance_status": 0, "execute_deletion": 0,
                              "approve_refund": 0, "extend_deadline": 0 }) {
      expect(SERVER_ROUTER).not.toContain(`@router.post` + `("${forbidden}`);
    }
    expect(SERVER_ROUTER).not.toContain("execute_deletion");
    expect(SERVER_ROUTER).not.toContain("advance_status");
  });
});

describe("Privacy policy page", () => {
  it("states only what the platform actually does", () => {
    // Grounded claims, each traceable to code: storage partitioning (capacitor
    // allowBackup=false), TLS-only (usesCleartextTraffic=false), single-use export
    // links, verification for export/deletion, no advertising.
    for (const claim of ["single-use links", "verify", "not stored by this app"]) {
      expect(POLICY).toContain(claim);
    }
    expect(POLICY.toLowerCase()).not.toContain("we sell your data");
  });

  it("never invents a statutory deadline or claims total erasure", () => {
    // DEADLINE_POLICIES is empty on purpose; deletion messages describe residue. The
    // policy page must not promise what the platform deliberately does not.
    expect(POLICY).not.toMatch(/\bwithin 30 days\b/i);
    expect(POLICY).not.toMatch(/\bdeleted (?:permanently|forever|entirely)\b/i);
    expect(POLICY).not.toMatch(/GDPR|CCPA|LGPD/);
  });

  it("links every actionable path instead of copying governed content", () => {
    // The processor list and the notice are versioned lifecycles served by the API; the
    // policy page links to them rather than embedding a copy that can drift.
    const linkTargets = (POLICY.match(/to="([^"]+)"/g) || []).join(" ");
    for (const target of ['to="/organization/privacy"', 'to="/trust"', 'to="/contact"']) {
      expect(linkTargets).toContain(target);
    }
  });
});

describe("the two pages agree with the service layer", () => {
  it("verify links are 72h-bound and download links expire within the hour", () => {
    // The constants the contract rests on, asserted where they are defined so a silent
    // change to either is visible in the same CI run that would catch a UI drift.
    expect(SERVER_MODELS).toContain("VERIFICATION_TTL_HOURS = 72");
    expect(SERVER_MODELS).toContain("DOWNLOAD_TTL_MINUTES = 60");
  });

  it("the Center is linked from the policy page and vice versa", () => {
    expect(POLICY).toContain('to="/organization/privacy"');
    expect(CENTER).toContain('to="/privacy"');
  });
});
