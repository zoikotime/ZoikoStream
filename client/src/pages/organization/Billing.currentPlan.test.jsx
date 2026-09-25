// The Billing page's current-plan detection, and the regression that made it necessary.
//
// THE BUG: an organization that had already paid still saw "Upgrade" on every plan card, and
// every click came back 409.
//
// WHY: Stripe's checkout.session.completed binds the subscription id and moves our row to
// `conversion_pending`; only customer.subscription.created advances it to `active`. Between
// those two webhooks — which, locally, is forever if webhooks are not being forwarded — the row
//   * HAS a stripe_subscription_id, so the checkout endpoint's guard refuses a second one, and
//   * is NOT in SUBSCRIPTION_ENTITLED_STATES, so entitlements() reports plan/plan_slug/status
//     all null.
// The page read that as "no subscription at all" and offered the purchase path. The fix is that
// the backend now reports `checkout_locked` — the guard's OWN predicate — and the page asks that
// instead of re-deriving the answer from a status list it kept in sync by hand.
//
// These tests drive the page through the exact payload entitlements() produces in that window.
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api", () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  errMsg: (e) => e?.message ?? "Something went wrong.",
}));

vi.mock("../../ui/Toast", () => ({
  notify: { success: vi.fn(), error: vi.fn() },
}));

import api from "../../api";
import { ThemeProvider } from "../../theme/ThemeContext";
import OrganizationBilling from "./Billing";

const PLANS = [
  {
    id: "p-dev", slug: "developer", name: "Developer", pricing_state: "PUBLISHED",
    price_monthly: 49, features: ["1 concurrent stream"],
    billing_intervals: ["monthly"], self_service: true,
  },
  {
    id: "p-biz", slug: "business", name: "Business", pricing_state: "PUBLISHED",
    price_monthly: 249, features: ["5 concurrent streams"],
    billing_intervals: ["monthly"], self_service: true,
  },
  {
    id: "p-ent", slug: "enterprise", name: "Enterprise", pricing_state: "CUSTOM",
    price_monthly: null, features: ["Unlimited streams"],
    billing_intervals: [], self_service: false,
  },
];

const overviewFor = (entitlements) => ({
  organization: { name: "Northwind" },
  entitlements: {
    plan: null, plan_slug: null, status: null,
    checkout_locked: false, subscription_plan_slug: null, subscription_status: null,
    items: [], ...entitlements,
  },
});

function mountBilling() {
  render(
    <MemoryRouter>
      <ThemeProvider>
        <OrganizationBilling />
      </ThemeProvider>
    </MemoryRouter>
  );
}

async function renderBilling(entitlements) {
  api.get.mockImplementation((url) => {
    if (url === "/organization/overview") {
      return Promise.resolve({ data: overviewFor(entitlements) });
    }
    if (url === "/organization/plans") return Promise.resolve({ data: PLANS });
    return Promise.reject(new Error(`unexpected GET ${url}`));
  });
  // Default: the repair endpoint answers "nothing changed". Individual tests override it.
  api.post.mockImplementation((url) => (
    url === "/organization/billing/sync"
      ? Promise.resolve({ data: { synced: false, outcome: "current" } })
      : Promise.resolve({ data: {} })
  ));
  mountBilling();
  await screen.findByText("Available Plans");
}

/** api.post calls that are not the unidentified-subscription repair. */
const nonSyncPosts = () =>
  api.post.mock.calls.filter(([url]) => url !== "/organization/billing/sync");

const planCard = (name) => screen.getByText(name, { selector: "p" }).closest("div.flex.flex-col");

function ctaText(name) {
  const card = planCard(name);
  const el = within(card).queryByRole("button") || within(card).queryByRole("link");
  return el?.textContent?.trim();
}

beforeEach(() => {
  vi.clearAllMocks();
  window.history.replaceState({}, "", "/organization/billing");
});

// The exact entitlements() payload for a paid-but-not-yet-activated subscription: every
// entitled field null, because `_plan()` excludes conversion_pending — with checkout_locked
// true, because the guard does not.
const CONVERSION_PENDING = {
  plan: null, plan_slug: null, status: null,
  checkout_locked: true,
  subscription_plan_slug: "developer", subscription_status: "conversion_pending",
};

describe("A paid subscription Stripe has not activated yet", () => {
  it("never offers Upgrade on any card", async () => {
    await renderBilling(CONVERSION_PENDING);
    for (const plan of ["Developer", "Business", "Enterprise"]) {
      expect(ctaText(plan)).not.toBe("Upgrade");
    }
  });

  it("says a subscription was detected rather than implying there is none", async () => {
    await renderBilling(CONVERSION_PENDING);
    expect(screen.getByRole("status")).toHaveTextContent(/current subscription detected/i);
  });

  it("names no Stripe identifier anywhere in the UI", async () => {
    await renderBilling(CONVERSION_PENDING);
    expect(document.body.textContent).not.toMatch(/\b(sub|cus|price|cs_test|cs_live)_/);
  });

  it("cannot reach the checkout endpoint from any card", async () => {
    await renderBilling(CONVERSION_PENDING);
    for (const plan of ["Developer", "Business", "Enterprise"]) {
      const btn = within(planCard(plan)).queryByRole("button");
      if (btn) await userEvent.click(btn, { pointerEventsCheck: 0 });
    }
    expect(nonSyncPosts()).toHaveLength(0);
  });

  it("does not guess a tier from the unactivated subscription's plan", async () => {
    await renderBilling(CONVERSION_PENDING);
    // subscription_plan_slug says "developer", but the backend is deliberately WITHHOLDING
    // entitlement to it. Rendering "Current Plan" would assert what Section 18 refuses to.
    expect(ctaText("Developer")).not.toBe("Current Plan");
  });
});

describe("checkout_locked decides purchasability, not a hand-kept status list", () => {
  it("routes an upgrade through plan-change whenever the provider subscription is live", async () => {
    api.post.mockResolvedValue({ data: {} });
    // `conversion_pending` appears in none of the old
    // ["active","past_due","plan_change_scheduled"] list, so the previous code would have
    // called checkout here and taken the 409.
    await renderBilling({
      plan: "Developer", plan_slug: "developer", status: "conversion_pending",
      checkout_locked: true,
    });

    await userEvent.click(within(planCard("Business")).getByRole("button"));

    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(1));
    expect(api.post.mock.calls[0][0]).toBe("/organization/billing/plan-change");
    expect(api.post).not.toHaveBeenCalledWith(
      "/organization/billing/checkout-session", expect.anything()
    );
  });

  it("still offers a real purchase when no provider subscription exists", async () => {
    api.post.mockResolvedValue({
      data: { checkout_url: "https://checkout.stripe.com/c/pay/test" },
    });
    await renderBilling({ plan: null, plan_slug: null, status: null, checkout_locked: false });

    expect(ctaText("Developer")).toBe("Upgrade");
    await userEvent.click(within(planCard("Developer")).getByRole("button"));

    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(1));
    expect(api.post.mock.calls[0][0]).toBe("/organization/billing/checkout-session");
  });

  it("falls back to the old status rule when an older backend omits the field", async () => {
    // The fallback must fail in the SAFE direction: treating a paying tenant as unsubscribed
    // is the one that offers a duplicate purchase.
    api.post.mockResolvedValue({ data: {} });
    await renderBilling({
      plan: "Developer", plan_slug: "developer", status: "active", checkout_locked: undefined,
    });
    await userEvent.click(within(planCard("Business")).getByRole("button"));
    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(1));
    expect(api.post.mock.calls[0][0]).toBe("/organization/billing/plan-change");
  });

  it("shows no detection notice when the plan is known", async () => {
    await renderBilling({
      plan: "Business", plan_slug: "business", status: "active", checkout_locked: true,
    });
    expect(ctaText("Business")).toBe("Current Plan");
    expect(screen.queryByText(/current subscription detected/i)).toBeNull();
  });
});

describe("Returning from Stripe with a confirmed payment", () => {
  it("re-reads entitlements so the card flips without a manual reload", async () => {
    window.history.replaceState(
      {}, "", "/organization/billing?checkout=success&session_id=cs_test_9");

    let overviewCalls = 0;
    api.get.mockImplementation((url) => {
      if (url === "/organization/overview") {
        overviewCalls += 1;
        // First read: the webhook had not landed yet. Second: it has.
        return Promise.resolve({
          data: overviewFor(overviewCalls === 1
            ? {}
            : {
                plan: "Business", plan_slug: "business", status: "active",
                checkout_locked: true,
              }),
        });
      }
      if (url === "/organization/plans") return Promise.resolve({ data: PLANS });
      if (url === "/organization/billing/checkout-status") {
        return Promise.resolve({ data: { state: "confirmed", plan: "Business" } });
      }
      return Promise.reject(new Error(`unexpected GET ${url}`));
    });

    mountBilling();

    await waitFor(() => expect(ctaText("Business")).toBe("Current Plan"));
    expect(overviewCalls).toBeGreaterThan(1);
  });

  it("does not re-read while the payment is still unconfirmed", async () => {
    window.history.replaceState(
      {}, "", "/organization/billing?checkout=success&session_id=cs_test_8");

    let overviewCalls = 0;
    api.get.mockImplementation((url) => {
      if (url === "/organization/overview") {
        overviewCalls += 1;
        return Promise.resolve({
          data: overviewFor({ plan: "Developer", plan_slug: "developer", status: "active" }),
        });
      }
      if (url === "/organization/plans") return Promise.resolve({ data: PLANS });
      if (url === "/organization/billing/checkout-status") {
        return Promise.resolve({ data: { state: "pending" } });
      }
      return Promise.reject(new Error(`unexpected GET ${url}`));
    });

    mountBilling();

    await screen.findByText("Available Plans");
    await waitFor(() => expect(screen.getByRole("status")).toBeTruthy());
    // Re-reading here would just refetch the same unchanged state on every poll.
    expect(overviewCalls).toBe(1);
  });
});

// ── Resolving a subscription the webhook never activated ─────────────────────────────────
// The repair for the root cause: rather than showing "confirming…" forever, the page asks the
// backend to re-read the subscription from Stripe. See services/subscription_sync.
describe("Resolving an unidentified subscription against Stripe", () => {
  const stranded = { plan: null, plan_slug: null, status: null, checkout_locked: true };
  const resolved = {
    plan: "Developer", plan_slug: "developer", status: "active", checkout_locked: true,
  };

  /** First overview read is stranded; later reads report whatever `after` says. */
  function mockRecovery(after, syncResponse) {
    let overviewCalls = 0;
    api.get.mockImplementation((url) => {
      if (url === "/organization/overview") {
        overviewCalls += 1;
        return Promise.resolve({
          data: overviewFor(overviewCalls === 1 ? stranded : after),
        });
      }
      if (url === "/organization/plans") return Promise.resolve({ data: PLANS });
      return Promise.reject(new Error(`unexpected GET ${url}`));
    });
    api.post.mockImplementation((url) => (
      url === "/organization/billing/sync"
        ? Promise.resolve({ data: syncResponse })
        : Promise.resolve({ data: {} })
    ));
    return () => overviewCalls;
  }

  it("asks the backend to re-read Stripe when it cannot identify the subscription", async () => {
    mockRecovery(resolved, { synced: true, outcome: "applied", status: "active" });
    mountBilling();
    await screen.findByText("Available Plans");
    await waitFor(() => expect(
      api.post.mock.calls.some(([url]) => url === "/organization/billing/sync")
    ).toBe(true));
  });

  it("shows the real plan once the sync resolves it", async () => {
    mockRecovery(resolved, { synced: true, outcome: "applied", status: "active" });
    mountBilling();
    await waitFor(() => expect(ctaText("Developer")).toBe("Current Plan"));
    expect(ctaText("Business")).toBe("Upgrade");
    expect(ctaText("Enterprise")).toBe("Talk to an expert");
    // The pending notice is gone, because nothing is pending any more.
    expect(screen.queryByText(/current subscription detected/i)).toBeNull();
  });

  it("resolves a Business subscription to the Business card", async () => {
    mockRecovery(
      { plan: "Business", plan_slug: "business", status: "active", checkout_locked: true },
      { synced: true, outcome: "applied", status: "active" },
    );
    mountBilling();
    await waitFor(() => expect(ctaText("Business")).toBe("Current Plan"));
    expect(ctaText("Developer")).toBe("Contact us to downgrade");
    expect(ctaText("Enterprise")).toBe("Talk to an expert");
  });

  it("shows Stripe's real reason instead of a pending state that cannot clear", async () => {
    mockRecovery(stranded, {
      synced: false, outcome: "unmappable",
      error: "Your payment hasn't completed at Stripe yet.",
    });
    mountBilling();
    await screen.findByText("Available Plans");
    await waitFor(() => expect(screen.getByRole("status"))
      .toHaveTextContent(/payment hasn't completed at Stripe yet/i));
    // And it stops claiming a confirmation is still running.
    expect(screen.getByRole("status")).not.toHaveTextContent(/We're confirming it with Stripe/i);
  });

  it("surfaces a transport failure rather than spinning", async () => {
    mockRecovery(stranded, null);
    api.post.mockImplementation((url) => (
      url === "/organization/billing/sync"
        ? Promise.reject(new Error("Network Error"))
        : Promise.resolve({ data: {} })
    ));
    mountBilling();
    await screen.findByText("Available Plans");
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent(/Network Error/i));
  });

  it("tries once, not on every render", async () => {
    mockRecovery(resolved, { synced: true, outcome: "applied", status: "active" });
    mountBilling();
    await waitFor(() => expect(ctaText("Developer")).toBe("Current Plan"));
    const syncCalls = api.post.mock.calls.filter(
      ([url]) => url === "/organization/billing/sync");
    expect(syncCalls).toHaveLength(1);
  });

  it("never asks for a sync when the plan is already known", async () => {
    await renderBilling({
      plan: "Business", plan_slug: "business", status: "active", checkout_locked: true,
    });
    expect(api.post.mock.calls.filter(([u]) => u === "/organization/billing/sync")).toHaveLength(0);
  });

  it("never asks for a sync when there is no subscription at all", async () => {
    await renderBilling({ plan: null, plan_slug: null, status: null, checkout_locked: false });
    expect(api.post.mock.calls.filter(([u]) => u === "/organization/billing/sync")).toHaveLength(0);
  });

  it("never opens a checkout as part of resolving", async () => {
    mockRecovery(resolved, { synced: true, outcome: "applied", status: "active" });
    mountBilling();
    await waitFor(() => expect(ctaText("Developer")).toBe("Current Plan"));
    expect(api.post).not.toHaveBeenCalledWith(
      "/organization/billing/checkout-session", expect.anything());
  });
});
