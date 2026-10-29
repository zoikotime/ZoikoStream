// Analytics -> Contracted MRR. The card read $596 whatever range was selected and looked like a
// windowed figure that ignored the filter. It is CURRENT contracted MRR (services/admin.analytics
// evaluates it now; a subscription row is overwritten on every transition, so past MRR is not
// recoverable). Pinned here: the range still reaches the server, and the card says when it holds.
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

vi.mock("../../api", () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  errMsg: (e) => e?.message ?? "failed",
  diagnoseLoadError: (e) => e?.message ?? "failed",
  AUTH_EXPIRED_EVENT: "zoiko:auth-expired",
}));

import api from "../../api";
import { ThemeProvider } from "../../theme/ThemeContext";
import Analytics from "./Analytics";

const AS_OF = "2026-10-27T09:30:00Z";
const body = (range) => ({
  window: { range, since: "2026-09-27T00:00:00Z", until: "2026-10-27T09:30:00Z", org_id: null, bucket: "day" },
  mrr: 596, mrr_as_of: AS_OF, mrr_basis: "contracted_list_price", trials: { count: 0, list_value: 0 },
  streaming_hours: 0, totals: {}, revenue: [], organizations: [], users: [], events: [],
  measurement: { mrr: "derived" }, unmeasured: [], note: "",
});

const callsTo = (url) => vi.mocked(api.get).mock.calls.filter(([u]) => u === url);
const lastParams = (url) => callsTo(url).at(-1)?.[1]?.params;
const wrap = () => render(<ThemeProvider><MemoryRouter><Analytics /></MemoryRouter></ThemeProvider>);
const asOfText = new Date(AS_OF).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.get).mockImplementation((url, opts) =>
    Promise.resolve({ data: url === "/admin/analytics" ? body(opts?.params?.range) : { items: [] } }));
});

describe("Analytics — Contracted MRR", () => {
  it("is labelled as current and says it is not affected by the range", async () => {
    wrap();
    expect(await screen.findByText(/Current contracted MRR/)).toBeInTheDocument();
    expect(await screen.findByText(new RegExp(`As of ${asOfText}.*not affected by the time range`))).toBeInTheDocument();
    expect(screen.queryByText("Now, at list price — not windowed")).not.toBeInTheDocument();
  });

  it.each([["24 hours", "24h"], ["7 days", "7d"], ["90 days", "90d"], ["30 days", "30d"]])(
    "still sends the %s range to the server", async (label, key) => {
      wrap();
      await screen.findByText(/As of/);
      if (key !== "30d") fireEvent.click(screen.getByRole("button", { name: label }));
      await waitFor(() => expect(lastParams("/admin/analytics")).toMatchObject({ range: key }));
      // The card's statement holds for every range, because the server evaluates MRR now.
      expect(await screen.findByText(/not affected by the time range/)).toBeInTheDocument();
    });

  it("issues one request per range change", async () => {
    wrap();
    await screen.findByText(/As of/);
    const before = callsTo("/admin/analytics").length;
    fireEvent.click(screen.getByRole("button", { name: "7 days" }));
    await waitFor(() => expect(lastParams("/admin/analytics")).toMatchObject({ range: "7d" }));
    fireEvent.click(screen.getByRole("button", { name: "90 days" }));
    await waitFor(() => expect(lastParams("/admin/analytics")).toMatchObject({ range: "90d" }));
    expect(callsTo("/admin/analytics").length - before).toBe(2);
  });

  it("keeps a custom window's UTC day boundaries", async () => {
    wrap();
    await screen.findByText(/As of/);
    fireEvent.click(screen.getByRole("button", { name: "Custom" }));
    fireEvent.change(screen.getByLabelText("From date"), { target: { value: "2026-03-01" } });
    fireEvent.change(screen.getByLabelText("To date"), { target: { value: "2026-03-31" } });
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    await waitFor(() => expect(lastParams("/admin/analytics")).toMatchObject({
      range: "custom", from: "2026-03-01T00:00:00Z", to: "2026-03-31T23:59:59Z" }));
    expect(await screen.findByText(/not affected by the time range/)).toBeInTheDocument();
  });

  it("falls back to 'now' when an older server omits mrr_as_of", async () => {
    vi.mocked(api.get).mockImplementation((url) => {
      const rest = { ...body("30d") };
      delete rest.mrr_as_of;
      return Promise.resolve({ data: url === "/admin/analytics" ? rest : { items: [] } });
    });
    wrap();
    expect(await screen.findByText(/As of now · list price · not affected by the time range/)).toBeInTheDocument();
  });
});

// The trial note under the cards. Production read "10 subscriptions are in trial ($641/mo ...)"
// for every range: it is a current snapshot, like the card above it, and now says so. $641 is
// nine trials at list price, so the tenth (no published price) is named rather than hidden.
describe("Analytics — trial note", () => {
  const withTrials = (trials) => (url, opts) =>
    Promise.resolve({ data: url === "/admin/analytics" ? { ...body(opts?.params?.range), trials } : { items: [] } });

  it("says the trials are current and the range does not apply", async () => {
    vi.mocked(api.get).mockImplementation(withTrials({ count: 10, list_value: 641, unpriced: 1 }));
    wrap();
    const note = await screen.findByTestId("trial-note");
    expect(note).toHaveTextContent(/10 subscriptions are currently in trial/);
    expect(note).toHaveTextContent(/potential MRR at current list price/);
    expect(note).toHaveTextContent(/1 on a plan with no published price, not valued/);
    expect(note).toHaveTextContent(/not included in Contracted MRR, and not affected by the time range/);
  });

  it("does not mention unpriced trials when every trial is priced", async () => {
    vi.mocked(api.get).mockImplementation(withTrials({ count: 1, list_value: 49, unpriced: 0 }));
    wrap();
    const note = await screen.findByTestId("trial-note");
    expect(note).toHaveTextContent(/1 subscription is currently in trial \(\$49(\.00)?\/mo potential MRR at current list price\)/);
    expect(note).not.toHaveTextContent(/no published price/);
  });

  it("renders the server's figures for each range and organization it asked for", async () => {
    vi.mocked(api.get).mockImplementation((url, opts) => Promise.resolve({ data: url === "/admin/analytics"
      ? { ...body(opts.params.range), trials: { count: opts.params.org_id ? 2 : 10, list_value: 98, unpriced: 0 } }
      : { items: [{ id: "org-9", name: "Initech" }] } }));
    wrap();
    expect(await screen.findByTestId("trial-note")).toHaveTextContent(/^10 subscriptions/);
    fireEvent.click(screen.getByRole("button", { name: "24 hours" }));
    await waitFor(() => expect(lastParams("/admin/analytics")).toMatchObject({ range: "24h" }));
    fireEvent.focus(screen.getByLabelText("Filter by organization"));
    fireEvent.change(screen.getByLabelText("Filter by organization"), { target: { value: "ini" } });
    fireEvent.click(await screen.findByRole("option", { name: "Initech" }));
    await waitFor(() => expect(lastParams("/admin/analytics")).toMatchObject({ range: "24h", org_id: "org-9" }));
    await waitFor(() => expect(screen.getByTestId("trial-note")).toHaveTextContent(/^2 subscriptions/));
  });
});
