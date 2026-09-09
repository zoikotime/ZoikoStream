import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

/** Static guards on the public status page (ZST-EC-001 STS-001 -> STS-006).
 *
 *  Structural, in the same spirit as Contact.static.test.js: these lock out whole classes of
 *  defect rather than the one instance a rendering test happens to drive. The guarantees the
 *  spec singles out are backend guarantees, and this page must not undo them in the browser.
 */
const SOURCE = readFileSync(resolve(process.cwd(), "src/pages/Status.jsx"), "utf8");
const APP = readFileSync(resolve(process.cwd(), "src/App.jsx"), "utf8");

// Strip comments first: the file's own prose explains what it must not do, and a bare
// substring search would flag the explanation.
const CODE = SOURCE.replace(/\/\*[\s\S]*?\*\//g, "")
  .replace(/\{\/\*[\s\S]*?\*\/\}/g, "")
  .replace(/^\s*\/\/.*$/gm, "");

describe("public status page", () => {
  it("is routed at /status, which is what every status email links to", () => {
    // email.py's status_page_url() is `{base}/status`. Without this route the CTA in every
    // STS message lands on the SPA's catch-all redirect instead of the status page.
    expect(APP).toContain('path="/status"');
    expect(APP).toContain('from "./pages/Status"');
  });

  it("reads published status only", () => {
    expect(CODE).toContain('api.get("/status")');
    // The internal incident record and the admin surfaces are not reachable from here.
    for (const forbidden of ["/admin/", "/ops/", "platform_ops", "internal_incident",
                             "/incidents"]) {
      expect(CODE).not.toContain(forbidden);
    }
  });

  it("renders no internal or investigative field", () => {
    // These are the field names the internal Incident carries. The public projection does
    // not send them, and this page must not start asking for them either.
    for (const field of ["commander", "severity", "root_cause", "detail", "evidence",
                         "monitoring_data", "sev1", "sev2"]) {
      expect(CODE).not.toContain(field);
    }
  });

  it("renders the full append-only history rather than only the latest version", () => {
    // A correction appends a version pointing back at what it corrects; both stay published.
    // Rendering only the newest entry would hide the corrected statement the server kept.
    expect(CODE).toContain("incident.history");
    expect(CODE).toContain("corrects_version");
    expect(CODE).toContain("Later corrected");
  });

  it("keeps a reopening visible", () => {
    expect(CODE).toContain("reopened_at");
    expect(CODE).toContain("Reopened");
  });

  it("distinguishes emergency maintenance from scheduled maintenance", () => {
    expect(CODE).toContain('w.kind === "emergency"');
    expect(CODE).toContain("Emergency");
    expect(CODE).toContain("Scheduled");
  });

  it("shows a recorded start rather than assuming the clock reached the window", () => {
    // `started_at` is written when work actually begins. Deriving "started" from
    // starts_at_utc vs. now() in the browser would reintroduce exactly the claim the
    // backend refuses to make.
    expect(CODE).toContain("w.started_at");
    expect(CODE).not.toMatch(/starts_at_utc\s*[<>]/);
    expect(CODE).not.toMatch(/Date\.now\(\)\s*[<>]/);
  });

  it("labels maintenance times as UTC and preserves a revised window", () => {
    expect(CODE).toContain("starts_at_utc");
    expect(CODE).toContain("previous_starts_at_utc");
    expect(CODE).toContain("UTC");
  });

  it("promises a next update only when one was published", () => {
    expect(CODE).toContain("incident.next_update_at");
    // No fabricated cadence anywhere in the page.
    for (const invented of ["30 minutes", "every hour", "within an hour", "shortly after"]) {
      expect(CODE.toLowerCase()).not.toContain(invented);
    }
  });

  it("does not claim health it has not been told about", () => {
    // "All systems operational" is rendered only for an explicit overall of "none", which
    // the server derives from the components' own recorded impact.
    expect(CODE).toContain('data.overall === "none"');
    // And a failed load says so rather than falling back to a reassuring green.
    expect(CODE).toContain("failed");
    expect(CODE).toContain("can&rsquo;t load the status page");
  });

  it("subscribes through the double opt-in endpoints and reveals nothing about an address", () => {
    expect(CODE).toContain('api.post("/status/subscribe"');
    expect(CODE).toContain("/status/subscribe/confirm");
    // No branch on "already subscribed" — the endpoint deliberately never says.
    expect(CODE.toLowerCase()).not.toContain("already subscribed");
  });

  it("strips subscription tokens from the URL after redeeming them", () => {
    expect(CODE).toContain('next.delete("confirm")');
    expect(CODE).toContain('next.delete("t")');
  });

  it("says unsubscribing touches status mail only", () => {
    expect(CODE).toContain("/status/unsubscribe");
    expect(CODE).toContain("Account, security, billing and privacy emails");
  });

  it("presents itself as an operational channel, not a marketing list", () => {
    expect(CODE).toContain("not a marketing list");
    for (const term of ["newsletter", "promotions", "product updates", "offers"]) {
      expect(CODE.toLowerCase()).not.toContain(term);
    }
  });

  it("stores no token in the browser", () => {
    // The manage handle lives in component state for the life of the visit and nowhere else:
    // a status page is a link people forward, and a persisted handle would travel with it.
    for (const sink of ["localStorage", "sessionStorage", "document.cookie"]) {
      expect(CODE).not.toContain(sink);
    }
  });
});
