import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import api from "../../api";
import { ThemeProvider } from "../../theme/ThemeContext";
import EventsCalendar from "./EventsCalendar";

vi.mock("../../api", () => ({
  default: { get: vi.fn() },
}));

const ev = (id, title, start_time, end_time) => ({
  id,
  title,
  slug: id,
  status: "scheduled",
  display_status: "scheduled",
  start_time,
  end_time,
  visibility: "public",
  registration_required: false,
});

function renderCalendar(items, month = "2026-10") {
  vi.mocked(api.get).mockResolvedValue({
    data: { items, total: items.length, page: 1, page_size: 100 },
  });

  return render(
    <ThemeProvider>
      <EventsCalendar month={month} onMonth={() => {}} onOpen={() => {}} />
    </ThemeProvider>
  );
}

describe("EventsCalendar overlap count in header and cards", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("October 6 events (11:40 AM, 11:43 AM, 12:33 PM) with non-intersecting durations display no overlap warning", async () => {
    const oct6Items = [
      ev("e1", "manual", "2026-10-06T11:40:00Z", "2026-10-06T11:42:00Z"),
      ev("e2", "manual testing", "2026-10-06T11:43:00Z", "2026-10-06T11:45:00Z"),
      ev("e3", "at", "2026-10-06T12:33:00Z", "2026-10-06T12:35:00Z"),
    ];

    renderCalendar(oct6Items, "2026-10");
    const cal = await screen.findByRole("region", { name: /event calendar, october 2026/i });
    await within(cal).findByText("manual");
    await within(cal).findByText("manual testing");
    await within(cal).findByText("at");

    // Overlap count warning must NOT be in document
    expect(within(cal).queryByText(/overlapping/i)).not.toBeInTheDocument();

    // Event buttons must not have overlap accessible labels
    expect(within(cal).getByRole("button", { name: /manual,/i })).not.toHaveAccessibleName(/overlaps/i);
    expect(within(cal).getByRole("button", { name: /manual testing,/i })).not.toHaveAccessibleName(/overlaps/i);
    expect(within(cal).getByRole("button", { name: /at,/i })).not.toHaveAccessibleName(/overlaps/i);
  });

  it("Two overlapping events display '1 overlapping' reflecting the single overlapping pair", async () => {
    const pairItems = [
      ev("a", "Event A", "2026-10-06T10:00:00Z", "2026-10-06T11:00:00Z"),
      ev("b", "Event B", "2026-10-06T10:30:00Z", "2026-10-06T11:30:00Z"),
      ev("c", "Event C", "2026-10-06T14:00:00Z", "2026-10-06T15:00:00Z"),
    ];

    renderCalendar(pairItems, "2026-10");
    const cal = await screen.findByRole("region", { name: /event calendar, october 2026/i });
    await within(cal).findByText("Event A");

    // Must display "1 overlapping" for the 1 unique pair, not "2 overlapping"
    expect(within(cal).getByText(/1 overlapping/i)).toBeInTheDocument();
    expect(within(cal).queryByText(/2 overlapping/i)).not.toBeInTheDocument();

    // Both clashing events are flagged
    expect(within(cal).getByRole("button", { name: /event a.*overlaps another event/i })).toBeInTheDocument();
    expect(within(cal).getByRole("button", { name: /event b.*overlaps another event/i })).toBeInTheDocument();
    // Non-overlapping event is not flagged
    expect(within(cal).getByRole("button", { name: /event c,/i })).not.toHaveAccessibleName(/overlaps/i);
  });

  it("Three mutually overlapping events display '3 overlapping' reflecting 3 overlapping pairs", async () => {
    const threeClash = [
      ev("a", "Event A", "2026-10-06T10:00:00Z", "2026-10-06T12:00:00Z"),
      ev("b", "Event B", "2026-10-06T10:30:00Z", "2026-10-06T12:30:00Z"),
      ev("c", "Event C", "2026-10-06T11:00:00Z", "2026-10-06T13:00:00Z"),
    ];

    renderCalendar(threeClash, "2026-10");
    const cal = await screen.findByRole("region", { name: /event calendar, october 2026/i });
    await within(cal).findByText("Event A");

    expect(within(cal).getByText(/3 overlapping/i)).toBeInTheDocument();

    expect(within(cal).getByRole("button", { name: /event a.*overlaps another event/i })).toBeInTheDocument();
    expect(within(cal).getByRole("button", { name: /event b.*overlaps another event/i })).toBeInTheDocument();
    expect(within(cal).getByRole("button", { name: /event c.*overlaps another event/i })).toBeInTheDocument();
  });

  it("Sequential boundary events touching at start/end boundaries display no overlap indicator", async () => {
    const boundaryItems = [
      ev("seq1", "First Event", "2026-10-06T10:00:00Z", "2026-10-06T11:00:00Z"),
      ev("seq2", "Second Event", "2026-10-06T11:00:00Z", "2026-10-06T12:00:00Z"),
    ];

    renderCalendar(boundaryItems, "2026-10");
    const cal = await screen.findByRole("region", { name: /event calendar, october 2026/i });
    await within(cal).findByText("First Event");

    expect(within(cal).queryByText(/overlapping/i)).not.toBeInTheDocument();
    expect(within(cal).getByRole("button", { name: /first event,/i })).not.toHaveAccessibleName(/overlaps/i);
    expect(within(cal).getByRole("button", { name: /second event,/i })).not.toHaveAccessibleName(/overlaps/i);
  });
});

