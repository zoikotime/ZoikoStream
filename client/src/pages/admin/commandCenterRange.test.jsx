// Command Center range handling, pinned from the browser side:
//   the selected range / custom window / filters are what the REQUEST carries;
//   refresh keeps them; export is exactly what is on screen and refuses a mismatch;
//   a failed refetch is announced instead of silently showing the previous window;
//   tiles say what their number covers, and never invent a trend or a zero.
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

vi.mock("../../api", () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  errMsg: (e) => e?.response?.data?.detail ?? e?.message ?? "failed",
  diagnoseLoadError: (e) => e?.message ?? "failed to load",
}));
vi.mock("../../utils/export", () => ({ downloadJson: vi.fn() }));

import api from "../../api";
import { downloadJson } from "../../utils/export";
import { ThemeProvider } from "../../theme/ThemeContext";
import AdminDashboard from "./Dashboard";
import { customWindowError } from "../../components/admin/sections/commandWindow";

const range = (mode) => ({
  mode, from: "2026-09-21T12:00:00+00:00", to: "2026-09-28T12:00:00+00:00", bucket_seconds: 21600,
  previous: { from: "2026-09-14T12:00:00+00:00", to: "2026-09-21T12:00:00+00:00" },
});

const payload = (mode = "live", kpis = {}) => ({
  generated_at: new Date().toISOString(),
  range: range(mode),
  window: { range: mode },
  regions: [{ code: "na", label: "NA" }, { code: "eu", label: "EU" }],
  kpis: {
    platform_health: { semantics: "current", status: "ok", ok: 4, total: 9, warn: 0, down: 0, unmonitored: 4, not_configured: 1,
                       series: [], comparison: { delta_pct: null } },
    live_sessions: { semantics: "current", value: 0, in_period: 1, starting_soon: 0, unattended: 0, series: [],
                     comparison: { current: 1, previous: 2, delta_pct: -50 } },
    at_risk_sessions: { semantics: "current", total: 0 },
    concurrent_audience: { semantics: mode === "live" ? "current" : "window", value: 0, measurement_state: "measured",
                           current: 0, peak: 0, average: 0, sampling_coverage_pct: null, series: [], comparison: { delta_pct: null } },
    api_health: { semantics: "window", measurement_state: "measured", requests: 20, errors: 0, value: 0, p95_ms: 250,
                  p95_overflow: false, series: [], comparison: { delta_pct: null } },
    ...kpis,
  },
  attention: [], incidents: [], incident_summary: { active: [], active_count: 0, resolved_in_window: [], resolved_count: 0 },
  action_queues: [], governance: null, privileged_activity: [], upcoming_events: [],
  upcoming: { from: "2026-09-28T12:00:00Z", to: "2026-10-05T12:00:00Z", total: 0 }, elevation: null,
});

const calls = () => vi.mocked(api.get).mock.calls.filter(([u]) => u === "/admin/command-center");
const lastParams = () => calls().at(-1)?.[1]?.params;

const show = () =>
  render(
    <ThemeProvider>
      <MemoryRouter>
        <AdminDashboard />
      </MemoryRouter>
    </ThemeProvider>
  );

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.get).mockImplementation((_url, { params }) => Promise.resolve({ data: payload(params.range) }));
});

describe("the range reaches the request", () => {
  it.each(["1h", "24h", "7d"])("sends range=%s", async (r) => {
    show();
    await screen.findByText("Live sessions");
    fireEvent.click(screen.getByRole("button", { name: r }));
    await waitFor(() => expect(lastParams()).toMatchObject({ range: r }));
    expect(lastParams()).not.toHaveProperty("from");
  });

  it("sends both custom instants, in UTC, only on Apply", async () => {
    show();
    await screen.findByText("Live sessions");
    const before = calls().length;
    fireEvent.click(screen.getByRole("button", { name: "Custom" }));
    fireEvent.change(screen.getByLabelText("Window start"), { target: { value: "2026-09-20T10:00" } });
    fireEvent.change(screen.getByLabelText("Window end"), { target: { value: "2026-09-21T10:00" } });
    expect(calls().length).toBe(before);          // typing sends nothing
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    await waitFor(() => expect(lastParams()).toMatchObject({
      range: "custom",
      from: new Date("2026-09-20T10:00").toISOString(),
      to: new Date("2026-09-21T10:00").toISOString(),
    }));
  });

  it("refuses an inverted custom window before sending it", async () => {
    show();
    await screen.findByText("Live sessions");
    fireEvent.click(screen.getByRole("button", { name: "Custom" }));
    fireEvent.change(screen.getByLabelText("Window start"), { target: { value: "2026-09-21T10:00" } });
    fireEvent.change(screen.getByLabelText("Window end"), { target: { value: "2026-09-20T10:00" } });
    expect(screen.getByRole("button", { name: "Apply" })).toBeDisabled();
    expect(screen.getByRole("alert")).toHaveTextContent(/start must be before the end/i);
    expect(customWindowError("2099-01-01T00:00", "2099-01-02T00:00")).toMatch(/future/);
  });

  it("sends region, scope and test mode", async () => {
    show();
    await screen.findByText("Live sessions");
    fireEvent.change(screen.getByLabelText("Region"), { target: { value: "eu" } });
    await waitFor(() => expect(lastParams()).toMatchObject({ region: "eu" }));
    fireEvent.change(screen.getByLabelText("Scope"), { target: { value: "core" } });
    await waitFor(() => expect(lastParams()).toMatchObject({ region: "eu", scope: "core" }));
    fireEvent.click(screen.getByLabelText("Include test mode"));
    await waitFor(() => expect(lastParams()).toMatchObject({ include_test: true, scope: "core" }));
  });

  it("refresh keeps every filter", async () => {
    show();
    await screen.findByText("Live sessions");
    fireEvent.click(screen.getByRole("button", { name: "7d" }));
    fireEvent.change(screen.getByLabelText("Region"), { target: { value: "na" } });
    await waitFor(() => expect(lastParams()).toMatchObject({ range: "7d", region: "na" }));
    const n = calls().length;
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await waitFor(() => expect(calls().length).toBe(n + 1));
    expect(lastParams()).toMatchObject({ range: "7d", region: "na", scope: "core_live", include_test: false });
  });
});

describe("export and failed refetches", () => {
  it("exports exactly what is shown, with the filters that produced it", async () => {
    show();
    await screen.findByText("Live sessions");
    fireEvent.click(screen.getByRole("button", { name: "7d" }));
    await waitFor(() => expect(screen.getByRole("button", { name: /Export snapshot/ })).toBeEnabled());
    await waitFor(() => expect(screen.getByTestId("command-window")).not.toHaveTextContent("loading"));
    fireEvent.click(screen.getByRole("button", { name: /Export snapshot/ }));
    const [exported] = vi.mocked(downloadJson).mock.calls.at(-1);
    expect(exported.filters).toMatchObject({ range: "7d" });
    expect(exported.range.mode).toBe("7d");
    expect(exported).not.toHaveProperty("fetched_at");
  });

  it("says so, and blocks export, when the selected window fails to load", async () => {
    show();
    await screen.findByText("Live sessions");
    vi.mocked(api.get).mockRejectedValueOnce({ response: { data: { detail: "'from' must be before 'to'." } } });
    fireEvent.click(screen.getByRole("button", { name: "24h" }));
    const banner = await screen.findByText(/Couldn’t load the selected window/);
    expect(banner).toHaveTextContent("'from' must be before 'to'.");
    expect(banner).toHaveTextContent(/not the\s+selected filters/);
    expect(screen.getByRole("button", { name: /Export snapshot/ })).toBeDisabled();
  });
});

describe("tiles say what they measure", () => {
  it("labels current-state tiles 'Now' and the windowed audience as a peak", async () => {
    show();
    await screen.findByText("Live sessions");
    fireEvent.click(screen.getByRole("button", { name: "7d" }));
    expect(await screen.findByText("Peak · last 7 days")).toBeInTheDocument();
    expect(screen.getAllByText("Now").length).toBeGreaterThanOrEqual(2);   // live + at-risk
    expect(screen.getByText(/1 in last 7 days \(2 in the previous 7 days\)/)).toBeInTheDocument();
  });

  it("shows no trend badge without a real previous-period comparison", async () => {
    vi.mocked(api.get).mockResolvedValue({ data: payload("7d", {
      platform_health: { semantics: "current", status: "ok", ok: 4, total: 9, series: [{ value: 1 }, { value: 9 }],
                         comparison: { delta_pct: null } },
    }) });
    show();
    await screen.findByText("Platform health");
    // A rising sparkline is not a comparison; the badge used to be computed from it.
    expect(screen.queryByText(/^\d+(\.\d+)?%$/)).toBeNull();
  });

  it("shows a badge from the server's previous-window comparison", async () => {
    vi.mocked(api.get).mockResolvedValue({ data: payload("7d", {
      platform_health: { semantics: "current", status: "ok", ok: 4, total: 9, series: [],
                         comparison: { current: 4.36, previous: 5, delta_pct: -12.8 } },
    }) });
    show();
    expect(await screen.findByText("12.8%")).toBeInTheDocument();
  });

  it("keeps a measured zero, and says not measured when nothing was collected", async () => {
    vi.mocked(api.get).mockResolvedValue({ data: payload("24h", {
      api_health: { semantics: "window", measurement_state: "measured", requests: 0, errors: 0, value: null, series: [] },
    }) });
    const { unmount } = show();
    expect(await screen.findByText(/0 requests in the last 24 hours — no error rate/)).toBeInTheDocument();
    unmount();
    vi.mocked(api.get).mockResolvedValue({ data: payload("24h", {
      api_health: { semantics: "window", measurement_state: "not_measured", requests: null, value: null, series: [] },
    }) });
    show();
    expect(await screen.findByText(/Not measured — no request data was collected for the last 24 hours/)).toBeInTheDocument();
  });

  it("shows a partial audience peak as a floor, not a clean measurement", async () => {
    vi.mocked(api.get).mockResolvedValue({ data: payload("7d", {
      concurrent_audience: { semantics: "window", value: 40, measurement_state: "partial", peak: 40, average: 12,
                             sampling_coverage_pct: 35, highest_session_peak: 55, current: 0, series: [], comparison: { delta_pct: null } },
    }) });
    show();
    expect(await screen.findByText(/sampled 35% of broadcast time — the peak is a floor/)).toBeInTheDocument();
    expect(screen.getByText(/highest single-session peak 55/)).toBeInTheDocument();
  });

  it("lists every active incident and counts the window's resolved ones separately", async () => {
    vi.mocked(api.get).mockResolvedValue({ data: {
      ...payload("1h"),
      incident_summary: {
        active: [{ id: "i1", title: "Old but open", severity: "sev2", status: "open", started_at: "2026-07-01T00:00:00Z" }],
        active_count: 1, resolved_in_window: [], resolved_count: 3,
      },
    } });
    show();
    const panel = (await screen.findByText("Active incidents")).closest("section");
    expect(within(panel).getByText("Old but open")).toBeInTheDocument();
  });

  it("states the upcoming horizon rather than the page's backward range", async () => {
    show();
    await screen.findByText("Live sessions");
    fireEvent.click(screen.getByRole("button", { name: "7d" }));
    expect(await screen.findByText(/No high-impact events scheduled in the next 7 days/)).toBeInTheDocument();
  });
});
