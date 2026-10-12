// Moving DOWN a tier, from the plan card.
//
// ── WHAT CHANGED ────────────────────────────────────────────────────────────────────────
// A downgrade is the same scheduled plan change as an upgrade, and the backend has always
// treated it that way: crud.admin.request_plan_change validates the cadence, the plan's
// availability and its approved price, then schedules it at `current_period_end`. It has no
// notion of direction at all.
//
// Only this card insisted otherwise — it rendered a link to the contact form, so a customer
// on Business could schedule their way UP self-service but had to email somebody to come back
// down. The card now uses the endpoint the backend already exposes.
//
// ── WHAT DELIBERATELY DID NOT CHANGE ────────────────────────────────────────────────────
// The billing rules, all of which live in the backend and are only surfaced here:
//   * nothing is charged or credited at request time (no proration);
//   * it takes effect at `current_period_end`, not now;
//   * the CURRENT plan stays entitled until that date — the lower plan does not become
//     "Current" the moment it is requested;
//   * a plan with no approved price (Enterprise) still cannot be scheduled, and an
//     organization with no paid subscription has no period end to schedule against. Both
//     keep the contact form.
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api", () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  errMsg: (e) => e?.response?.data?.detail ?? e?.message ?? "Something went wrong",
  transportMessage: () => null,
  diagnoseLoadError: () => "Couldn't load billing information.",
}));
vi.mock("../../ui/Toast", () => ({ notify: { success: vi.fn(), error: vi.fn() } }));

import api from "../../api";
import { notify } from "../../ui/Toast";
import { ThemeProvider } from "../../theme/ThemeContext";
import OrganizationBilling from "./Billing";

const PLANS = [
  { id: "p-dev", slug: "developer", name: "Developer", pricing_state: "PUBLISHED",
    price_monthly: 49, features: [], billing_intervals: ["monthly"], self_service: true },
  { id: "p-biz", slug: "business", name: "Business", pricing_state: "PUBLISHED",
    price_monthly: 249, features: [], billing_intervals: ["monthly"], self_service: true },
  { id: "p-ent", slug: "enterprise", name: "Enterprise", pricing_state: "CUSTOM",
    price_monthly: null, features: [], billing_intervals: [], self_service: false },
];

const PERIOD_END = "2026-11-09T00:00:00Z";

// Business is the live, paid plan.
const ON_BUSINESS = {
  plan: "Business", plan_slug: "business", status: "active", billing_interval: "monthly",
  checkout_locked: true, current_period_end: PERIOD_END,
  pending_plan_slug: null, pending_plan_name: null, plan_change_effective_at: null, items: [],
};

async function mount(entitlements = ON_BUSINESS, { plans = PLANS, post } = {}) {
  api.get.mockImplementation((url) => {
    if (url === "/organization/overview") {
      return Promise.resolve({
        data: { organization: { name: "Northwind" }, entitlements },
      });
    }
    if (url === "/organization/plans") return Promise.resolve({ data: plans });
    return Promise.reject(new Error(`unexpected GET ${url}`));
  });
  // Set here, not by the caller beforehand: mount would otherwise overwrite it.
  api.post.mockImplementation(post || (() => Promise.resolve({ data: {} })));
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
const planChanges = () =>
  api.post.mock.calls.filter(([u]) => u === "/organization/billing/plan-change");
const checkouts = () =>
  api.post.mock.calls.filter(([u]) => u === "/organization/billing/checkout-session");

let confirmSpy;

beforeEach(() => {
  vi.clearAllMocks();
  window.history.replaceState({}, "", "/organization/billing");
  confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
});
afterEach(() => confirmSpy.mockRestore());

describe("the CTA matrix when Business is current", () => {
  it("marks Business as the current plan", async () => {
    await mount();
    expect(ctaText("Business")).toBe("Current Plan");
  });

  it("offers Developer as a Downgrade, not a contact form", async () => {
    await mount();
    expect(ctaText("Developer")).toBe("Downgrade");
  });

  it("keeps Enterprise quote-led", async () => {
    await mount();
    expect(ctaText("Enterprise")).toBe("Talk to an expert");
  });

  it("says when it will take effect", async () => {
    await mount();
    expect(cta("Developer")).toHaveAttribute(
      "title", expect.stringMatching(/end of your billing period/i));
  });
});

describe("requesting the downgrade", () => {
  it("schedules it through the plan-change endpoint", async () => {
    await mount();
    await userEvent.click(cta("Developer"));
    await waitFor(() => expect(planChanges()).toHaveLength(1));
    expect(planChanges()[0][1]).toEqual({ plan_slug: "developer", billing_interval: "monthly" });
  });

  it("NEVER opens checkout, which would be a second subscription", async () => {
    await mount();
    await userEvent.click(cta("Developer"));
    await waitFor(() => expect(planChanges()).toHaveLength(1));
    expect(checkouts()).toHaveLength(0);
  });

  it("asks first, and says nothing is charged now", async () => {
    await mount();
    await userEvent.click(cta("Developer"));
    const asked = confirmSpy.mock.calls[0][0];
    expect(asked).toMatch(/Developer/);
    expect(asked).toMatch(/charged or credited/i);
    expect(asked).toMatch(/stays active until then/i);
  });

  it("does nothing at all if the confirmation is declined", async () => {
    confirmSpy.mockReturnValue(false);
    await mount();
    await userEvent.click(cta("Developer"));
    expect(api.post).not.toHaveBeenCalled();
  });

  it("re-reads from the backend instead of asserting the outcome itself", async () => {
    await mount();
    const before = api.get.mock.calls.filter(([u]) => u === "/organization/overview").length;
    await userEvent.click(cta("Developer"));
    await waitFor(() => {
      const after = api.get.mock.calls.filter(([u]) => u === "/organization/overview").length;
      expect(after).toBeGreaterThan(before);
    });
  });

  it("surfaces a refusal and leaves the plan alone", async () => {
    await mount(ON_BUSINESS, { post: () => Promise.reject(
      { response: { status: 409, data: { detail: "Already scheduled" } } }) });
    await userEvent.click(cta("Developer"));
    await waitFor(() => expect(notify.error).toHaveBeenCalledWith("Already scheduled"));
    expect(ctaText("Business")).toBe("Current Plan");
  });

  it("restores the button after a failure so it can be retried", async () => {
    await mount(ON_BUSINESS, { post: () => Promise.reject(
      { response: { status: 409, data: { detail: "nope" } } }) });
    await userEvent.click(cta("Developer"));
    await waitFor(() => expect(notify.error).toHaveBeenCalled());
    expect(ctaText("Developer")).toBe("Downgrade");
    expect(cta("Developer")).toBeEnabled();
  });
});

describe("the lower plan does not become current early", () => {
  const PENDING_DOWNGRADE = {
    ...ON_BUSINESS,
    status: "plan_change_scheduled",
    pending_plan_slug: "developer", pending_plan_name: "Developer",
    pending_billing_interval: "monthly", plan_change_effective_at: PERIOD_END,
  };

  it("keeps Business as the entitled plan while the change is pending", async () => {
    await mount(PENDING_DOWNGRADE);
    expect(ctaText("Business")).toBe("Current Plan");
  });

  it("shows the scheduled change and when it lands", async () => {
    await mount(PENDING_DOWNGRADE);
    expect(screen.getByText(/Plan change scheduled/i)).toBeInTheDocument();
    expect(screen.getByText(/end of your current\s+billing period/i)).toBeInTheDocument();
  });

  it("offers to cancel it", async () => {
    await mount(PENDING_DOWNGRADE);
    expect(screen.getByRole("button", { name: /cancel change/i })).toBeInTheDocument();
  });

  it("blocks a second request while one is outstanding", async () => {
    await mount(PENDING_DOWNGRADE);
    expect(cta("Developer")).toBeDisabled();
    expect(cta("Developer")).toHaveAttribute(
      "title", expect.stringMatching(/cancel the scheduled change first/i));
  });
});

describe("when there is nothing to schedule against", () => {
  it("keeps the contact form for a plan with no approved price", async () => {
    // request_plan_change resolves an approved Stripe price and fails closed without one, so
    // an unpriced plan must never render a button that would be refused. Driven by making
    // Business unpriced while Enterprise is current — Enterprise itself is the top tier and
    // so is never a downgrade target at all.
    const unpricedBusiness = PLANS.map((p) =>
      p.slug === "business" ? { ...p, self_service: false, billing_intervals: [] } : p);
    await mount({ ...ON_BUSINESS, plan: "Enterprise", plan_slug: "enterprise" },
                { plans: unpricedBusiness });
    expect(ctaText("Business")).toBe("Contact us to downgrade");
  });

  it("does not offer a scheduled downgrade without a paid subscription", async () => {
    // No live provider subscription means no billing period to schedule against.
    await mount({ ...ON_BUSINESS, checkout_locked: false, current_period_end: null });
    expect(ctaText("Developer")).toBe("Contact us to downgrade");
  });
});
