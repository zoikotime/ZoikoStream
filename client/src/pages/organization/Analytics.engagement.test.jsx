// Organization Analytics — the Recent Reports table's Engagement cell.
//
// THE BUG (production): events with no audience showed a bare "%" under a full-width rose
// bar. The cell interpolated the raw value, and the analytics service sends `engagement:
// null` when there was nothing to measure the score against (no telemetry, or no audience —
// it is interactions PER viewer). `{null}%` renders "%", `width: null%` is invalid CSS that
// the browser drops (so the fill takes the whole track), and engTone(null) is "low" (rose).
//
// A measured 0 is different and must stay "0%": an audience was there and did not interact.
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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

const report = (id, event, viewers, engagement, extra = {}) => ({
  id, event, date: "2026-09-27", viewers, watch_hours: viewers ? 0.5 : null,
  ...(engagement === "absent" ? {} : { engagement }), measurement_state: "measured", ...extra,
});
const REPORTS = [
  report("e1", "Town hall", 50, 100),
  report("e2", "Webinar", 8, 25),
  report("e3", "at", 1, 0),                   // an audience, no interaction: a real 0
  report("e4", "testing", 0, null),           // the production rows: nobody watched
  report("e5", "legacy row", 0, "absent"),    // no field at all
];
const PAYLOAD = {
  range: "30d", timezone: "UTC",
  summary: { peak_viewers_summed: 59, watch_hours: 1.5, total_watch_seconds: 5400, peak: 50, engagement: 42 },
  trends: { viewership: [], watch_time: [] }, top_events: [],
  reports: REPORTS, devices: null, locations: null, traffic_sources: null,
};

beforeEach(() => {
  vi.clearAllMocks();
  window.matchMedia = vi.fn().mockImplementation((query) => ({
    matches: query === "(prefers-reduced-motion: reduce)", media: query, onchange: null,
    addListener: vi.fn(), removeListener: vi.fn(), addEventListener: vi.fn(), removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }));
  vi.mocked(api.get).mockResolvedValue({ data: PAYLOAD });
});

const open = async () => {
  render(
    <ThemeProvider>
      <MemoryRouter initialEntries={["/organization/analytics"]}>
        <Analytics />
      </MemoryRouter>
    </ThemeProvider>,
  );
  await screen.findByText("Recent Reports");
  return screen.getAllByRole("table").find((t) => within(t).queryByText("Town hall"));
};

const bodyRows = (table) => within(table).getAllByRole("row").filter((r) => r.querySelector("td"));
const engagementOf = (table, event) => {
  const row = bodyRows(table).find((r) => r.querySelector("td").textContent.trim() === event);
  const cells = row.querySelectorAll("td");
  return cells[cells.length - 1];
};
const text = (cell) => cell.querySelector("span").textContent.trim();
const fill = (cell) => cell.querySelector("[aria-hidden='true'] > div");
const order = (table) => bodyRows(table).map((r) => r.querySelector("td").textContent.trim());

describe("the Engagement column", () => {
  it("shows the production rows — no audience, engagement null — as a dash, never a bare %", async () => {
    const table = await open();
    const cell = engagementOf(table, "testing");
    expect(text(cell)).toBe("—");
    expect(cell.querySelector("span")).toHaveAttribute("title", "Not measured");
    expect(fill(cell)).toBeNull();                        // a neutral, empty track
    expect(cell.innerHTML).not.toMatch(/null%|undefined%|NaN%/);
  });

  it("renders measured values as percentages, 0 included", async () => {
    const table = await open();
    expect(text(engagementOf(table, "Town hall"))).toBe("100%");
    expect(text(engagementOf(table, "Webinar"))).toBe("25%");
    expect(text(engagementOf(table, "at"))).toBe("0%");
  });

  it("treats a missing field like null", async () => {
    const table = await open();
    const cell = engagementOf(table, "legacy row");
    expect(text(cell)).toBe("—");
    expect(fill(cell)).toBeNull();
  });

  it("never renders a % without a number in front of it", async () => {
    const table = await open();
    for (const row of bodyRows(table)) {
      expect(row.textContent).not.toMatch(/(^|[^\d])%/);
    }
  });

  it("fills the bar to the score: empty at 0, proportional otherwise", async () => {
    const table = await open();
    expect(fill(engagementOf(table, "at")).style.width).toBe("0%");
    expect(fill(engagementOf(table, "Webinar")).style.width).toBe("25%");
    expect(fill(engagementOf(table, "Town hall")).style.width).toBe("100%");
  });

  it("sorts on the number: 0 is the lowest score, and unmeasured rows stay apart, last", async () => {
    const table = await open();
    const header = within(table).getByRole("button", { name: /engagement/i });
    fireEvent.click(header);                              // ascending
    await waitFor(() => expect(order(table).slice(0, 3)).toEqual(["at", "Webinar", "Town hall"]));
    expect(order(table).slice(3).sort()).toEqual(["legacy row", "testing"]);
    fireEvent.click(header);                              // descending
    await waitFor(() => expect(order(table).slice(0, 3)).toEqual(["Town hall", "Webinar", "at"]));
    expect(order(table).slice(3).sort()).toEqual(["legacy row", "testing"]);
  });
});
