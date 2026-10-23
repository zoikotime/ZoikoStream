// Section order on /organization/analytics: mobile and desktop differ on purpose.
//
//   below xl (phones, tablets, < 1280px), one column in DOM order:
//     Engagement mix > Audience retention > Viewing sessions > Device breakdowns > Recent Reports
//   xl and up (>= 1280px), unchanged from before:
//     Audience retention | Device breakdowns side by side, Viewing sessions full width below
//
// One grid holds the three cards. Viewing sessions sits between the other two in the DOM, so a
// phone shows it right after retention, and only its xl:order-last + xl:col-span-2 move it
// under both on desktop. Measured in Chromium (mobile build 320–768px, web 1279/1280/1440): the
// order above at every width, desktop rectangles identical to before the change, one copy of
// each section, no overflow. jsdom has no layout; this pins the structure that produces it.
import { render, screen } from "@testing-library/react";
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

const PAYLOAD = {
  range: "30d",
  timezone: "UTC",
  summary: { peak_viewers_summed: 1, watch_hours: 0.1, total_watch_seconds: 360, peak: 1, engagement: 0 },
  trends: { viewership: [{ label: "Oct 16", value: 1 }], watch_time: [{ label: "Oct 16", value: 0.1 }] },
  top_events: [{ label: "Quarterly all-hands", value: 1 }],
  reports: [{
    id: "e1", event: "Quarterly all-hands", date: "2026-10-16", viewers: 1, watch_hours: 0.1,
    total_watch_seconds: 360, average_watch_seconds: 360, engagement: 0, measurement_state: "measured",
    chat_count: 0, question_count: 0, reaction_count: 0,
  }],
  devices: null, locations: null, traffic_sources: null,
  breakdowns_note: "Device, location and traffic-source breakdowns need a metering/GeoIP pipeline that isn't integrated yet.",
  session_summary: null,
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.get).mockResolvedValue({ data: PAYLOAD });
});

const open = async () => {
  render(
    <ThemeProvider>
      <MemoryRouter initialEntries={["/organization/analytics"]}>
        <Analytics />
      </MemoryRouter>
    </ThemeProvider>
  );
  await screen.findByText(RETENTION, { exact: true });
};

const ENGAGEMENT = "Engagement mix";
const RETENTION = "Audience retention";
const VIEWING = "Viewing sessions";
const DEVICE = "Device, location & traffic-source breakdowns";
const REPORTS = "Recent Reports";
const MOBILE_ORDER = [ENGAGEMENT, RETENTION, VIEWING, DEVICE, REPORTS];

const heading = (name) => screen.getAllByText(name, { exact: true });
const follows = (a, b) => Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
const classesOf = (el) => (el.getAttribute("class") || "").split(/\s+/).filter(Boolean);
// The grid holding retention, viewing sessions and the breakdowns note, and each card's item in it.
const sharedGrid = () => {
  const [r] = heading(RETENTION), [v] = heading(VIEWING), [d] = heading(DEVICE);
  return [...document.querySelectorAll("div.grid")].find((g) => g.contains(r) && g.contains(v) && g.contains(d));
};
const itemOf = (name) => {
  const grid = sharedGrid();
  let n = heading(name)[0];
  while (n.parentElement !== grid) n = n.parentElement;
  return n;
};

describe("Analytics sections", () => {
  it("renders every section exactly once, with its existing content", async () => {
    await open();
    for (const name of MOBILE_ORDER) expect(heading(name)).toHaveLength(1);
    // The moved card still gets its data: same props, same empty state for a null summary.
    expect(screen.getAllByText("No viewing sessions recorded in this window yet.")).toHaveLength(1);
    expect(screen.getAllByText(PAYLOAD.breakdowns_note)).toHaveLength(1);
  });

  it("is ordered for mobile: Engagement mix, Retention, Viewing sessions, Device, Recent Reports", async () => {
    await open();
    const els = MOBILE_ORDER.map((name) => heading(name)[0]);
    for (let i = 1; i < els.length; i++) {
      expect(follows(els[i - 1], els[i]), `${MOBILE_ORDER[i - 1]} before ${MOBILE_ORDER[i]}`).toBe(true);
    }
  });
});

describe("one grid, two arrangements", () => {
  it("is one column below xl and two from xl", async () => {
    await open();
    const grid = sharedGrid();
    expect(grid, "retention, viewing sessions and device share one grid").toBeTruthy();
    expect(classesOf(grid)).toEqual(expect.arrayContaining(["grid", "grid-cols-1", "xl:grid-cols-2"]));
    expect([...grid.children]).toEqual([itemOf(RETENTION), itemOf(VIEWING), itemOf(DEVICE)]);
  });

  it("moves Viewing sessions under both cards, full width, only from xl", async () => {
    await open();
    const viewing = classesOf(itemOf(VIEWING));
    expect(viewing).toEqual(expect.arrayContaining(["xl:order-last", "xl:col-span-2"]));
    // Nothing unprefixed, so below xl the visual order IS the DOM order above.
    expect(viewing.filter((c) => /^(order-|col-span-|col-start-|row-start-)/.test(c))).toEqual([]);
    // Retention and Device carry no ordering or spanning, so from xl they fill row one side by side.
    for (const name of [RETENTION, DEVICE]) {
      expect(classesOf(itemOf(name)).filter((c) => /(^|:)(order-|col-span-|col-start-|row-start-)/.test(c))).toEqual([]);
    }
  });

  it("hides or absolutely positions none of the sections at any breakpoint", async () => {
    await open();
    for (const name of MOBILE_ORDER) {
      for (let n = heading(name)[0]; n && n !== document.body; n = n.parentElement) {
        const bad = classesOf(n).filter((c) => /^(\w+:)?(hidden|invisible|absolute|fixed)$/.test(c));
        expect(bad, `${name}: ${n.tagName.toLowerCase()} carries ${bad.join(" ")}`).toEqual([]);
      }
    }
  });
});
