// Available Plans on the organization Billing page: which tiers are offered, and what each
// card's call-to-action does.
//
// What these pin, in order of what would actually hurt if it regressed:
//   1. the retired Starter/Pro tiers are never OFFERED — but an organization still on one
//      keeps seeing its own plan, because hiding what somebody pays for is worse than showing
//      a tier nobody else can buy
//   2. the current plan comes from the BACKEND's entitlements.plan_slug, never from price text
//   3. a downgrade never reaches Stripe — no checkout, no plan-change, just the contact route
//   4. an upgrade still uses the existing server-resolved path, and an org that ALREADY pays
//      is never sent to checkout (which would open a second Stripe subscription and bill twice)
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

// Shape copied from the real GET /organization/plans payload (routers/organization.py::
// list_plans -> PlanOut + billing_intervals/self_service). `self_service` is SERVER-set from
// whether an approved Stripe price exists, which is why enterprise is false here.
//
// Deliberately includes the retired starter/pro rows AND returns them out of hierarchy order:
// both are true of a deployment where migrate_plan_names.py has not been run, which is the
// state the screenshots were taken in.
const PLANS = [
  {
    id: "p-dev", slug: "developer", name: "Developer", pricing_state: "PUBLISHED",
    price_monthly: 49, features: ["1 concurrent stream", "720p", "Community support"],
    billing_intervals: ["monthly"], self_service: true,
  },
  {
    id: "p-biz", slug: "business", name: "Business", pricing_state: "PUBLISHED",
    price_monthly: 249, features: ["5 concurrent streams", "1080p", "Recordings"],
    billing_intervals: ["monthly"], self_service: true,
  },
  {
    id: "p-ent", slug: "enterprise", name: "Enterprise", pricing_state: "CUSTOM",
    price_monthly: null, features: ["Unlimited streams", "4K", "SSO"],
    billing_intervals: [], self_service: false,
  },
  {
    id: "p-pro", slug: "pro", name: "Pro", pricing_state: "NOT_PUBLISHED",
    price_monthly: null, features: ["5 concurrent streams", "1080p"],
    billing_intervals: [], self_service: false,
  },
  {
    id: "p-start", slug: "starter", name: "Starter", pricing_state: "NOT_PUBLISHED",
    price_monthly: null, features: ["1 concurrent stream", "720p"],
    billing_intervals: [], self_service: false,
  },
];

const overviewFor = (entitlements) => ({
  organization: { name: "Northwind" },
  entitlements: {
    plan: null, plan_slug: null, status: "active",
    // `checkout_locked` is services/org.py's report of the checkout endpoint's own 409
    // predicate (has_live_provider_subscription). Defaulted false = no live Stripe
    // subscription, which is the only state in which a new checkout may be offered.
    checkout_locked: false, subscription_plan_slug: null, subscription_status: null,
    usage: [], ...entitlements,
  },
});

/** Renders Billing with the backend reporting `entitlements`, and waits for both GETs. */
async function renderBilling(entitlements) {
  api.get.mockImplementation((url) => {
    if (url === "/organization/overview") return Promise.resolve({ data: overviewFor(entitlements) });
    if (url === "/organization/plans") return Promise.resolve({ data: PLANS });
    return Promise.reject(new Error(`unexpected GET ${url}`));
  });
  render(
    <MemoryRouter>
      <ThemeProvider>
        <OrganizationBilling />
      </ThemeProvider>
    </MemoryRouter>
  );
  // The plan grid only exists once both requests have resolved.
  await screen.findByText("Available Plans");
}

/** The one plan card whose title is `name`. Scoped so "Business" in a heading or a scheduled-
 *  change notice can never satisfy an assertion meant for the card. */
function planCard(name) {
  const title = screen.getByText(name, { selector: "p" });
  return title.closest("div.flex.flex-col");
}

/** The card's call-to-action, whether it rendered as a <button> or a <Link>. */
function ctaText(name) {
  const card = planCard(name);
  const el = within(card).queryByRole("button") || within(card).queryByRole("link");
  return el?.textContent?.trim();
}

beforeEach(() => {
  vi.clearAllMocks();
  window.history.replaceState({}, "", "/organization/billing");
});

describe("Available Plans — which tiers are offered", () => {
  it("does not render the retired Pro plan", async () => {
    await renderBilling({ plan: "Developer", plan_slug: "developer" });
    expect(screen.queryByText("Pro", { selector: "p" })).toBeNull();
  });

  it("does not render the retired Starter plan", async () => {
    await renderBilling({ plan: "Developer", plan_slug: "developer" });
    expect(screen.queryByText("Starter", { selector: "p" })).toBeNull();
  });

  it("renders Developer, Business and Enterprise, and nothing else", async () => {
    await renderBilling({ plan: "Developer", plan_slug: "developer" });
    expect(planCard("Developer")).toBeTruthy();
    expect(planCard("Business")).toBeTruthy();
    expect(planCard("Enterprise")).toBeTruthy();
    // Exactly three cards — no empty card left where a filtered plan used to be.
    const grid = planCard("Developer").parentElement;
    expect(grid.children).toHaveLength(3);
  });

  it("orders the cards Developer -> Business -> Enterprise", async () => {
    await renderBilling({ plan: "Business", plan_slug: "business" });
    const grid = planCard("Developer").parentElement;
    expect([...grid.children].map((c) => c.querySelector("p").textContent))
      .toEqual(["Developer", "Business", "Enterprise"]);
  });
});

describe("Current plan comes from the backend, not the price", () => {
  it("marks Developer current when entitlements say developer", async () => {
    await renderBilling({ plan: "Developer", plan_slug: "developer" });
    expect(ctaText("Developer")).toBe("Current Plan");
    // and NOT the most expensive, nor the first in the payload
    expect(ctaText("Business")).toBe("Upgrade");
  });

  it("marks Business current when entitlements say business", async () => {
    await renderBilling({ plan: "Business", plan_slug: "business" });
    expect(ctaText("Business")).toBe("Current Plan");
  });

  it("offers a purchase to an organization with no plan at all", async () => {
    await renderBilling({ plan: null, plan_slug: null, status: "trial" });
    expect(ctaText("Developer")).toBe("Upgrade");
    expect(ctaText("Business")).toBe("Upgrade");
    expect(ctaText("Enterprise")).toBe("Talk to an expert");
  });
});

describe("CTA matrix — current plan is Developer", () => {
  beforeEach(async () => {
    await renderBilling({ plan: "Developer", plan_slug: "developer" });
  });

  it("Developer shows Current Plan", () => expect(ctaText("Developer")).toBe("Current Plan"));
  it("Business shows Upgrade", () => expect(ctaText("Business")).toBe("Upgrade"));
  it("Enterprise shows Talk to an expert", () =>
    expect(ctaText("Enterprise")).toBe("Talk to an expert"));
});

describe("CTA matrix — current plan is Business", () => {
  beforeEach(async () => {
    await renderBilling({ plan: "Business", plan_slug: "business" });
  });

  it("Developer shows Contact us to downgrade", () =>
    expect(ctaText("Developer")).toBe("Contact us to downgrade"));
  it("Business shows Current Plan", () => expect(ctaText("Business")).toBe("Current Plan"));
  it("Enterprise shows Talk to an expert", () =>
    expect(ctaText("Enterprise")).toBe("Talk to an expert"));
});

describe("CTA matrix — current plan is Enterprise", () => {
  beforeEach(async () => {
    await renderBilling({ plan: "Enterprise", plan_slug: "enterprise" });
  });

  it("Developer shows Contact us to downgrade", () =>
    expect(ctaText("Developer")).toBe("Contact us to downgrade"));
  it("Business shows Contact us to downgrade", () =>
    expect(ctaText("Business")).toBe("Contact us to downgrade"));
  it("Enterprise shows Current Plan", () => expect(ctaText("Enterprise")).toBe("Current Plan"));
});

describe("The Current Plan control is inert", () => {
  it("is a disabled button, not a link", async () => {
    await renderBilling({ plan: "Business", plan_slug: "business" });
    const el = within(planCard("Business")).getByRole("button");
    expect(el).toBeDisabled();
    expect(within(planCard("Business")).queryByRole("link")).toBeNull();
  });

  it("clicking it calls no endpoint", async () => {
    await renderBilling({ plan: "Business", plan_slug: "business" });
    await userEvent.click(within(planCard("Business")).getByRole("button"), { pointerEventsCheck: 0 });
    expect(api.post).not.toHaveBeenCalled();
  });
});

describe("Downgrade never touches Stripe", () => {
  it("routes to the contact form carrying the plan and the intent", async () => {
    await renderBilling({ plan: "Business", plan_slug: "business" });
    const link = within(planCard("Developer")).getByRole("link");
    expect(link).toHaveAttribute("href", "/contact?plan=Developer&action=downgrade");
  });

  it("issues no request when clicked", async () => {
    await renderBilling({ plan: "Business", plan_slug: "business" });
    await userEvent.click(within(planCard("Developer")).getByRole("link"));
    expect(api.post).not.toHaveBeenCalled();
    expect(api.delete).not.toHaveBeenCalled();
  });
});

describe("Upgrade preserves the existing Stripe flow", () => {
  it("posts a plan slug and cadence only — never a price or a Price ID", async () => {
    api.post.mockResolvedValue({ data: { checkout_url: "https://checkout.stripe.com/c/pay/cs_test_1" } });
    // A trial is not a paid subscription, so this is the checkout path.
    await renderBilling({ plan: "Developer", plan_slug: "developer", status: "trial" });

    await userEvent.click(within(planCard("Business")).getByRole("button"));

    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(1));
    const [url, body] = api.post.mock.calls[0];
    expect(url).toBe("/organization/billing/checkout-session");
    expect(body).toEqual({ plan_slug: "business", billing_interval: "monthly" });
    // Nothing a browser could tamper with may appear in the body.
    for (const forbidden of ["price", "price_id", "amount", "currency", "unit_amount"]) {
      expect(body).not.toHaveProperty(forbidden);
    }
  });

  it("schedules a change instead of opening a second checkout when already paying", async () => {
    api.post.mockResolvedValue({ data: {} });
    // A live Stripe subscription: the backend reports checkout_locked, which is the same
    // predicate that would 409 a checkout attempt.
    await renderBilling({
      plan: "Developer", plan_slug: "developer", status: "active", checkout_locked: true,
    });

    await userEvent.click(within(planCard("Business")).getByRole("button"));

    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(1));
    // The existing plan-change endpoint — NOT checkout, which would create a second Stripe
    // subscription and bill the customer for both.
    expect(api.post.mock.calls[0][0]).toBe("/organization/billing/plan-change");
    expect(api.post).not.toHaveBeenCalledWith(
      "/organization/billing/checkout-session", expect.anything()
    );
  });

  it("never offers a purchase for a plan the server has not priced", async () => {
    await renderBilling({ plan: "Developer", plan_slug: "developer" });
    // Enterprise ranks above Business but self_service is false, so rank must not override it.
    const el = within(planCard("Enterprise")).queryByRole("button");
    expect(el).toBeNull();
    expect(within(planCard("Enterprise")).getByRole("link"))
      .toHaveAttribute("href", "/contact?plan=Enterprise");
  });
});

describe("An organization still on a retired tier", () => {
  // The edge case the filtering creates: hiding Pro from everyone would also hide it from the
  // people paying for it. They keep their card (marked current and inert); nobody else is
  // offered it; and no subscription is migrated to make the UI tidier.
  it("still sees its own plan, marked Current", async () => {
    await renderBilling({ plan: "Pro", plan_slug: "pro" });
    expect(planCard("Pro")).toBeTruthy();
    expect(ctaText("Pro")).toBe("Current Plan");
    expect(within(planCard("Pro")).getByRole("button")).toBeDisabled();
  });

  it("is not offered the other retired tier", async () => {
    await renderBilling({ plan: "Pro", plan_slug: "pro" });
    expect(screen.queryByText("Starter", { selector: "p" })).toBeNull();
  });

  it("is never auto-converted: the page issues no write on render", async () => {
    await renderBilling({ plan: "Pro", plan_slug: "pro" });
    expect(api.post).not.toHaveBeenCalled();
    expect(api.patch).not.toHaveBeenCalled();
    expect(api.delete).not.toHaveBeenCalled();
  });

  it("can still be moved up, and down only by contacting us", async () => {
    await renderBilling({ plan: "Pro", plan_slug: "pro" });
    // Developer ranks below Pro's tier, so it is a downgrade conversation.
    expect(ctaText("Developer")).toBe("Contact us to downgrade");
    // Business IS Pro's replacement at the same tier — neither an upgrade nor a downgrade,
    // so it goes to sales rather than silently charging for a sideways move.
    expect(ctaText("Business")).toBe("Talk to an expert");
    expect(ctaText("Enterprise")).toBe("Talk to an expert");
  });
});

describe("The page reads billing state and writes nothing", () => {
  it("issues only the two documented GETs on render", async () => {
    await renderBilling({ plan: "Developer", plan_slug: "developer" });
    expect(api.get.mock.calls.map(([url]) => url).sort())
      .toEqual(["/organization/overview", "/organization/plans"]);
    expect(api.post).not.toHaveBeenCalled();
    expect(api.patch).not.toHaveBeenCalled();
    expect(api.delete).not.toHaveBeenCalled();
  });
});
