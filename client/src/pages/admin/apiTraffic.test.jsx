// The Traffic quick link, and the view it now opens.
//
// The API-health tile's "Traffic →" pointed at /admin/developers — the API KEYS page, which
// shows no request volume, no error rate and no latency. Nothing else in the console showed
// them either, so this is the destination that was missing rather than a route that was
// mistyped. What is pinned here:
//
//   * the tile no longer sends anyone to the keys page, and the destination it names exists;
//   * the view reports the SERVER's figures and renders "—" for anything the window did not
//     measure, because a request counter that was never flushed is not a measured zero;
//   * unmeasured and partial windows are said out loud instead of being drawn as a flat line.
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

vi.mock("../../api", () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  errMsg: (e) => e?.message ?? "failed",
  diagnoseLoadError: (e) => e?.message ?? "failed to load",
  AUTH_EXPIRED_EVENT: "zoiko:auth-expired",
}));
vi.mock("../../auth/AuthContext", () => ({
  useAuth: () => ({ user: { id: "u-me", full_name: "Ada Ops", role: "super_admin" }, logout: vi.fn() }),
}));

import api from "../../api";
import { ThemeProvider } from "../../theme/ThemeContext";
import KpiRow from "../../components/admin/sections/KpiRow";
import ApiTraffic from "./ApiTraffic";

const KPIS = {
  platform_health: { semantics: "current", status: "ok", ok: 7, total: 9, series: [], comparison: {} },
  live_sessions: { semantics: "current", value: 1, series: [], comparison: {} },
  at_risk_sessions: { semantics: "current", total: 0 },
  concurrent_audience: { semantics: "current", value: 0, measurement_state: "measured", series: [], comparison: {} },
  api_health: { semantics: "window", measurement_state: "measured", requests: 644, errors: 32, value: 4.97,
                p95_ms: 200, series: [], comparison: {} },
};

const payload = (over = {}) => ({
  generated_at: "2026-10-22T10:00:00Z",
  range: { mode: "24h", from: "2026-10-21T10:00:00Z", to: "2026-10-22T10:00:00Z", bucket_seconds: 3600 },
  measurement_state: "measured",
  coverage_pct: 100,
  requests: 644,
  errors: 32,
  value: 4.97,
  p95_ms: 200,
  p95_basis: "histogram_upper_bound",
  p95_overflow: false,
  series: [
    { label: "2026-10-21T10:00:00Z", t: "2026-10-21T10:00:00Z", value: 120 },
    { label: "2026-10-21T11:00:00Z", t: "2026-10-21T11:00:00Z", value: 524 },
  ],
  comparison: { current: 644, previous: 500, delta_pct: 28.8 },
  ...over,
});

const serve = (data) => vi.mocked(api.get).mockResolvedValue({ data });
const view = () =>
  render(
    <ThemeProvider><MemoryRouter><ApiTraffic /></MemoryRouter></ThemeProvider>
  );

const lastRange = () => vi.mocked(api.get).mock.calls.at(-1)?.[1]?.params?.range;

beforeEach(() => {
  vi.clearAllMocks();
  serve(payload());
});

describe("the Traffic quick link", () => {
  const tiles = () =>
    render(
      <ThemeProvider><MemoryRouter><KpiRow kpis={KPIS} range={{ mode: "24h" }} age="3s" /></MemoryRouter></ThemeProvider>
    );
  const traffic = () => screen.getByRole("link", { name: /Traffic/ });

  it("no longer opens the API keys page", () => {
    tiles();
    expect(traffic().getAttribute("href")).not.toBe("/admin/developers");
    expect(traffic().getAttribute("href")).not.toContain("developers");
  });

  it("opens the API traffic view", () => {
    tiles();
    expect(traffic().getAttribute("href")).toBe("/admin/status?tab=traffic");
  });

  it("leaves the other quick links alone", () => {
    tiles();
    expect(screen.getByRole("link", { name: /Current Status/ }).getAttribute("href")).toBe("/admin/status");
    expect(screen.getByRole("link", { name: /At Risk/ }).getAttribute("href")).toBe("#sessions-attention");
  });
});

describe("the destination matches its label", () => {
  it("is headed API traffic, not Developers", async () => {
    view();
    expect(await screen.findByRole("heading", { name: "API traffic" })).toBeInTheDocument();
    expect(screen.queryByText("API Keys")).toBeNull();
  });

  it("reports volume, error rate and latency — the three the label promises", async () => {
    view();
    await screen.findByRole("heading", { name: "API traffic" });
    expect(screen.getByText(/Requests · last 24 hours/)).toBeInTheDocument();
    expect(screen.getByText(/Error rate · last 24 hours/)).toBeInTheDocument();
    expect(screen.getByText(/p95 latency · last 24 hours/)).toBeInTheDocument();
  });

  it("asks the server for the window, and never computes one itself", async () => {
    view();
    await screen.findByRole("heading", { name: "API traffic" });
    await waitFor(() => expect(api.get).toHaveBeenCalledWith("/admin/api-traffic", { params: { range: "24h" } }));
  });

  it("re-asks the server when the range changes", async () => {
    view();
    await screen.findByRole("heading", { name: "API traffic" });
    fireEvent.click(screen.getByRole("button", { name: "7d" }));
    await waitFor(() => expect(lastRange()).toBe("7d"));
    fireEvent.click(screen.getByRole("button", { name: "Live" }));
    await waitFor(() => expect(lastRange()).toBe("live"));
  });
});

describe("it never invents a number", () => {
  it("says the window was not measured rather than drawing zero traffic", async () => {
    serve(payload({ measurement_state: "not_measured", requests: null, errors: null, value: null,
                    p95_ms: null, coverage_pct: null, series: [] }));
    view();
    expect(await screen.findByText(/No request minutes were recorded/)).toBeInTheDocument();
    // An error rate over zero requests does not exist; it must not read as 0%.
    expect(screen.queryByText("0%")).toBeNull();
  });

  it("calls a partial window a floor, with the coverage the server reported", async () => {
    serve(payload({ measurement_state: "partial", coverage_pct: 62.5 }));
    view();
    expect(await screen.findByText(/62.5% of the expected minutes/)).toBeInTheDocument();
    expect(screen.getByText(/a floor, not a complete count/)).toBeInTheDocument();
  });

  it("warns when p95 overflowed its top histogram bucket", async () => {
    serve(payload({ p95_overflow: true }));
    view();
    expect(await screen.findByText(/overflowed the top bucket/)).toBeInTheDocument();
  });

  it("does not warn about overflow when there was none", async () => {
    view();
    await screen.findByRole("heading", { name: "API traffic" });
    expect(screen.queryByText(/overflowed the top bucket/)).toBeNull();
  });
});

describe("when the endpoint fails", () => {
  it("says so and offers a retry, instead of an empty chart", async () => {
    vi.mocked(api.get).mockRejectedValue(new Error("boom"));
    view();
    const alert = await screen.findByRole("alert");
    expect(within(alert).getByRole("button", { name: "Retry" })).toBeInTheDocument();
    expect(screen.queryByText(/Requests · last 24 hours/)).toBeNull();
  });
});
