// The unidentified-subscription repair, when the repair request itself fails.
//
// ── THE BUG ─────────────────────────────────────────────────────────────────────────────
// A subscription that Stripe has bound but not yet activated reports `checkout_locked` with
// no entitled `plan_slug`. The page correctly refuses to offer "Upgrade" there — checkout
// answers 409 every time — so every card falls back to "Talk to an expert", and the page
// posts to /organization/billing/sync to resolve the state.
//
// That POST was attempted exactly once. `syncAttempted` was a ref set BEFORE the request and
// never cleared, so a single failed attempt ended the repair for the life of the mount. The
// banner said "Plan changes are paused ... Contact us if this doesn't clear shortly" — but
// nothing could clear it. There was no retry control, and no route back except reloading the
// browser. A dropped request left the entire Billing page inert.
//
// Compounding it, the failure was printed straight into a sentence about the account:
// "Current subscription detected. Network Error Plan changes are paused and nothing further
// will be charged." One unanswered request, reported as a problem with their subscription.
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api", () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  // The real wording, so these tests pin what a reader actually sees.
  errMsg: (e) => {
    if (!e?.response && e?.request) {
      return "Couldn't reach the ZoikoStream server, so nothing was submitted. Check your connection and try again.";
    }
    const d = e?.response?.data?.detail;
    return typeof d === "string" ? d : (e?.message ?? "Something went wrong");
  },
  transportMessage: (e) => (e?.response || !e?.request ? null : "Couldn't reach the ZoikoStream server."),
  // Billing now diagnoses a failed load instead of saying "try refreshing", so the stub
  // has to answer this too.
  diagnoseLoadError: (e, path) => (
    e?.response?.status
      ? `The platform API returned ${e.response.status}`
      : `The platform API is unreachable — check that the API server is running and that VITE_API_URL points at it.`
  ),
}));
vi.mock("../../ui/Toast", () => ({ notify: { success: vi.fn(), error: vi.fn() } }));

import api from "../../api";
import { ThemeProvider } from "../../theme/ThemeContext";
import OrganizationBilling from "./Billing";

const PLANS = [
  { id: "p-dev", slug: "developer", name: "Developer", pricing_state: "PUBLISHED",
    price_monthly: 49, features: [], billing_intervals: ["monthly"], self_service: true },
  { id: "p-biz", slug: "business", name: "Business", pricing_state: "PUBLISHED",
    price_monthly: 249, features: [], billing_intervals: ["monthly"], self_service: true },
];

// A subscription Stripe has bound but not activated: locked, with nothing to name.
const STRANDED = {
  plan: null, plan_slug: null, status: "conversion_pending",
  checkout_locked: true, subscription_plan_slug: null, subscription_status: "conversion_pending",
  items: [],
};
const RESOLVED = { ...STRANDED, plan: "Business", plan_slug: "business", status: "active" };

const overviewFor = (ent) => ({ organization: { name: "Northwind" }, entitlements: ent });

/** A request that was sent but never answered — what axios hands back for a blocked response. */
const noResponse = () => Object.assign(new Error("Network Error"), { request: {}, response: undefined });

/** A request the server answered with a real refusal. */
const answered = (detail) =>
  Object.assign(new Error("Request failed"), { response: { status: 502, data: { detail } } });

function mountBilling() {
  render(
    <MemoryRouter>
      <ThemeProvider>
        <OrganizationBilling />
      </ThemeProvider>
    </MemoryRouter>
  );
}

const syncPosts = () =>
  api.post.mock.calls.filter(([url]) => url === "/organization/billing/sync");

const banner = () => screen.getByRole("status");

beforeEach(() => {
  vi.clearAllMocks();
  window.history.replaceState({}, "", "/organization/billing");
  api.get.mockImplementation((url) => {
    if (url === "/organization/overview") return Promise.resolve({ data: overviewFor(STRANDED) });
    if (url === "/organization/plans") return Promise.resolve({ data: PLANS });
    return Promise.reject(new Error(`unexpected GET ${url}`));
  });
});

describe("when the repair request never reaches the server", () => {
  beforeEach(() => {
    api.post.mockImplementation((url) =>
      url === "/organization/billing/sync" ? Promise.reject(noResponse()) : Promise.resolve({ data: {} }));
  });

  it("offers a way to try again", async () => {
    mountBilling();
    await screen.findByText("Available Plans");
    await waitFor(() => expect(syncPosts()).toHaveLength(1));
    // The regression: there was no control here at all, and the ref blocked a second attempt.
    expect(await within(banner()).findByRole("button", { name: /try again/i })).toBeEnabled();
  });

  it("actually re-sends the repair when that is clicked", async () => {
    mountBilling();
    await screen.findByText("Available Plans");
    await waitFor(() => expect(syncPosts()).toHaveLength(1));

    await userEvent.click(within(banner()).getByRole("button", { name: /try again/i }));
    await waitFor(() => expect(syncPosts()).toHaveLength(2));
  });

  it("recovers the page when a later attempt succeeds", async () => {
    let overviewCalls = 0;
    api.get.mockImplementation((url) => {
      if (url === "/organization/overview") {
        overviewCalls += 1;
        return Promise.resolve({ data: overviewFor(overviewCalls === 1 ? STRANDED : RESOLVED) });
      }
      if (url === "/organization/plans") return Promise.resolve({ data: PLANS });
      return Promise.reject(new Error(`unexpected GET ${url}`));
    });
    let attempt = 0;
    api.post.mockImplementation((url) => {
      if (url !== "/organization/billing/sync") return Promise.resolve({ data: {} });
      attempt += 1;
      return attempt === 1
        ? Promise.reject(noResponse())
        : Promise.resolve({ data: { synced: true, outcome: "applied", status: "active" } });
    });

    mountBilling();
    await screen.findByText("Available Plans");
    await waitFor(() => expect(syncPosts()).toHaveLength(1));
    await userEvent.click(within(banner()).getByRole("button", { name: /try again/i }));

    // The banner is gone, which is the whole point: the page is usable again.
    await waitFor(() => expect(screen.queryByRole("status")).not.toBeInTheDocument());
  });

  it("does not tell the reader their subscription is at fault", async () => {
    mountBilling();
    await screen.findByText("Available Plans");
    await waitFor(() => expect(syncPosts()).toHaveLength(1));
    const text = banner().textContent;
    // The old wording printed axios's bare string into a sentence about their account.
    expect(text).not.toMatch(/Network Error/);
    expect(text).toMatch(/couldn't reach/i);
  });

  it("still says no further charge will be made", async () => {
    mountBilling();
    await screen.findByText("Available Plans");
    await waitFor(() => expect(syncPosts()).toHaveLength(1));
    expect(banner().textContent).toMatch(/nothing further will be charged/i);
  });
});

describe("when the server answers with a real refusal", () => {
  beforeEach(() => {
    api.post.mockImplementation((url) =>
      url === "/organization/billing/sync"
        ? Promise.reject(answered("Stripe has no record of this subscription. Please contact support."))
        : Promise.resolve({ data: {} }));
  });

  it("reports what the server said", async () => {
    mountBilling();
    await screen.findByText("Available Plans");
    await waitFor(() => expect(syncPosts()).toHaveLength(1));
    expect(banner().textContent).toMatch(/Stripe has no record of this subscription/);
  });

  it("offers no retry, because retrying cannot change that answer", async () => {
    mountBilling();
    await screen.findByText("Available Plans");
    await waitFor(() => expect(syncPosts()).toHaveLength(1));
    expect(within(banner()).queryByRole("button", { name: /try again/i })).not.toBeInTheDocument();
  });
});

describe("the safeguards are unchanged", () => {
  it("never offers Upgrade while the subscription cannot be identified", async () => {
    // Retrying must not become a way past the lock that stops a second charge.
    api.post.mockImplementation((url) =>
      url === "/organization/billing/sync" ? Promise.reject(noResponse()) : Promise.resolve({ data: {} }));
    mountBilling();
    await screen.findByText("Available Plans");
    await waitFor(() => expect(syncPosts()).toHaveLength(1));
    await userEvent.click(within(banner()).getByRole("button", { name: /try again/i }));
    await waitFor(() => expect(syncPosts()).toHaveLength(2));

    expect(screen.queryByRole("button", { name: /^Upgrade$/ })).not.toBeInTheDocument();
  });

  it("starts no checkout while the repair is outstanding", async () => {
    api.post.mockImplementation((url) =>
      url === "/organization/billing/sync" ? Promise.reject(noResponse()) : Promise.resolve({ data: {} }));
    mountBilling();
    await screen.findByText("Available Plans");
    await waitFor(() => expect(syncPosts()).toHaveLength(1));
    await userEvent.click(within(banner()).getByRole("button", { name: /try again/i }));
    await waitFor(() => expect(syncPosts()).toHaveLength(2));

    const checkout = api.post.mock.calls.filter(([u]) => u !== "/organization/billing/sync");
    expect(checkout).toHaveLength(0);
  });
});
