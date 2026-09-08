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

  it("performs no unsanctioned imperative navigation", () => {
    // These sinks remain forbidden outright — each one hands the click somewhere this page
    // does not control, which is how the mailto/GoDaddy handoff happened.
    for (const sink of ["location.replace", "location.href", "window.open"]) {
      expect(CODE).not.toContain(sink);
    }
  });

  it("navigates only to a checkout URL the backend returned", () => {
    // ONE navigation is sanctioned: the redirect to Stripe-hosted Checkout, which is the
    // required architecture (card details must be entered on Stripe, never here). The guard
    // that keeps this safe is not "no navigation" but "no navigation to anything we did not
    // just receive from our own API": the destination must be `data.checkout_url`, never a
    // literal URL in this file.
    const assigns = [...CODE.matchAll(/location\.assign\(([^)]*)\)/g)].map((m) => m[1].trim());
    expect(assigns).toEqual(["data.checkout_url"]);
    // No hardcoded external destination anywhere in the page.
    expect(CODE.match(/["'`]https?:\/\//g)).toBeNull();
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
  it("contains no Event Commercial (Ledger 2) payment logic", () => {
    // Ledger separation, unchanged in substance: this page bills the PLATFORM SUBSCRIPTION
    // (Ledger 1). Event-order payment belongs to the event's own Commercial tab and must never
    // appear here. What is forbidden is Ledger 2's endpoints and vocabulary — not the word
    // "checkout", which Ledger 1 legitimately needs for its own subscription purchase.
    // Scoped to Ledger 2 ENDPOINTS and object vocabulary. Deliberately not plain English
    // words like "invoice" or "refund": Section 16 requires this page to explain that invoices
    // live outside the Tenant Console, and that copy must stay sayable.
    for (const forbidden of ["/commercial", "payments/authorize", "event_order", "eventorder",
                             "event-order", "refund-credit", "refund_credit"]) {
      expect(CODE.toLowerCase()).not.toContain(forbidden);
    }
  });

  it("calls only organization subscription endpoints", () => {
    // Widened to cover api.post as well as api.get: the subscription checkout is a POST, and a
    // guard that only inspected reads would have missed it entirely.
    const calls = [...CODE.matchAll(/api\.(?:get|post|patch|put|delete)\(\s*["'`]([^"'`]+)/g)]
      .map((m) => m[1]);
    expect(calls.length).toBeGreaterThan(0);
    for (const path of calls) {
      expect(path.startsWith("/organization/")).toBe(true);
    }
  });

  it("never sends a price, amount or Stripe Price ID from the browser", () => {
    // The server resolves the approved price from configuration. The browser posts canonical
    // IDENTIFIERS only, so there is no field here through which a tampered request could
    // change what the customer is charged.
    //
    // `interval:` was on this list and is not any more. The approved price book publishes two
    // cadences per plan, so the page must be able to say WHICH — but a cadence is a closed
    // two-value vocabulary the server validates and resolves against its own price map, not a
    // price. It selects between amounts an operator already approved and cannot introduce one;
    // an unconfigured cadence is refused server-side rather than substituted. Everything that
    // could actually carry an amount is still forbidden, and more of it than before.
    for (const forbidden of ["price_id", "unit_amount", "amount:", "currency:", "coupon",
                             "discount", "unit_price", "trial_days", "trial_period_days"]) {
      expect(CODE).not.toContain(forbidden);
    }
    expect(CODE).toContain("plan_slug");
  });

  it("sends only a plan slug and a cadence in the checkout request", () => {
    // Pins the request body to exactly the two canonical identifiers. Anything else appearing
    // in this POST is what the rule above exists to catch, so assert the shape directly rather
    // than relying on a denylist of field names alone.
    const body = CODE.match(/checkout-session"[^)]*?\{([\s\S]*?)\}\s*\)/);
    expect(body).not.toBeNull();
    const keys = [...body[1].matchAll(/(\w+)\s*:/g)].map((m) => m[1]).sort();
    expect(keys).toEqual(["billing_interval", "plan_slug"]);
  });

  it("offers only cadences the server says are purchasable", () => {
    // The toggle must be driven by the server's `billing_intervals`, never by a hardcoded
    // list — otherwise a deployment with no annual price would still offer annual and every
    // click would 409.
    expect(CODE).toContain("billing_intervals");
  });
});

describe("Billing page describes the billing integration truthfully", () => {
  // These are not style rules. Stripe subscription billing is live, so the page's old
  // placeholder copy ("No payment provider connected", "Cards and billing details aren't
  // collected yet", "Invoice generation needs a connected payment provider, which isn't set up
  // yet") told a customer who had just paid by card the opposite of the truth. Comments are
  // stripped from CODE, so only text the user can actually see is checked here.
  it("never claims a payment provider is missing or unconnected", () => {
    for (const claim of [
      "No payment provider connected",
      "no payment provider",
      "isn't set up yet",
      "not set up yet",
    ]) {
      expect(CODE).not.toContain(claim);
    }
  });

  it("never claims card details are not collected", () => {
    for (const claim of ["aren't collected", "are not collected", "not collected yet"]) {
      expect(CODE).not.toContain(claim);
    }
  });

  it("never claims no invoices exist", () => {
    // A paying subscriber HAS invoices — Stripe issues one per billing period. The page may
    // say they are not listed here; it may not say they do not exist.
    expect(CODE).not.toContain("No invoices yet");
  });

  it("still refuses to invent card or invoice data it has no endpoint for", () => {
    // The honest position is "held by Stripe / emailed to you", not a fabricated table. There
    // is no payment-method or invoice endpoint, so no fetch for one may appear.
    for (const forbidden of ["payment-method", "payment_methods", "/invoices"]) {
      expect(CODE.toLowerCase()).not.toContain(forbidden);
    }
  });

  it("tells the customer that card entry happens on Stripe", () => {
    expect(CODE).toContain("Stripe");
  });
});

describe("scheduled plan change", () => {
  it("schedules a change instead of opening checkout for a paying tenant", () => {
    // Checkout CREATES a Stripe subscription; a paying tenant needs the one they have MOVED.
    // Sending them to checkout is what double-billed them.
    expect(CODE).toContain("/organization/billing/plan-change");
  });

  it("sends only a plan slug and cadence when scheduling", () => {
    const body = CODE.match(/plan-change"[^)]*?\{([\s\S]*?)\}\s*\)/);
    expect(body).not.toBeNull();
    const keys = [...body[1].matchAll(/(\w+)\s*:/g)].map((m) => m[1]).sort();
    expect(keys).toEqual(["billing_interval", "plan_slug"]);
  });

  it("computes no effective date of its own", () => {
    // The date is the one the backend promised the customer. Deriving it here could show a
    // different date than the one that will actually be honoured.
    for (const forbidden of ["addMonths", "setMonth", "Date.now()", "new Date(Date"]) {
      expect(CODE).not.toContain(forbidden);
    }
    expect(CODE).toContain("plan_change_effective_at");
  });

  it("renders the pending change from backend state only", () => {
    expect(CODE).toContain("pending_plan_slug");
    expect(CODE).toContain("pending_billing_interval");
  });

  it("re-reads state from the backend after scheduling rather than assuming success", () => {
    expect(CODE).toContain("reloadOverview");
  });

  it("offers no immediate-change control", () => {
    // Every paid change is scheduled. A label promising an instant switch would misdescribe
    // what the button does.
    expect(CODE).not.toContain("Change now");
    expect(CODE).not.toContain("Apply immediately");
  });

  it("blocks a second change while one is already pending", () => {
    expect(CODE).toContain("Boolean(pending)");
  });
});
