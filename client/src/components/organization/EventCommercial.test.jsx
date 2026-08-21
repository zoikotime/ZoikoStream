// Phase 4C — payment-flow tests for EventCommercial.
//
// Scope is the PAYMENT path only: the Pay action, the amount it shows, the request it sends,
// the redirect, and the Checkout return. Deliberately no coverage of unrelated commercial UI.
//
// The api module is mocked at its boundary, so nothing here needs a backend, a browser, or
// any Stripe package — the app has no Stripe browser SDK by design (hosted Checkout redirects).
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api", () => ({
  default: { get: vi.fn(), post: vi.fn() },
  errMsg: (e) => e?.message ?? "error",
}));
// Toasts are irrelevant here and would need a portal host.
vi.mock("../../ui/Toast", () => ({ notify: { error: vi.fn(), success: vi.fn() } }));

import api from "../../api";
import EventCommercial from "./EventCommercial";

const EVENT = { id: "11111111-1111-1111-1111-111111111111", title: "Memorial" };
const ORDER_ID = "22222222-2222-2222-2222-222222222222";

/** An accepted, tax-determined order: 1200.00 + 240.00 tax = 1440.00 outstanding. */
function order(over = {}) {
  return {
    id: ORDER_ID, event_id: EVENT.id, status: "accepted", order_version: 1,
    currency: "USD", subtotal: "1200.00", tax_amount: "240.00", total_amount: "1440.00",
    risk_tier: "r2", billing_classification: "commercial", lines: [], ...over,
  };
}

/** Route every GET the component makes. `payments` drives the outstanding calculation. */
function wireApi({ ord = order(), payments = [], invoices = [], schedule = [],
                   readiness = { ready: true, blocking_reasons: [], exceptions_applied: [] } } = {}) {
  api.get.mockImplementation((url) => {
    if (url.includes("/orders/current")) return Promise.resolve({ data: ord });
    if (url.endsWith("/quotes")) return Promise.resolve({ data: [] });
    if (url.endsWith("/capacity")) return Promise.resolve({ data: [] });
    if (url.endsWith("/readiness")) return Promise.resolve({ data: readiness });
    if (url.endsWith("/incidents")) return Promise.resolve({ data: [] });
    if (url.endsWith("/payment-schedule")) return Promise.resolve({ data: schedule });
    if (url.endsWith("/payments")) return Promise.resolve({ data: payments });
    if (url.endsWith("/invoices")) return Promise.resolve({ data: invoices });
    return Promise.resolve({ data: [] });
  });
}

let assignSpy;

beforeEach(() => {
  vi.clearAllMocks();
  // window.location.assign is not implemented in jsdom; replace it so the redirect is observable.
  assignSpy = vi.fn();
  Object.defineProperty(window, "location", {
    configurable: true,
    value: { ...window.location, assign: assignSpy, search: "", pathname: "/organization/events/e1", hash: "" },
  });
  window.history.replaceState = vi.fn();
});

afterEach(() => vi.restoreAllMocks());

async function renderReady(opts) {
  wireApi(opts);
  render(<EventCommercial event={EVENT} />);
  await waitFor(() => expect(screen.getByTestId("pay-button")).toBeInTheDocument());
}

// ── Pay button visibility ────────────────────────────────────────────────────────────────

describe("Pay action", () => {
  it("renders for an order with an outstanding balance", async () => {
    await renderReady();
    expect(screen.getByTestId("pay-button")).toHaveTextContent(/pay with card/i);
  });

  it("is hidden once the order is fully paid", async () => {
    wireApi({ payments: [{ id: "p1", state: "paid", amount: "1440.00", currency: "USD" }] });
    render(<EventCommercial event={EVENT} />);
    await waitFor(() => expect(screen.getByText(/fully authorized/i)).toBeInTheDocument());
    expect(screen.queryByTestId("pay-button")).not.toBeInTheDocument();
  });

  it("is still shown when only part of the balance is paid", async () => {
    await renderReady({ payments: [{ id: "p1", state: "paid", amount: "440.00", currency: "USD" }] });
    expect(screen.getByTestId("pay-button")).toBeInTheDocument();
  });
});

// ── Amount / currency are the backend's ──────────────────────────────────────────────────

describe("amount and currency", () => {
  it("shows the backend outstanding amount, read-only", async () => {
    await renderReady();
    await userEvent.click(screen.getByTestId("pay-button"));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByTestId("pay-amount")).toHaveTextContent("1,440.00");
    expect(within(dialog).getByTestId("pay-currency")).toHaveTextContent("USD");
  });

  it("offers no amount input at all", async () => {
    await renderReady();
    await userEvent.click(screen.getByTestId("pay-button"));
    const dialog = await screen.findByRole("dialog");
    // The payer chooses WHETHER to pay, never HOW MUCH.
    expect(within(dialog).queryByRole("spinbutton")).not.toBeInTheDocument();
    expect(within(dialog).queryByRole("textbox")).not.toBeInTheDocument();
  });

  it("shows the backend currency, not a hardcoded one", async () => {
    await renderReady({ ord: order({ currency: "INR", total_amount: "50000.00" }) });
    await userEvent.click(screen.getByTestId("pay-button"));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByTestId("pay-currency")).toHaveTextContent("INR");
  });

  it("reflects the remaining balance after a partial payment", async () => {
    await renderReady({ payments: [{ id: "p1", state: "paid", amount: "440.00", currency: "USD" }] });
    await userEvent.click(screen.getByTestId("pay-button"));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByTestId("pay-amount")).toHaveTextContent("1,000.00");
  });
});

// ── Request shape + redirect ─────────────────────────────────────────────────────────────

describe("checkout request", () => {
  it("posts to the checkout endpoint with NO financial fields", async () => {
    api.post.mockResolvedValue({ data: { checkout_url: "https://checkout.stripe.com/c/pay/cs_1" } });
    await renderReady();
    await userEvent.click(screen.getByTestId("pay-button"));
    await userEvent.click(await screen.findByRole("button", { name: /pay \$/i }));

    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(1));
    const [url, body] = api.post.mock.calls[0];
    expect(url).toBe(`/commercial/orders/${ORDER_ID}/payments/checkout-session`);
    // The client cannot choose the amount, currency, tax or seller.
    expect(Object.keys(body)).toEqual(["provider_name"]);
    for (const forbidden of ["amount", "currency", "tax", "tax_amount", "discount", "seller"]) {
      expect(body).not.toHaveProperty(forbidden);
    }
  });

  it("redirects the browser to the provider URL", async () => {
    api.post.mockResolvedValue({ data: { checkout_url: "https://checkout.stripe.com/c/pay/cs_2" } });
    await renderReady();
    await userEvent.click(screen.getByTestId("pay-button"));
    await userEvent.click(await screen.findByRole("button", { name: /pay \$/i }));
    await waitFor(() =>
      expect(assignSpy).toHaveBeenCalledWith("https://checkout.stripe.com/c/pay/cs_2"));
  });

  it("does not redirect when the backend returns no URL", async () => {
    api.post.mockResolvedValue({ data: {} });
    await renderReady();
    await userEvent.click(screen.getByTestId("pay-button"));
    await userEvent.click(await screen.findByRole("button", { name: /pay \$/i }));
    await waitFor(() => expect(api.post).toHaveBeenCalled());
    expect(assignSpy).not.toHaveBeenCalled();
  });

  it("surfaces a backend error without redirecting", async () => {
    api.post.mockRejectedValue({ message: "This order is already paid in full" });
    await renderReady();
    await userEvent.click(screen.getByTestId("pay-button"));
    await userEvent.click(await screen.findByRole("button", { name: /pay \$/i }));
    await waitFor(() => expect(api.post).toHaveBeenCalled());
    expect(assignSpy).not.toHaveBeenCalled();
  });

  it("does not fire twice on a double click", async () => {
    let resolve;
    api.post.mockReturnValue(new Promise((r) => { resolve = r; }));
    await renderReady();
    await userEvent.click(screen.getByTestId("pay-button"));
    const confirm = await screen.findByRole("button", { name: /pay \$/i });
    await userEvent.click(confirm);
    await userEvent.click(confirm).catch(() => {});   // second click while in flight
    expect(api.post).toHaveBeenCalledTimes(1);
    resolve({ data: { checkout_url: "https://checkout.stripe.com/c/pay/cs_3" } });
  });
});

// ── Checkout return: never trusted ───────────────────────────────────────────────────────

describe("checkout return", () => {
  function withSearch(search) {
    Object.defineProperty(window, "location", {
      configurable: true,
      value: { ...window.location, assign: assignSpy, search, pathname: "/organization/events/e1", hash: "" },
    });
  }

  it("shows a WAITING message on success — never 'paid'", async () => {
    withSearch("?checkout=success");
    wireApi();
    render(<EventCommercial event={EVENT} />);
    const banner = await screen.findByTestId("checkout-return");
    expect(banner).toHaveTextContent(/waiting for confirmation/i);
    expect(banner.textContent).not.toMatch(/\bpaid\b/i);
  });

  it("shows a cancelled message and confirms nothing was taken", async () => {
    withSearch("?checkout=cancelled");
    wireApi();
    render(<EventCommercial event={EVENT} />);
    const banner = await screen.findByTestId("checkout-return");
    expect(banner).toHaveTextContent(/cancelled/i);
    expect(banner).toHaveTextContent(/no payment was taken/i);
  });

  it("re-fetches backend state on return instead of trusting the URL", async () => {
    withSearch("?checkout=success");
    wireApi();
    render(<EventCommercial event={EVENT} />);
    await screen.findByTestId("checkout-return");
    // The initial load fetches, and the return triggers another round — the displayed state
    // therefore comes from the backend, not from the redirect.
    await waitFor(() =>
      expect(api.get.mock.calls.filter((c) => String(c[0]).includes("/orders/current")).length)
        .toBeGreaterThan(1));
  });

  it("strips checkout and session_id from the URL", async () => {
    withSearch("?checkout=success&session_id=cs_someone_else&tab=commercial");
    wireApi();
    render(<EventCommercial event={EVENT} />);
    await screen.findByTestId("checkout-return");
    const replaced = window.history.replaceState.mock.calls.at(-1)?.[2] ?? "";
    expect(replaced).not.toContain("checkout=");
    expect(replaced).not.toContain("session_id");   // never trusted, never retained
    expect(replaced).toContain("tab=commercial");   // unrelated params preserved
  });

  it("ignores an unrecognised checkout value", async () => {
    withSearch("?checkout=totally_made_up");
    wireApi();
    render(<EventCommercial event={EVENT} />);
    await waitFor(() => expect(screen.getByTestId("pay-button")).toBeInTheDocument());
    expect(screen.queryByTestId("checkout-return")).not.toBeInTheDocument();
  });

  it("shows no banner on a normal visit", async () => {
    withSearch("");
    await renderReady();
    expect(screen.queryByTestId("checkout-return")).not.toBeInTheDocument();
  });
});

// ── Backend payment states are displayed, not reinvented ─────────────────────────────────

describe("payment states", () => {
  it.each([
    ["requires_action", /requires action/i],
    ["pending", /pending/i],
    ["paid", /paid/i],
    ["failed", /failed/i],
    ["part_refunded", /part refunded/i],
    ["disputed", /disputed/i],
  ])("renders the backend state %s", async (state, label) => {
    await renderReady({
      payments: [{ id: "p1", state, amount: "10.00", currency: "USD",
                   provider: "stripe", created_at: "2026-08-01T10:00:00Z" }],
    });
    expect(screen.getByText(label)).toBeInTheDocument();
  });
});

// ── No secret may reach the browser ──────────────────────────────────────────────────────

describe("security", () => {
  it("never references a Stripe secret or webhook secret", async () => {
    api.post.mockResolvedValue({ data: { checkout_url: "https://checkout.stripe.com/c/pay/cs_4" } });
    await renderReady();
    await userEvent.click(screen.getByTestId("pay-button"));
    const html = document.body.innerHTML;
    for (const forbidden of ["sk_test", "sk_live", "whsec_", "STRIPE_SECRET_KEY",
                              "STRIPE_WEBHOOK_SECRET"]) {
      expect(html).not.toContain(forbidden);
    }
  });

  it("never posts a client-chosen amount even if the dialog is reopened", async () => {
    api.post.mockResolvedValue({ data: { checkout_url: "https://x/y" } });
    await renderReady();
    for (let i = 0; i < 2; i += 1) {
      await userEvent.click(screen.getByTestId("pay-button"));
      const confirm = await screen.findByRole("button", { name: /pay \$/i });
      await userEvent.click(confirm);
      await waitFor(() => expect(api.post).toHaveBeenCalled());
      for (const [, body] of api.post.mock.calls) {
        expect(body).not.toHaveProperty("amount");
      }
      api.post.mockClear();
    }
  });
});
