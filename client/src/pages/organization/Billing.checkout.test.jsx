// The Upgrade button: from click to Stripe-hosted Checkout.
//
// What these pin, in order of what would actually hurt if it regressed:
//   1. the browser is sent ONLY to a URL the server returned — never to `undefined`, which
//      navigates to "<origin>/undefined" and looks like a redirect while charging nothing
//   2. the request body carries a plan slug and a cadence, and nothing a browser could
//      tamper with into a different price
//   3. one click is one Checkout Session, and a double-click is still one
//   4. a failure restores the button and says something true, without echoing a provider
//      exception at the customer
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api", () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  errMsg: (e, fallback = "Something went wrong") => {
    const d = e?.response?.data?.detail;
    if (typeof d === "string") return d;
    return e?.message ?? fallback;
  },
}));

vi.mock("../../ui/Toast", () => ({
  notify: { success: vi.fn(), error: vi.fn() },
}));

import api from "../../api";
import { notify } from "../../ui/Toast";
import { ThemeProvider } from "../../theme/ThemeContext";
import OrganizationBilling from "./Billing";

// GET /organization/plans. `self_service` is SERVER-set from whether an approved Stripe price
// exists for the plan — which is why Enterprise is false and can never render a purchase.
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

const CHECKOUT_URL = "https://checkout.stripe.com/c/pay/cs_test_a1b2c3";

// No subscription yet: the one state in which a NEW checkout is the correct path.
const NO_SUBSCRIPTION = {
  plan: null, plan_slug: null, status: null,
  checkout_locked: false, subscription_plan_slug: null, subscription_status: null,
};

const overviewFor = (entitlements) => ({
  organization: { name: "Northwind" },
  entitlements: { ...NO_SUBSCRIPTION, items: [], ...entitlements },
});

let assigned;

async function renderBilling(entitlements = {}, { post } = {}) {
  api.get.mockImplementation((url) => {
    if (url === "/organization/overview") {
      return Promise.resolve({ data: overviewFor(entitlements) });
    }
    if (url === "/organization/plans") return Promise.resolve({ data: PLANS });
    return Promise.reject(new Error(`unexpected GET ${url}`));
  });
  api.post.mockImplementation(post
    || (() => Promise.resolve({ data: { checkout_url: CHECKOUT_URL } })));
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
const checkoutCalls = () =>
  api.post.mock.calls.filter(([url]) => url === "/organization/billing/checkout-session");

beforeEach(() => {
  vi.clearAllMocks();
  window.history.replaceState({}, "", "/organization/billing");
  assigned = [];
  // jsdom refuses a real navigation ("Not implemented: navigation"). Recording it is also
  // the only way to assert WHERE the browser was sent.
  Object.defineProperty(window, "location", {
    configurable: true,
    value: { ...window.location, assign: (url) => assigned.push(url), search: "" },
  });
});

describe("Which plans offer a purchase", () => {
  it("shows Upgrade on Developer", async () => {
    await renderBilling();
    expect(cta("Developer")).toHaveTextContent("Upgrade");
  });

  it("shows Upgrade on Business", async () => {
    await renderBilling();
    expect(cta("Business")).toHaveTextContent("Upgrade");
  });

  it("keeps Enterprise on Talk to an expert", async () => {
    await renderBilling();
    expect(cta("Enterprise")).toHaveTextContent("Talk to an expert");
    // Contract-priced: a link to sales, never a button that could post a checkout.
    expect(within(planCard("Enterprise")).queryByRole("button")).toBeNull();
  });

  it("offers no purchase for a plan the server has not priced", async () => {
    // self_service false is the server's answer, and the hierarchy must not override it.
    await renderBilling();
    await userEvent.click(cta("Enterprise"));
    expect(checkoutCalls()).toHaveLength(0);
  });

  it("never offers to re-buy the plan already held", async () => {
    await renderBilling({
      plan: "Developer", plan_slug: "developer", status: "active", checkout_locked: true,
    });
    expect(cta("Developer")).toHaveTextContent("Current Plan");
    expect(cta("Developer")).toBeDisabled();
  });
});

describe("The request the browser sends", () => {
  it("posts developer to the one checkout endpoint", async () => {
    await renderBilling();
    await userEvent.click(cta("Developer"));
    await waitFor(() => expect(checkoutCalls()).toHaveLength(1));
    const [url, body] = checkoutCalls()[0];
    expect(url).toBe("/organization/billing/checkout-session");
    expect(body).toEqual({ plan_slug: "developer", billing_interval: "monthly" });
  });

  it("posts business", async () => {
    await renderBilling();
    await userEvent.click(cta("Business"));
    await waitFor(() => expect(checkoutCalls()).toHaveLength(1));
    expect(checkoutCalls()[0][1]).toEqual({
      plan_slug: "business", billing_interval: "monthly",
    });
  });

  it("carries no price, amount or Stripe identifier", async () => {
    await renderBilling();
    await userEvent.click(cta("Business"));
    await waitFor(() => expect(checkoutCalls()).toHaveLength(1));
    const body = checkoutCalls()[0][1];
    expect(Object.keys(body).sort()).toEqual(["billing_interval", "plan_slug"]);
    expect(JSON.stringify(body)).not.toMatch(/price_|sk_|pk_|cs_/);
  });

  it("sends only a cadence the plan is actually priced on", async () => {
    // billing_intervals is the server's approved-price list; annual is absent here, so annual
    // must never be requested — inventing one would ask for a Price that does not exist.
    await renderBilling();
    await userEvent.click(cta("Developer"));
    await waitFor(() => expect(checkoutCalls()).toHaveLength(1));
    expect(checkoutCalls()[0][1].billing_interval).toBe("monthly");
  });
});

describe("The redirect", () => {
  it("sends the browser to the URL the server returned", async () => {
    await renderBilling();
    await userEvent.click(cta("Developer"));
    await waitFor(() => expect(assigned).toEqual([CHECKOUT_URL]));
    expect(assigned[0]).toMatch(/^https:\/\/checkout\.stripe\.com\//);
  });

  it("does NOT navigate when the response carries no checkout_url", async () => {
    // assign(undefined) walks the browser to "<origin>/undefined": a 404 that looks like a
    // redirect, with the purchase silently not started.
    await renderBilling({}, { post: () => Promise.resolve({ data: {} }) });
    await userEvent.click(cta("Developer"));
    await waitFor(() => expect(notify.error).toHaveBeenCalled());
    expect(assigned).toEqual([]);
    expect(cta("Developer")).not.toBeDisabled();
  });
});

describe("One click, one Checkout Session", () => {
  it("disables the button and says what is happening", async () => {
    let release;
    await renderBilling({}, {
      post: () => new Promise((resolve) => { release = resolve; }),
    });
    await userEvent.click(cta("Developer"));
    await waitFor(() => expect(cta("Developer")).toBeDisabled());
    expect(cta("Developer")).toHaveTextContent(/Redirecting to Stripe/i);
    release({ data: { checkout_url: CHECKOUT_URL } });
  });

  it("creates only one session when clicked twice in the same tick", async () => {
    let release;
    await renderBilling({}, {
      post: () => new Promise((resolve) => { release = resolve; }),
    });
    const btn = cta("Developer");
    // Both dispatched before React can re-render and apply `disabled`.
    btn.click();
    btn.click();
    btn.click();
    await waitFor(() => expect(checkoutCalls().length).toBeGreaterThan(0));
    expect(checkoutCalls()).toHaveLength(1);
    release({ data: { checkout_url: CHECKOUT_URL } });
  });

  it("does not start a second checkout from the other card while one is in flight", async () => {
    let release;
    await renderBilling({}, {
      post: () => new Promise((resolve) => { release = resolve; }),
    });
    cta("Developer").click();
    cta("Business").click();
    await waitFor(() => expect(checkoutCalls().length).toBeGreaterThan(0));
    expect(checkoutCalls()).toHaveLength(1);
    expect(checkoutCalls()[0][1].plan_slug).toBe("developer");
    release({ data: { checkout_url: CHECKOUT_URL } });
  });
});

describe("When checkout fails", () => {
  const failing = (status, detail) => () => Promise.reject({
    response: { status, data: detail === undefined ? {} : { detail } },
    message: "Request failed",
  });

  const clickAndRead = async () => {
    await userEvent.click(cta("Developer"));
    await waitFor(() => expect(notify.error).toHaveBeenCalled());
    return notify.error.mock.calls[0][0];
  };

  it("restores the button so the customer can retry", async () => {
    await renderBilling({}, { post: failing(503, "Payments are not configured") });
    await clickAndRead();
    expect(cta("Developer")).not.toBeDisabled();
    expect(cta("Developer")).toHaveTextContent("Upgrade");
    expect(assigned).toEqual([]);
  });

  it("lets a retry through after a failure", async () => {
    await renderBilling({}, { post: failing(502) });
    await clickAndRead();
    api.post.mockImplementation(() => Promise.resolve({ data: { checkout_url: CHECKOUT_URL } }));
    await userEvent.click(cta("Developer"));
    await waitFor(() => expect(assigned).toEqual([CHECKOUT_URL]));
  });

  it("shows the backend's own wording for the cases it explains", async () => {
    for (const [status, detail] of [
      [404, "Plan not found"],
      [409, "This organization already has a paid subscription."],
      [422, "Unsupported billing interval"],
      [503, "Payments are not configured"],
    ]) {
      // Unmount between cases: without this the cards accumulate and the queries below
      // match several "Developer" at once.
      cleanup();
      vi.clearAllMocks();
      await renderBilling({}, { post: failing(status, detail) });
      expect(await clickAndRead()).toBe(detail);
    }
  });

  it("never shows a provider exception to the customer", async () => {
    // The route raises 502 as f"Payment provider error: {e}" — a log line, not a sentence for
    // a paying customer, and the one place a Stripe-side detail could reach the browser.
    await renderBilling({}, {
      post: failing(502, "Payment provider error: StripeAPIError('sk_test_51H... invalid')"),
    });
    const message = await clickAndRead();
    expect(message).not.toMatch(/sk_|StripeAPIError|Payment provider error/);
    expect(message).toMatch(/nothing has been charged/i);
  });

  it("explains an expired session rather than echoing Not authenticated", async () => {
    await renderBilling({}, { post: failing(401, "Not authenticated") });
    expect(await clickAndRead()).toMatch(/session has expired/i);
  });

  it("explains a permission failure", async () => {
    await renderBilling({}, { post: failing(403, "Forbidden") });
    expect(await clickAndRead()).toMatch(/owner or admin/i);
  });

  it("distinguishes a network failure from a server answer", async () => {
    await renderBilling({}, { post: () => Promise.reject(new Error("Network Error")) });
    expect(await clickAndRead()).toMatch(/couldn't reach|connection/i);
  });
});
