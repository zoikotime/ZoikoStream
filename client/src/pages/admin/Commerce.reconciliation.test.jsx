import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

vi.mock("../../api", () => ({
  default: { get: vi.fn(), post: vi.fn() },
  errMsg: (error) => error?.message ?? "failed",
}));

import api from "../../api";
import { ThemeProvider } from "../../theme/ThemeContext";
import Commerce from "./Commerce";

const reconciliation = {
  reservations_without_order: [],
  reservations_with_missing_order: [],
  unmatched_settlements: Array.from({ length: 22 }, (_, index) => ({
    id: `settlement-${index}`,
    provider: "stripe",
    provider_payment_ref: `stripe-payment-reference-${index}`,
    amount: 25,
    currency: "USD",
    reason: `Unmatched settlement reason ${index}`,
  })),
  orders_missing_invoice: Array.from({ length: 52 }, (_, index) => ({
    id: `order-${String(index).padStart(8, "0")}`,
    total_amount: 50 + index,
    currency: "USD",
  })),
  orders_missing_seller_entity: [],
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.get).mockImplementation((url) => {
    if (url === "/admin/payment-provider") return Promise.resolve({ data: { mode: "not_configured" } });
    if (url === "/commercial/reconciliation") return Promise.resolve({ data: reconciliation });
    if (url === "/commercial/provider-events") {
      return Promise.resolve({ data: [{
        id: "provider-event-1",
        payment_id: "payment-1",
        provider_payment_ref: "stripe-payment-reference-0",
        processing_status: "processed",
      }] });
    }
    return Promise.resolve({ data: [] });
  });
  vi.mocked(api.post).mockResolvedValue({ data: {} });
});

function openReconciliation() {
  render(
    <ThemeProvider>
      <MemoryRouter>
        <Commerce />
      </MemoryRouter>
    </ThemeProvider>,
  );
  fireEvent.click(screen.getByRole("tab", { name: "Reconciliation" }));
}

describe("Commerce reconciliation layout", () => {
  it("keeps all settlement and missing-invoice records in independently scrollable, named regions", async () => {
    openReconciliation();

    const settlements = await screen.findByRole("region", {
      name: "Unmatched payment settlements list",
    });
    const missingInvoices = screen.getByRole("region", {
      name: "Accepted orders with no invoice list",
    });

    expect(settlements).toHaveClass(
      "zk-scroll-thin",
      "max-h-[min(60vh,560px)]",
      "overflow-y-auto",
      "overscroll-contain",
    );
    expect(missingInvoices).toHaveClass(
      "zk-scroll-thin",
      "max-h-[min(60vh,560px)]",
      "overflow-y-auto",
      "overscroll-contain",
    );
    expect(within(settlements).getAllByRole("listitem")).toHaveLength(22);
    expect(within(missingInvoices).getAllByRole("listitem")).toHaveLength(52);
    expect(within(settlements).getByText("stripe-payment-reference-21")).toBeInTheDocument();
    expect(within(missingInvoices).getAllByRole("listitem").at(-1)).toHaveTextContent("101 USD");

    expect(screen.getByText("Capacity without order")).toBeInTheDocument();
    expect(screen.getByText("Orphaned reservations")).toBeInTheDocument();
    expect(screen.getByText("Unmatched settlements")).toBeInTheDocument();
  });

  it("keeps the existing Match action available from the bounded settlement list", async () => {
    openReconciliation();
    const settlements = await screen.findByRole("region", {
      name: "Unmatched payment settlements list",
    });

    fireEvent.click(within(settlements).getAllByRole("button", { name: "Match" })[0]);
    expect(await screen.findByText("Match settlement to a payment")).toBeInTheDocument();
  });
});
