// Organization Analytics — the "Top Performing Events" card (RankedBars).
//
// Reported: content inside the card overlapped. Measured in a real browser, the card header
// was one row that could not wrap: the title column (min-w-0) was squeezed to 27px at 320px
// and 67px at 360px — narrower than the word "Performing", which then painted over the
// metric switch — while the switch itself was squeezed until "Watch time" broke in two (on
// every phone width, and at 1280px with the Engagement metric). The header now wraps, the
// title column starts from 10rem, and a truncated event name carries its full name.
//
// jsdom has no layout, so the GEOMETRY was verified in Chromium at 320–1440px (no overlap,
// no spill, no horizontal overflow; desktop pixel-identical to before). What is pinned here
// is the structure that geometry depends on, plus the card's data and its empty, loading and
// error states, which the fix must not have changed.
import { fireEvent, render, screen, within } from "@testing-library/react";
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
import { formatWatchTime } from "../../utils/analyticsFormat";
import Analytics from "./Analytics";

const LONG = "Quarterly All-Hands Town Hall & Product Roadmap Deep-Dive — Q4 2026 (EMEA + APAC replay)";
const UNBROKEN = "ProductLaunchKeynoteWithAnExtremelyLongUnbrokenNameThatCannotWrapAnywhere2026";
const row = (id, event, viewers, watch_hours, engagement) => ({
  id, event, date: "2026-10-02", viewers, watch_hours,
  total_watch_seconds: watch_hours == null ? null : Math.round(watch_hours * 3600), engagement, measurement_state: "measured",
});
const REPORTS = [
  row("e1", LONG, 1234567, 12.5, 40),
  row("e2", UNBROKEN, 45000, 98765.4, 88),
  row("e3", "Town hall", 8000, 312.25, 61),
];
const PAYLOAD = {
  range: "30d", timezone: "UTC",
  summary: { peak_viewers_summed: 1287567, watch_hours: 99090.15, total_watch_seconds: 356724540, peak: 1234567, engagement: 63 },
  trends: { viewership: [{ label: "Oct 2", value: 1234567 }], watch_time: [{ label: "Oct 2", value: 99090.15 }] },
  top_events: [{ label: LONG, value: 1234567 }], reports: REPORTS,
  devices: null, locations: null, traffic_sources: null, session_summary: null, breakdowns_note: "Not measured yet.",
};

beforeEach(() => {
  vi.clearAllMocks();
  api.get.mockResolvedValue({ data: PAYLOAD });
});

const open = () =>
  render(
    <ThemeProvider>
      <MemoryRouter initialEntries={["/organization/analytics"]}>
        <Analytics />
      </MemoryRouter>
    </ThemeProvider>
  );

/** The card, its header row, title column and metric switch. */
async function topEvents() {
  const heading = await screen.findByRole("heading", { name: "Top Performing Events" });
  const titleColumn = heading.parentElement;
  const header = titleColumn.parentElement;
  return { card: header.parentElement, header, titleColumn, switcher: within(header).getByRole("group") };
}
/** [label, value] per ranked row, in order. */
const rows = (card) =>
  within(card).getAllByRole("listitem").map((li) => {
    const [label, value] = li.querySelectorAll("span");
    return [label.textContent, value.textContent];
  });

describe("Top Performing Events", () => {
  it("ranks the events by the selected metric, each with its value", async () => {
    open();
    const { card, switcher } = await topEvents();
    expect(within(card).getByText("Ranked by peak viewers")).toBeInTheDocument();
    expect(rows(card)).toEqual([
      [LONG, (1234567).toLocaleString()],
      [UNBROKEN, (45000).toLocaleString()],
      ["Town hall", (8000).toLocaleString()],
    ]);

    fireEvent.click(within(switcher).getByRole("button", { name: "Watch time" }));
    expect(within(card).getByText("Ranked by hours watched")).toBeInTheDocument();
    expect(rows(card)).toEqual([
      [UNBROKEN, formatWatchTime(98765.4)],
      ["Town hall", formatWatchTime(312.25)],
      [LONG, formatWatchTime(12.5)],
    ]);

    fireEvent.click(within(switcher).getByRole("button", { name: "Engagement" }));
    expect(within(card).getByText("Ranked by engagement score")).toBeInTheDocument();
    expect(rows(card)).toEqual([[UNBROKEN, "88%"], ["Town hall", "61%"], [LONG, "40%"]]);
    expect(within(switcher).getByRole("button", { name: "Engagement" })).toHaveAttribute("aria-pressed", "true");
  });

  it("keeps a long name to one truncated line beside its value, with the full name available", async () => {
    open();
    const { card } = await topEvents();
    for (const li of within(card).getAllByRole("listitem")) {
      const [label, value] = li.querySelectorAll("span");
      // Label and value are separate boxes on one row: the label may shrink and truncate,
      // the value may not.
      expect(label.parentElement).toBe(value.parentElement);
      expect(label).toHaveClass("truncate");
      expect(value).toHaveClass("shrink-0");
      // A truncated name is still fully available: as the tooltip and as the text itself.
      expect(label).toHaveAttribute("title", label.textContent);
    }
    expect(within(card).getByTitle(LONG)).toHaveTextContent(LONG);
    expect(within(card).getByTitle(UNBROKEN)).toHaveTextContent(UNBROKEN);
  });

  it("lets the header wrap, so the title is never squeezed under the metric switch", async () => {
    open();
    const { header, titleColumn, switcher } = await topEvents();
    // The row may wrap, and the title column starts from 10rem — wider than any word in the
    // title — so when both cannot fit, the switch moves to the next line instead.
    expect(header).toHaveClass("flex", "flex-wrap");
    expect(titleColumn).toHaveClass("min-w-0", "flex-1", "basis-40");
    expect(switcher.parentElement).toBe(header);
    // All three metrics are still offered, in the same order.
    expect(within(switcher).getAllByRole("button").map((b) => b.textContent)).toEqual(["Viewers", "Watch time", "Engagement"]);
  });

  it("still says why it is empty, per metric", async () => {
    api.get.mockResolvedValue({
      data: { ...PAYLOAD, reports: [row("z1", "Rehearsal", 0, 0, null)] },
    });
    open();
    const { card, switcher } = await topEvents();
    expect(within(card).getByText("No events with viewers in this window yet.")).toBeInTheDocument();
    fireEvent.click(within(switcher).getByRole("button", { name: "Watch time" }));
    expect(within(card).getByText("No events with watch time in this window yet.")).toBeInTheDocument();
    fireEvent.click(within(switcher).getByRole("button", { name: "Engagement" }));
    expect(within(card).getByText("No events with engagement in this window yet.")).toBeInTheDocument();
    expect(within(card).queryAllByRole("listitem")).toHaveLength(0);
  });

  it("with no events at all, says the window is empty", async () => {
    api.get.mockResolvedValue({ data: { ...PAYLOAD, reports: [] } });
    open();
    const { card } = await topEvents();
    expect(within(card).getByText("No events in this window yet.")).toBeInTheDocument();
  });

  it("is not shown while the analytics are loading, nor when they failed to load", async () => {
    api.get.mockReturnValue(new Promise(() => {}));
    const loading = open();
    expect(screen.queryByRole("heading", { name: "Top Performing Events" })).not.toBeInTheDocument();
    loading.unmount();

    api.get.mockRejectedValue(Object.assign(new Error("boom"), { response: { status: 500 } }));
    open();
    expect(await screen.findByText(/couldn't load analytics/i)).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Top Performing Events" })).not.toBeInTheDocument();
  });
});
