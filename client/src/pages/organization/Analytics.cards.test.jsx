// Organization Analytics — the four top-level cards, against the payload the service sends.
//
// The reported failure was all four reading empty after a completed live session: peaks at
// 0 and Watch Time / Engagement Score at an em dash. utils/analyticsFormat.test.js already
// pins the two formatters in isolation; what had no coverage was the CARDS — which summary
// field each one reads, and that a figure the backend measured survives the trip onto the
// screen. A card wired to the wrong key, or one that turned a measured 0 into "—", would
// have looked exactly like the bug and passed every existing test.
//
// Reduced motion is switched on below so ui/Counter renders its value directly. Left off,
// the counters tween from 0 through an IntersectionObserver that src/test/setup.js stubs as
// a deliberately inert no-op, so every numeric card reads "0" in jsdom no matter what the
// payload said — which is the one thing these assertions are about.
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

vi.mock("../../api", async (importOriginal) => {
  const real = await importOriginal();
  return { ...real, default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() } };
});
vi.mock("../../ui/Toast", () => ({
  notify: { error: vi.fn(), success: vi.fn(), alert: vi.fn(), warning: vi.fn(), info: vi.fn() },
}));

import api from "../../api";
import { ThemeProvider } from "../../theme/ThemeContext";
import Analytics from "./Analytics";

// A completed 30-minute session: 12 peak concurrent, 6000 viewer-seconds, score 43.
const MEASURED = {
  range: "30d",
  timezone: "UTC",
  summary: {
    peak_viewers_summed: 12,
    watch_hours: 6000 / 3600,
    total_watch_seconds: 6000,
    peak: 12,
    engagement: 43,
  },
  trends: { viewership: [{ label: "Sep 27", value: 12 }], watch_time: [{ label: "Sep 27", value: 1.7 }] },
  top_events: [{ label: "Product Launch", value: 12 }],
  reports: [{
    id: "e1", event: "Product Launch", date: "2026-09-27", viewers: 12,
    watch_hours: 6000 / 3600, total_watch_seconds: 6000, average_watch_seconds: 500,
    engagement: 43, measurement_state: "measured",
    chat_count: 5, question_count: 2, reaction_count: 8,
  }],
  devices: null, locations: null, traffic_sources: null,
};

const payload = (summary) => ({ ...MEASURED, summary: { ...MEASURED.summary, ...summary } });

const open = () =>
  render(
    <ThemeProvider>
      <MemoryRouter initialEntries={["/organization/analytics"]}>
        <Analytics />
      </MemoryRouter>
    </ThemeProvider>
  );

/** The figure rendered on the STATS CARD with this title.
 *
 * Matched on the card's shape — a title <p> immediately followed by a value <p> — because
 * "Watch Time" is also a column header in the reports table further down the page, and a
 * plain text query picks up both.
 */
const cardValue = (title) => {
  for (const label of screen.getAllByText(title)) {
    const ps = label.parentElement?.querySelectorAll("p") ?? [];
    if (ps.length >= 2 && ps[0].textContent.trim() === title) {
      return ps[1].textContent.trim();
    }
  }
  throw new Error(`no stats card titled "${title}"`);
};

/** Resolves once the cards have rendered. */
const cardsReady = () => screen.findAllByText("Event Peak Viewers");

const rangesRequested = () =>
  vi.mocked(api.get).mock.calls
    .filter(([url]) => url === "/organization/analytics")
    .map(([, config]) => config?.params?.range);

beforeEach(() => {
  vi.clearAllMocks();
  // A real user preference, not a test hook: it is the supported way to get a static figure.
  window.matchMedia = vi.fn().mockImplementation((query) => ({
    matches: query === "(prefers-reduced-motion: reduce)",
    media: query, onchange: null,
    addListener: vi.fn(), removeListener: vi.fn(),
    addEventListener: vi.fn(), removeEventListener: vi.fn(), dispatchEvent: vi.fn(),
  }));
  vi.mocked(api.get).mockResolvedValue({ data: MEASURED });
});

describe("a completed session reaches all four cards", () => {
  it("shows the measured figures rather than empty ones", async () => {
    open();
    await cardsReady();
    expect(cardValue("Event Peak Viewers")).toBe("12");
    expect(cardValue("Peak Concurrent Viewers")).toBe("12");
    expect(cardValue("Watch Time")).toBe("1.7 hrs");
    expect(cardValue("Engagement Score")).toBe("43%");
  });

  it("reads peak concurrency and the summed peak from their own fields", async () => {
    // Distinct values, so a card wired to the wrong key cannot pass by coincidence.
    vi.mocked(api.get).mockResolvedValue({ data: payload({ peak_viewers_summed: 30, peak: 12 }) });
    open();
    await cardsReady();
    expect(cardValue("Event Peak Viewers")).toBe("30");
    expect(cardValue("Peak Concurrent Viewers")).toBe("12");
  });

  it("renders watch time in minutes for a short session", async () => {
    vi.mocked(api.get).mockResolvedValue({ data: payload({ watch_hours: 12 / 60, total_watch_seconds: 720 }) });
    open();
    await cardsReady();
    expect(cardValue("Watch Time")).toBe("12 min");
  });
});

describe("measured zero is not the same as never measured", () => {
  it("renders a measured zero as a figure", async () => {
    vi.mocked(api.get).mockResolvedValue({
      data: payload({ peak_viewers_summed: 0, watch_hours: 0, total_watch_seconds: 0, peak: 0, engagement: 0 }),
    });
    open();
    await cardsReady();
    expect(cardValue("Watch Time")).toBe("0 min");
    expect(cardValue("Engagement Score")).toBe("0%");
    expect(cardValue("Event Peak Viewers")).toBe("0");
  });

  it("renders an em dash only where the service said nothing was measured", async () => {
    vi.mocked(api.get).mockResolvedValue({
      data: payload({ watch_hours: null, total_watch_seconds: null, engagement: null }),
    });
    open();
    await cardsReady();
    expect(cardValue("Watch Time")).toBe("—");
    expect(cardValue("Engagement Score")).toBe("—");
    // The peaks were still counted, so they are still numbers.
    expect(cardValue("Peak Concurrent Viewers")).toBe("12");
  });
});

describe("the date range drives the request", () => {
  it("asks for the default 30-day window on first load", async () => {
    open();
    await waitFor(() => expect(rangesRequested()).toEqual(["30d"]));
  });

  it("re-requests and re-renders when the range changes", async () => {
    open();
    await cardsReady();

    vi.mocked(api.get).mockResolvedValue({
      data: payload({ peak: 4, watch_hours: 0.5, total_watch_seconds: 1800, engagement: 10 }),
    });
    fireEvent.change(screen.getByLabelText("Date range"), { target: { value: "7d" } });

    await waitFor(() => expect(rangesRequested()).toContain("7d"));
    await waitFor(() => expect(cardValue("Peak Concurrent Viewers")).toBe("4"));
    expect(cardValue("Watch Time")).toBe("30 min");
    expect(cardValue("Engagement Score")).toBe("10%");
  });

  it("carries every supported window through to the query", async () => {
    open();
    await cardsReady();
    for (const key of ["7d", "90d", "12m"]) {
      fireEvent.change(screen.getByLabelText("Date range"), { target: { value: key } });
      await waitFor(() => expect(rangesRequested()).toContain(key));
    }
  });
});

describe("a failure is not shown as zeroes", () => {
  it("says the figures could not be loaded instead of drawing empty cards", async () => {
    vi.mocked(api.get).mockRejectedValue(new Error("boom"));
    open();
    expect(await screen.findByText(/Couldn't load analytics/i)).toBeInTheDocument();
    expect(screen.queryByText("Event Peak Viewers")).not.toBeInTheDocument();
  });
});
