// Organization Analytics — Y-axis labels stay whole (Audience retention, Viewership & watch time).
//
// THE BUG (production): the Audience retention axis read "0h, 025h, )05h, 075h, 0.1h". The tick
// values were right (0, 0.025, 0.05, 0.075, 0.1); the LAYOUT cut them. A Y axis anchors its
// labels to its inner edge, and both charts used a fixed 40-44px axis plus a NEGATIVE margin
// (`left/right: -12`), which put that edge 24px from the SVG's border — so any label wider than
// ~24px started outside the SVG and was clipped. "0h" and "0.1h" fit; the rest lost "0.".
//
// jsdom lays nothing out, so this gives every axis label a measured width (a conservative
// 6.6px per character at the axes' 11px size) and checks, in the real page, that each label
// box lies inside its SVG — at two chart widths. Recharts' width="auto" sizes an axis from
// exactly that measurement, which is what makes the boxes fit.
import { act, render, screen } from "@testing-library/react";
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

const layout = vi.hoisted(() => ({ width: 600 }));

vi.mock("recharts", async (importOriginal) => {
  const real = await importOriginal();
  const { Children, cloneElement } = await import("react");
  return {
    ...real,
    // A fixed-size chart: ResponsiveContainer measures its parent, which jsdom reports as 0x0.
    ResponsiveContainer: ({ children }) => cloneElement(Children.only(children), { width: layout.width, height: 260 }),
  };
});
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

const CHAR_PX = 6.6;
const TICK = "recharts-cartesian-axis-tick";
const labelWidth = (text) => text.length * CHAR_PX;

const realRect = Element.prototype.getBoundingClientRect;
beforeAll(() => {
  Element.prototype.getBoundingClientRect = function rect() {
    const cls = this.getAttribute?.("class") || "";
    if (!cls.includes(TICK)) return realRect.call(this);
    const width = labelWidth(this.textContent || "");
    return { x: 0, y: 0, top: 0, left: 0, right: width, bottom: 12, width, height: 12, toJSON() {} };
  };
});
afterAll(() => {
  Element.prototype.getBoundingClientRect = realRect;
});

const report = (id, event, viewers, watch_hours) => ({
  id, event, date: "2026-10-06", viewers, watch_hours, total_watch_seconds: watch_hours * 3600,
  average_watch_seconds: 60, engagement: 50, measurement_state: "measured",
});
// The production window: 1-viewer events with minutes of watch time, so hours run 0..0.1.
const SMALL = {
  range: "30d", timezone: "UTC",
  summary: { peak_viewers_summed: 3, watch_hours: 0.19, total_watch_seconds: 684, peak: 1, engagement: 50 },
  trends: {
    viewership: [{ label: "Oct 5", value: 1 }, { label: "Oct 6", value: 2 }],
    watch_time: [{ label: "Oct 5", value: 0.0083 }, { label: "Oct 6", value: 0.092 }],
  },
  top_events: [{ label: "at", value: 1 }],
  reports: [report("e1", "web", 1, 0.092), report("e2", "manual testing", 1, 0.088), report("e3", "at", 1, 0.0083)],
  devices: null, locations: null, traffic_sources: null,
};
// Long labels on every axis: five-digit audiences, four-digit hours.
const LARGE = {
  ...SMALL,
  summary: { ...SMALL.summary, peak_viewers_summed: 24000, peak: 12000 },
  trends: {
    viewership: [{ label: "Oct 5", value: 11800 }, { label: "Oct 6", value: 12000 }],
    watch_time: [{ label: "Oct 5", value: 1234.5 }, { label: "Oct 6", value: 1233.5 }],
  },
  reports: [report("e1", "Keynote", 12000, 1234.5), report("e2", "Town hall", 11800, 1233.5)],
};

beforeEach(() => {
  vi.clearAllMocks();
  window.matchMedia = vi.fn().mockImplementation((query) => ({
    matches: query === "(prefers-reduced-motion: reduce)", media: query, onchange: null,
    addListener: vi.fn(), removeListener: vi.fn(), addEventListener: vi.fn(), removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }));
});

async function open(payload) {
  vi.mocked(api.get).mockResolvedValue({ data: payload });
  render(
    <ThemeProvider>
      <MemoryRouter initialEntries={["/organization/analytics"]}>
        <Analytics />
      </MemoryRouter>
    </ThemeProvider>,
  );
  await screen.findByText("Audience retention");
  // width="auto" measures the rendered labels and re-lays the axis on the next pass.
  await act(async () => { await new Promise((r) => setTimeout(r, 50)); });
}

/** The chart under this card title: its plot SVG and its Y-axis labels, with their boxes. */
function chart(title) {
  let card = screen.getByText(title, { selector: "h2" });
  while (card && !card.querySelector("svg.recharts-surface")) card = card.parentElement;
  const svg = [...card.querySelectorAll("svg.recharts-surface")]
    .sort((a, b) => Number(b.getAttribute("width")) - Number(a.getAttribute("width")))[0];
  const svgWidth = Number(svg.getAttribute("width"));
  const ticks = [...svg.querySelectorAll(`text.${TICK}-value`)];
  const y = (anchor) => ticks.filter((t) => t.getAttribute("text-anchor") === anchor).map((t) => {
    const x = Number(t.getAttribute("x"));
    const w = labelWidth(t.textContent);
    return { text: t.textContent, left: anchor === "end" ? x - w : x, right: anchor === "end" ? x : x + w };
  });
  return { svg, svgWidth, left: y("end"), right: y("start") };
}

const inside = ({ svgWidth, left, right }) =>
  [...left, ...right].filter((l) => l.left < -0.5 || l.right > svgWidth + 0.5).map((l) => l.text);

// A whole watch-hours label: a leading digit before any decimal point, then the unit.
const WHOLE_HOURS = /^(0|[1-9]\d*)(\.\d+)?h$/;

describe("Audience retention's Y axis", () => {
  it("labels the production window 0h, 0.025h, 0.05h, 0.075h, 0.1h — whole", async () => {
    await open(SMALL);
    const { left } = chart("Audience retention");
    expect(left.map((l) => l.text)).toEqual(["0h", "0.025h", "0.05h", "0.075h", "0.1h"]);
    for (const { text } of left) {
      expect(text).toMatch(WHOLE_HOURS);
      expect(text).not.toMatch(/^0[1-9]|^\)|^\./);              // never 025h, )05h or .05h
    }
  });

  it.each([[600], [320]])("keeps every label inside the chart at %ipx", async (width) => {
    layout.width = width;
    await open(SMALL);
    expect(inside(chart("Audience retention"))).toEqual([]);
    layout.width = 600;
  });

  it("keeps long labels inside too", async () => {
    await open(LARGE);
    const c = chart("Audience retention");
    expect(c.left.length).toBeGreaterThan(0);
    expect(inside(c)).toEqual([]);
  });

  it("plots the same points: one per event with an audience", async () => {
    await open(SMALL);
    const { svg } = chart("Audience retention");
    expect(svg.querySelectorAll(".recharts-scatter-symbol")).toHaveLength(SMALL.reports.length);
  });
});

describe("Viewership & watch time's two Y axes (the same configuration)", () => {
  it.each([["the production window", SMALL], ["long labels", LARGE]])(
    "keeps every label on both axes inside the chart (%s)", async (_name, payload) => {
      await open(payload);
      const c = chart("Viewership & watch time");
      expect(c.left.length).toBeGreaterThan(0);                    // viewers, on the left
      expect(c.right.length).toBeGreaterThan(0);                   // watch hours, on the right
      expect(inside(c)).toEqual([]);
      for (const { text } of c.right) expect(text).toMatch(WHOLE_HOURS);
    });
});
