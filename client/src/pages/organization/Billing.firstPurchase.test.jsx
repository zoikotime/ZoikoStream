// A first-time customer BUYING a plan — not upgrading one.
//
// ── THE BUG ─────────────────────────────────────────────────────────────────────────────
// An organization with no subscription saw "Upgrade" on Developer and Business, and the click
// was answered with:
//
//     This organization has no subscription record to upgrade
//
// Two separate faults met there. The page claimed the customer was moving up from a plan they
// did not have, and the checkout endpoint refused anyone without an existing row — so the
// first paid subscription was impossible for any organization that predated registration-time
// provisioning. The endpoint now self-heals through `provision_initial_subscription`
// (see server/test_first_subscription.py); this file pins the other half: the card says what
// is actually happening, and sends the click down the first-purchase path.
//
// The CTA matrix for this state:
//     Developer  -> Buy Plan
//     Business   -> Buy Plan
//     Enterprise -> Talk to an expert   (no approved Stripe price; quote-led)
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api", () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  errMsg: (e, fallback = "Something went wrong") => {
    const d = e?.response?.data?.detail;
    return typeof d === "string" ? d : (e?.message ?? fallback);
  },
  transportMessage: (e) => (e?.response ? null : "Couldn't reach the ZoikoStream server."),
  diagnoseLoadError: () => "Couldn't load billing information.",
}));
vi.mock("../../ui/Toast", () => ({ notify: { success: vi.fn(), error: vi.fn() } }));

import api from "../../api";
import { notify } from "../../ui/Toast";
import { ThemeProvider } from "../../theme/ThemeContext";
import OrganizationBilling from "./Billing";
import { planCta } from "./planPresentation";

const PLANS = [
  { id: "p-dev", slug: "developer", name: "Developer", pricing_state: "PUBLISHED",
    price_monthly: 49, features: ["1 concurrent stream", "720p", "Community support"],
    billing_intervals: ["monthly"], self_service: true },
  { id: "p-biz", slug: "business", name: "Business", pricing_state: "PUBLISHED",
    price_monthly: 249, features: ["5 concurrent streams", "1080p", "Recordings", "Email support"],
    billing_intervals: ["monthly"], self_service: true },
  { id: "p-ent", slug: "enterprise", name: "Enterprise", pricing_state: "CUSTOM",
    price_monthly: null, features: ["Unlimited streams", "4K", "SSO", "Dedicated support", "SLA"],
    billing_intervals: [], self_service: false },
];

const CHECKOUT_URL = "https://checkout.stripe.com/c/pay/cs_test_first";

// The reported state: no row, nothing locked, nothing entitled.
const NO_SUBSCRIPTION = {
  plan: null, plan_slug: null, status: null,
  checkout_locked: false, subscription_plan_slug: null, subscription_status: null, items: [],
};

let assigned;

async function mount(entitlements = {}, { post } = {}) {
  api.get.mockImplementation((url) => {
    if (url === "/organization/overview") {
      return Promise.resolve({
        data: { organization: { name: "Northwind" },
                entitlements: { ...NO_SUBSCRIPTION, ...entitlements } },
      });
    }
    if (url === "/organization/plans") return Promise.resolve({ data: PLANS });
    return Promise.reject(new Error(`unexpected GET ${url}`));
  });
  api.post.mockImplementation(post || (() => Promise.resolve({ data: { checkout_url: CHECKOUT_URL } })));
  render(
    <MemoryRouter>
      <ThemeProvider>
        <OrganizationBilling />
      </ThemeProvider>
    </MemoryRouter>
  );
  await screen.findByText("Available Plans");
}

const planCard = (name) => screen.getByText(name, { selector: "p" }).closest("div.flex.flex-col");
const cta = (name) => {
  const card = planCard(name);
  return within(card).queryByRole("button") || within(card).queryByRole("link");
};
const ctaText = (name) => cta(name)?.textContent?.trim();
const postsTo = (url) => api.post.mock.calls.filter(([u]) => u === url);

beforeEach(() => {
  vi.clearAllMocks();
  window.history.replaceState({}, "", "/organization/billing");
  assigned = [];
  Object.defineProperty(window, "location", {
    configurable: true,
    value: { ...window.location, assign: (url) => assigned.push(url), search: "" },
  });
});

describe("the CTA matrix with no subscription", () => {
  it("offers Buy Plan on Developer", async () => {
    await mount();
    expect(ctaText("Developer")).toBe("Buy Plan");
  });

  it("offers Buy Plan on Business", async () => {
    await mount();
    expect(ctaText("Business")).toBe("Buy Plan");
  });

  it("keeps Enterprise quote-led", async () => {
    await mount();
    expect(ctaText("Enterprise")).toBe("Talk to an expert");
  });

  it("never says Upgrade anywhere, because nothing exists to upgrade", async () => {
    await mount();
    for (const name of ["Developer", "Business", "Enterprise"]) {
      expect(ctaText(name)).not.toBe("Upgrade");
    }
  });

  it("marks no card as current", async () => {
    await mount();
    for (const name of ["Developer", "Business", "Enterprise"]) {
      expect(ctaText(name)).not.toBe("Current Plan");
    }
  });
});

describe("clicking Buy Plan starts a first purchase", () => {
  it("creates a Checkout Session for that plan", async () => {
    await mount();
    await userEvent.click(cta("Developer"));
    await waitFor(() => expect(postsTo("/organization/billing/checkout-session")).toHaveLength(1));
    expect(postsTo("/organization/billing/checkout-session")[0][1]).toEqual({
      plan_slug: "developer", billing_interval: "monthly",
    });
  });

  it("buys Business when Business is the card clicked", async () => {
    await mount();
    await userEvent.click(cta("Business"));
    await waitFor(() => expect(postsTo("/organization/billing/checkout-session")).toHaveLength(1));
    expect(postsTo("/organization/billing/checkout-session")[0][1].plan_slug).toBe("business");
  });

  it("NEVER calls the plan-change endpoint, which has no subscription to change", async () => {
    // This is the endpoint that produced "no subscription record to upgrade".
    await mount();
    await userEvent.click(cta("Developer"));
    await waitFor(() => expect(postsTo("/organization/billing/checkout-session")).toHaveLength(1));
    expect(postsTo("/organization/billing/plan-change")).toHaveLength(0);
  });

  it("sends the browser to the URL Stripe returned", async () => {
    await mount();
    await userEvent.click(cta("Developer"));
    await waitFor(() => expect(assigned).toEqual([CHECKOUT_URL]));
  });

  it("is one session per click, even on a double-click", async () => {
    await mount();
    const button = cta("Developer");
    await userEvent.click(button);
    await userEvent.click(button);
    await waitFor(() => expect(assigned).toHaveLength(1));
    expect(postsTo("/organization/billing/checkout-session")).toHaveLength(1);
  });

  it("shows progress on the card that was clicked", async () => {
    let release;
    await mount({}, { post: () => new Promise((r) => { release = r; }) });
    await userEvent.click(cta("Developer"));
    await waitFor(() => expect(ctaText("Developer")).toBe("Redirecting to Stripe…"));
    release({ data: { checkout_url: CHECKOUT_URL } });
  });
});

describe("a failed first purchase", () => {
  const refused = (status, detail) => () =>
    Promise.reject(Object.assign(new Error("failed"), { response: { status, data: { detail } } }));

  it("restores the Buy Plan button so it can be retried", async () => {
    await mount({}, { post: refused(503, "Payments are not configured") });
    await userEvent.click(cta("Developer"));
    await waitFor(() => expect(notify.error).toHaveBeenCalled());
    expect(cta("Developer")).not.toBeDisabled();
    expect(ctaText("Developer")).toBe("Buy Plan");
  });

  it("navigates nowhere when no session was created", async () => {
    await mount({}, { post: refused(503, "Payments are not configured") });
    await userEvent.click(cta("Developer"));
    await waitFor(() => expect(notify.error).toHaveBeenCalled());
    expect(assigned).toEqual([]);
  });
});

describe("the rule itself", () => {
  const dev = PLANS[0];
  const ent = PLANS[2];

  it("is a purchase when there is no current plan and nothing is locked", () => {
    expect(planCta(dev, null, { checkoutLocked: false }).kind).toBe("buy");
  });

  it("stays quote-led for a plan with no approved price", () => {
    expect(planCta(ent, null, { checkoutLocked: false }).kind).toBe("contact");
  });

  it("is still an upgrade once a plan is held", () => {
    // Buying and upgrading reach the same endpoint; they are different claims about state.
    expect(planCta(PLANS[1], "developer", { checkoutLocked: true }).kind).toBe("upgrade");
  });

  it("does not offer a purchase while a live subscription cannot be identified", () => {
    // The safeguard that stops a second Stripe subscription, unchanged.
    expect(planCta(dev, null, { checkoutLocked: true }).kind).toBe("contact");
  });
});
