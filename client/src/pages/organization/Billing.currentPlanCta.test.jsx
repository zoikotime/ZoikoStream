// The "Current Plan" control: deliberately non-actionable, and provably harmless.
//
// ── WHAT THIS IS ────────────────────────────────────────────────────────────────────────
// It was reported as an unresponsive button. It is not broken — it is the product rule. The
// organization is already on this plan, so there is nothing to buy, schedule or cancel from
// this card, and the control carries NO onClick at all. That absence is the real safety
// property: an accidental second subscription is structurally impossible here, not merely
// guarded against.
//
// What was genuinely wrong was the SIGNAL. `disabled:opacity-100` cancels the usual dimming
// — correct, because fading the plan somebody pays for reads as "deactivated" — but with a
// neutral cursor the control looked like a live CTA, so people clicked it and nothing
// happened. It now explains itself on hover and the cursor says so first.
//
// These tests exist because the dangerous regression is silent: wiring an onClick here, or
// letting the "current" branch fall through to the Upgrade branch, would send a paying
// customer to Stripe for a plan they already have. Nothing in the UI would look wrong.
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api", () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  errMsg: (e) => e?.message ?? "Something went wrong",
  transportMessage: (e) => (e?.response ? null : "Couldn't reach the ZoikoStream server."),
  diagnoseLoadError: () => "The platform API is unreachable.",
}));
vi.mock("../../ui/Toast", () => ({ notify: { success: vi.fn(), error: vi.fn() } }));

import api from "../../api";
import { notify } from "../../ui/Toast";
import { ThemeProvider } from "../../theme/ThemeContext";
import OrganizationBilling from "./Billing";

const PLANS = [
  { id: "p-dev", slug: "developer", name: "Developer", pricing_state: "PUBLISHED",
    price_monthly: 49, features: ["1 concurrent stream"],
    billing_intervals: ["monthly"], self_service: true },
  { id: "p-biz", slug: "business", name: "Business", pricing_state: "PUBLISHED",
    price_monthly: 249, features: ["5 concurrent streams"],
    billing_intervals: ["monthly"], self_service: true },
  { id: "p-ent", slug: "enterprise", name: "Enterprise", pricing_state: "CUSTOM",
    price_monthly: null, features: ["Unlimited streams"],
    billing_intervals: [], self_service: false },
];

// The reported state: entitled to Developer, no live Stripe subscription yet.
const ON_DEVELOPER = {
  plan: "Developer", plan_slug: "developer", status: "trialing",
  checkout_locked: false, subscription_plan_slug: null, subscription_status: null, items: [],
};

let assigned;

async function mount({ entitlements = ON_DEVELOPER, overview } = {}) {
  api.get.mockImplementation((url) => {
    if (url === "/organization/overview") {
      return overview || Promise.resolve({
        data: { organization: { name: "Northwind" }, entitlements },
      });
    }
    if (url === "/organization/plans") return Promise.resolve({ data: PLANS });
    return Promise.reject(new Error(`unexpected GET ${url}`));
  });
  api.post.mockResolvedValue({ data: { checkout_url: "https://checkout.stripe.com/c/pay/cs_x" } });
  render(
    <MemoryRouter>
      <ThemeProvider>
        <OrganizationBilling />
      </ThemeProvider>
    </MemoryRouter>
  );
}

const planCard = (name) => screen.getByText(name, { selector: "p" }).closest("div.flex.flex-col");
const cta = (name) => {
  const card = planCard(name);
  return within(card).queryByRole("button") || within(card).queryByRole("link");
};
const ctaText = (name) => cta(name)?.textContent?.trim();

beforeEach(() => {
  vi.clearAllMocks();
  window.history.replaceState({}, "", "/organization/billing");
  assigned = [];
  Object.defineProperty(window, "location", {
    configurable: true,
    value: { ...window.location, assign: (url) => assigned.push(url), search: "" },
  });
});

describe("the Current Plan control", () => {
  it("is what the Developer card shows when Developer is the entitled plan", async () => {
    await mount();
    await screen.findByText("Available Plans");
    expect(ctaText("Developer")).toBe("Current Plan");
  });

  it("is disabled, using the real attribute rather than a CSS imitation", async () => {
    // pointer-events:none would hide it from a mouse and leave a keyboard user able to fire
    // it — the wrong half of the problem.
    await mount();
    await screen.findByText("Available Plans");
    expect(cta("Developer")).toBeDisabled();
  });

  it("announces itself as the current plan", async () => {
    await mount();
    await screen.findByText("Available Plans");
    const button = cta("Developer");
    expect(button).toHaveAttribute("aria-current", "true");
    expect(button).toHaveAccessibleName(/current plan/i);
  });

  it("explains on hover why nothing happens", async () => {
    await mount();
    await screen.findByText("Available Plans");
    expect(cta("Developer")).toHaveAttribute("title", expect.stringMatching(/nothing to do/i));
  });

  it("is a plain button that can never submit a form", async () => {
    await mount();
    await screen.findByText("Available Plans");
    expect(cta("Developer")).toHaveAttribute("type", "button");
  });
});

describe("clicking it does nothing at all", () => {
  async function clickCurrentPlan() {
    await mount();
    await screen.findByText("Available Plans");
    await userEvent.click(cta("Developer"));
    // Give any stray async work a chance to run before asserting the absence of it.
    await new Promise((r) => setTimeout(r, 0));
  }

  it("creates no Stripe Checkout Session", async () => {
    await clickCurrentPlan();
    expect(api.post.mock.calls.filter(([u]) => u === "/organization/billing/checkout-session"))
      .toHaveLength(0);
  });

  it("schedules no plan change", async () => {
    await clickCurrentPlan();
    expect(api.post.mock.calls.filter(([u]) => u === "/organization/billing/plan-change"))
      .toHaveLength(0);
  });

  it("sends no write request of any kind", async () => {
    await clickCurrentPlan();
    expect(api.post).not.toHaveBeenCalled();
    expect(api.patch).not.toHaveBeenCalled();
    expect(api.delete).not.toHaveBeenCalled();
  });

  it("navigates nowhere", async () => {
    await clickCurrentPlan();
    expect(assigned).toEqual([]);
    expect(window.location.pathname).toBe("/organization/billing");
  });

  it("raises no toast, because nothing failed", async () => {
    await clickCurrentPlan();
    expect(notify.error).not.toHaveBeenCalled();
    expect(notify.success).not.toHaveBeenCalled();
  });

  it("never enters a loading label", async () => {
    await clickCurrentPlan();
    expect(ctaText("Developer")).toBe("Current Plan");
  });

  it("cannot be activated from the keyboard either", async () => {
    await mount();
    await screen.findByText("Available Plans");
    cta("Developer").focus();
    await userEvent.keyboard("{Enter}");
    await userEvent.keyboard(" ");
    expect(api.post).not.toHaveBeenCalled();
  });
});

describe("the other cards are unaffected", () => {
  it("Business still offers Upgrade", async () => {
    await mount();
    await screen.findByText("Available Plans");
    expect(ctaText("Business")).toBe("Upgrade");
  });

  it("Business Upgrade still reaches the checkout flow", async () => {
    await mount();
    await screen.findByText("Available Plans");
    await userEvent.click(cta("Business"));
    await waitFor(() => expect(
      api.post.mock.calls.filter(([u]) => u === "/organization/billing/checkout-session")
    ).toHaveLength(1));
    expect(api.post.mock.calls[0][1].plan_slug).toBe("business");
  });

  it("Enterprise still links to the contact route", async () => {
    await mount();
    await screen.findByText("Available Plans");
    expect(ctaText("Enterprise")).toBe("Talk to an expert");
    expect(cta("Enterprise")).toHaveAttribute("href", expect.stringContaining("/contact"));
  });
});

describe("Current Plan is never shown on a state we do not know", () => {
  it("shows the loading state before the subscription is known", async () => {
    let release;
    await mount({ overview: new Promise((r) => { release = r; }) });
    expect(screen.queryByText("Current Plan")).not.toBeInTheDocument();
    release({ data: { organization: { name: "Northwind" }, entitlements: ON_DEVELOPER } });
    await screen.findByText("Available Plans");
  });

  it("shows the error state, not a plan, when the subscription cannot be loaded", async () => {
    await mount({ overview: Promise.reject(Object.assign(new Error("boom"), { request: {} })) });
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.queryByText("Current Plan")).not.toBeInTheDocument();
    expect(screen.queryByText("Available Plans")).not.toBeInTheDocument();
  });
});
