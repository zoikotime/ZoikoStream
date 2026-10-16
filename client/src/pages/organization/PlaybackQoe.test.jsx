// /organization/playback's viewer-side measurement: Playback starts, Rebuffer ratio and
// Failures by reason, from GET /organization/playback/qoe (server-aggregated), live and
// replay apart, per event and window — and the honest states when there is nothing to show.
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api", () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  errMsg: (e) => e?.message ?? "Something went wrong.",
}));
vi.mock("../../auth/AuthContext", () => ({
  useAuth: () => ({ user: { full_name: "Ada Admin", email: "ada@example.com" }, logout: vi.fn() }),
}));

import api from "../../api";
import { ThemeProvider } from "../../theme/ThemeContext";
import PlaybackAccess from "./PlaybackAccess";

const EV1 = "11111111-1111-4111-8111-111111111111";
const EV2 = "22222222-2222-4222-8222-222222222222";
const event = (id, title, start) => ({
  id, title, start_time: start, visibility: "public", status: "ended", registration_required: false,
  password_protected: false, replay_enabled: true, waiting_room_enabled: false, end_time: null,
});
const EVENTS = { total: 2, page: 1, page_size: 100,
                 items: [event(EV1, "Town hall", "2026-10-10T10:00:00Z"), event(EV2, "Webinar", "2026-10-01T10:00:00Z")] };

const NONE = { sessions: 0, playback_starts: 0, playback_duration_ms: 0, rebuffer_duration_ms: 0, rebuffer_count: 0,
               rebuffer_ratio: null, startup_ms_median: null, failed_sessions: 0, failures: [] };
const figures = (over) => ({ ...NONE, ...over });
const TOWN_HALL = {
  live: figures({
    sessions: 160, playback_starts: 148, playback_duration_ms: 600_000, rebuffer_duration_ms: 14_400,
    rebuffer_count: 7, rebuffer_ratio: 0.024, startup_ms_median: 1_200, failed_sessions: 13,
    failures: [{ reason: "network_error", count: 8 }, { reason: "autoplay_blocked", count: 3 },
               { reason: "stream_unavailable", count: 2 }],
  }),
  replay: figures({ sessions: 5, playback_starts: 5, playback_duration_ms: 3_600_000, rebuffer_ratio: 0 }),
};
const EMPTY = { live: NONE, replay: NONE, total: { sessions: 0, playback_starts: 0, failed_sessions: 0, failures: [] } };
const RECENT = { live: figures({ sessions: 30, startup_ms_median: 1_450 }), replay: NONE,
                 total: { sessions: 40, playback_starts: 34, failed_sessions: 6, failures: [] } };

let perEvent;
beforeEach(() => {
  vi.clearAllMocks();
  perEvent = (params) => (params.event_id === EV1 ? TOWN_HALL : EMPTY);
  vi.mocked(api.get).mockImplementation((url, config = {}) => {
    if (url === "/events") return Promise.resolve({ data: EVENTS });
    if (url === "/organization/playback/qoe") {
      const params = config.params || {};
      return Promise.resolve({ data: params.event_id ? perEvent(params) : RECENT });
    }
    return Promise.resolve({ data: [] });
  });
});

const show = () => render(
  <ThemeProvider>
    <MemoryRouter>
      <PlaybackAccess />
    </MemoryRouter>
  </ThemeProvider>
);
const fact = (label) => screen.getByText(label, { selector: "dt" }).parentElement;
const qoeCalls = () => vi.mocked(api.get).mock.calls.filter(([url]) => url === "/organization/playback/qoe")
  .map(([, config]) => config.params);
const statRow = (label) => screen.getByText(label).closest("div").parentElement;

describe("viewer-side measurement", () => {
  it("shows the real starts, rebuffer ratio and failures — and no 'not ingested' blank", async () => {
    show();
    await waitFor(() => expect(within(fact("Playback starts")).getByText("148")).toBeInTheDocument());
    expect(within(fact("Playback starts")).getByText(/of 160 playback sessions · not unique viewers/)).toBeInTheDocument();
    expect(within(fact("Rebuffer ratio")).getByText("2.4%")).toBeInTheDocument();
    expect(within(fact("Rebuffer ratio")).getByText("7 stalls · 14 s of 10 min watched")).toBeInTheDocument();
    const failures = within(fact("Failures by reason")).getByRole("list");
    expect(within(failures).getAllByRole("listitem").map((li) => li.textContent))
      .toEqual(["Network error8", "Autoplay blocked3", "Stream unavailable2"]);
    expect(screen.queryByText(/not ingested/i)).toBeNull();
    // Aggregated by the server for this event, over the default window.
    expect(qoeCalls()).toContainEqual({ event_id: EV1, window: "all" });
  });

  it("fills the 24-hour cards from the organization's own sessions", async () => {
    show();
    await waitFor(() => expect(within(statRow("Playback failures (24h)")).getByText("6")).toBeInTheDocument());
    expect(within(statRow("Median live start (24h)")).getByText("1.5 s")).toBeInTheDocument();
    expect(qoeCalls()).toContainEqual({ window: "24h" });
  });

  it("keeps live and replay apart, and says so when there were no failures", async () => {
    const user = userEvent.setup();
    show();
    await waitFor(() => expect(within(fact("Playback starts")).getByText("148")).toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: "Replay" }));
    expect(within(fact("Playback starts")).getByText("5")).toBeInTheDocument();
    expect(within(fact("Rebuffer ratio")).getByText("0%")).toBeInTheDocument();
    expect(within(fact("Failures by reason")).getByText("No playback failures")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Replay" })).toHaveAttribute("aria-pressed", "true");
  });

  it("says there is no playback data yet, rather than showing zeros", async () => {
    perEvent = () => EMPTY;
    show();
    await waitFor(() => expect(within(fact("Failures by reason")).getByText("No playback data yet")).toBeInTheDocument());
    for (const label of ["Playback starts", "Rebuffer ratio"]) {
      expect(within(fact(label)).getByText("—")).toBeInTheDocument();
      expect(within(fact(label)).getByText("No playback data yet")).toBeInTheDocument();
    }
    expect(screen.queryByText("0%")).toBeNull();
  });

  it("asks the server again for another window, and for another event", async () => {
    const user = userEvent.setup();
    perEvent = (params) => {
      if (params.event_id !== EV1) return EMPTY;
      return params.window === "24h"
        ? { ...TOWN_HALL, live: figures({ sessions: 2, playback_starts: 2, playback_duration_ms: 1_000, rebuffer_ratio: 0.0004 }) }
        : TOWN_HALL;
    };
    show();
    await waitFor(() => expect(within(fact("Playback starts")).getByText("148")).toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: "24 hours" }));
    await waitFor(() => expect(within(fact("Playback starts")).getByText("2")).toBeInTheDocument());
    expect(within(fact("Rebuffer ratio")).getByText("<0.1%")).toBeInTheDocument();
    expect(qoeCalls()).toContainEqual({ event_id: EV1, window: "24h" });

    await user.click(screen.getByRole("option", { name: /webinar/i }));
    await waitFor(() => expect(qoeCalls()).toContainEqual({ event_id: EV2, window: "24h" }));
    await waitFor(() => expect(within(fact("Failures by reason")).getByText("No playback data yet")).toBeInTheDocument());
    expect(screen.queryByText("148")).toBeNull();
  });

  it("shows an error it can retry when the figures cannot be loaded", async () => {
    const user = userEvent.setup();
    let fail = true;
    perEvent = () => {
      if (fail) throw new Error("Service unavailable");
      return TOWN_HALL;
    };
    vi.mocked(api.get).mockImplementation((url, config = {}) => {
      if (url === "/events") return Promise.resolve({ data: EVENTS });
      if (url === "/organization/playback/qoe") {
        const params = config.params || {};
        if (!params.event_id) return Promise.resolve({ data: RECENT });
        try {
          return Promise.resolve({ data: perEvent(params) });
        } catch (e) {
          return Promise.reject(e);
        }
      }
      return Promise.resolve({ data: [] });
    });
    show();
    expect(await screen.findByText("Couldn't load playback quality")).toBeInTheDocument();
    expect(screen.getByText("Service unavailable")).toBeInTheDocument();
    fail = false;
    await user.click(screen.getByRole("button", { name: /retry/i }));
    await waitFor(() => expect(within(fact("Playback starts")).getByText("148")).toBeInTheDocument());
  });
});
