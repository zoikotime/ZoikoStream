import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

/** Static guard on the organization Billing page's outbound links.
 *
 *  Why static rather than a runtime allowlist: after the fix this page has NO external links at
 *  all, so an allowlist would be a guard with nothing to guard. What actually needs enforcing is
 *  that none reappear — and that is a property of the source, checkable without mocking the two
 *  API calls the page makes. Same idea as the backend's code_only() structural assertions.
 *
 *  The bug this locks down: the plan cards used `mailto:support@zoikostream.com`. A mailto: hands
 *  the click to whatever mail handler the OS has registered, and for a GoDaddy-hosted domain that
 *  is sso.secureserver.net. From inside the product it looked like ZoikoStream had redirected the
 *  operator to a GoDaddy sign-in page.
 */
// Resolved from the project root (vitest's cwd) — import.meta.url is not a file: URL here.
const SOURCE = readFileSync(
  resolve(process.cwd(), "src/pages/organization/Billing.jsx"),
  "utf8",
);

// Strip comments so the prose explaining these rules cannot satisfy or trip them.
const CODE = SOURCE.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");

describe("Billing page outbound links", () => {
  it("uses no mailto: link", () => {
    expect(CODE).not.toContain("mailto:");
  });

  it("hands off to no external origin", () => {
    // Any absolute URL in a link would leave the application. There should be none.
    expect(CODE.match(/href=["'`]https?:\/\//g)).toBeNull();
  });

  it("performs no imperative navigation", () => {
    for (const sink of ["window.location", "location.assign", "location.replace",
                        "location.href", "window.open"]) {
      expect(CODE).not.toContain(sink);
    }
  });

  it("never reaches a GoDaddy or secureserver domain", () => {
    for (const domain of ["secureserver", "godaddy", "sso."]) {
      expect(CODE.toLowerCase()).not.toContain(domain);
    }
  });

  it("routes contact actions through the in-app contact page", () => {
    expect(CODE).toContain('to="/contact"');
  });

  // Ledger separation: organization subscription billing must not grow event-order payment
  // logic. Event order payments live on the event's own Commercial tab.
  it("contains no Event Commercial payment logic", () => {
    for (const forbidden of ["/commercial", "checkout-session", "payments/authorize",
                             "checkout_url", "stripe"]) {
      expect(CODE.toLowerCase()).not.toContain(forbidden);
    }
  });

  it("calls only organization subscription endpoints", () => {
    const calls = [...CODE.matchAll(/api\.get\(\s*["'`]([^"'`]+)/g)].map((m) => m[1]);
    expect(calls.length).toBeGreaterThan(0);
    for (const path of calls) {
      expect(path.startsWith("/organization/")).toBe(true);
    }
  });
});
