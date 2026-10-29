// The Event Analytics Overview chart's tooltip says which date, which metric and what value.
//
// Production report: hovering a zero-activity day (Oct 09) showed a box containing only ": 0".
// AreaTrend handed recharts' default tooltip an empty series name and an empty label, and that
// default renders `${name} : ${value}` — so every point, zero or not, read ": <value>", with no
// date and no metric. The tooltip is now AreaTrend's own (TrendTooltip): the point's label under
// its kind ("Date"), and each series by name with its formatted value.
//
// The chart is driven the way a keyboard user drives it — recharts' accessibility layer moves
// the active point with the arrow keys — so the tooltip under test is the real one, rendered
// by recharts for a real active point.
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("recharts", async (importOriginal) => {
  const real = await importOriginal();
  const { Children, cloneElement } = await import("react");
  // ResponsiveContainer measures its parent, which jsdom reports as 0x0.
  return { ...real, ResponsiveContainer: ({ children }) => cloneElement(Children.only(children), { width: 520, height: 200 }) };
});

import { ThemeProvider } from "../../theme/ThemeContext";
import AnalyticsOverview from "../../components/organization/dashboard/AnalyticsOverview";
import { AreaTrend, TrendTooltip } from "./index";

const DAYS = [
  { label: "Oct 08", value: 2 },
  { label: "Oct 09", value: 0 },
  { label: "Oct 10", value: 1234 },
];

// Focus the chart, then step right `steps` times; returns the tooltip box.
function pointAt(container, steps) {
  const surface = container.querySelector("svg.recharts-surface");
  act(() => surface.focus());
  for (let i = 0; i < steps; i += 1) fireEvent.keyDown(surface, { key: "ArrowRight" });
  return container.querySelector(".recharts-tooltip-wrapper");
}

describe("TrendTooltip", () => {
  const row = (value) => [{ name: "Peak viewers", value, dataKey: "value", color: "#8b5cf6" }];

  it("names the date, the metric and a zero value", () => {
    render(<TrendTooltip active label="Oct 09" labelName="Date" payload={row(0)} />);
    expect(screen.getByText("Date:", { exact: false })).toBeInTheDocument();
    expect(screen.getByText("Oct 09")).toBeInTheDocument();
    expect(screen.getByText("Peak viewers:")).toBeInTheDocument();
    expect(screen.getByText("0")).toBeInTheDocument();
  });

  it("formats a non-zero value", () => {
    render(<TrendTooltip active label="Oct 10" labelName="Date" payload={row(1234)} />);
    expect(screen.getByText("1,234")).toBeInTheDocument();
  });

  it("shows every series drawn at the point, and leaves out one with no value there", () => {
    render(
      <TrendTooltip
        active
        label="Oct 09"
        labelName="Date"
        payload={[
          { name: "Peak viewers", value: 0, dataKey: "viewers", color: "#8b5cf6" },
          { name: "Watch hours", value: 2.5, dataKey: "watch", color: "#14b8a6" },
          { name: "Playback starts", value: null, dataKey: "starts", color: "#f59e0b" },
        ]}
      />
    );
    expect(screen.getAllByRole("listitem").map((li) => li.textContent)).toEqual(["Peak viewers:0", "Watch hours:2.5"]);
  });

  it("renders nothing when no point is active", () => {
    const { container } = render(<TrendTooltip active={false} label="Oct 09" payload={row(0)} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("is themed for both modes: light surface and its dark counterpart", () => {
    const { container } = render(<TrendTooltip active label="Oct 09" labelName="Date" payload={row(0)} />);
    const box = container.firstElementChild;
    for (const cls of ["bg-white", "border-slate-200", "text-slate-900", "dark:bg-slate-900", "dark:border-slate-700", "dark:text-slate-100", "shadow-lg"]) {
      expect(box.className).toContain(cls);
    }
  });
});

describe("AreaTrend, hovered or stepped through with the keyboard", () => {
  const show = () =>
    render(
      <ThemeProvider>
        <AreaTrend data={DAYS} name="Peak viewers" labelName="Date" showX />
      </ThemeProvider>
    );

  it("a zero-activity day reads 'Date: Oct 09' and 'Peak viewers: 0' — not ': 0'", () => {
    const { container } = show();
    const tip = pointAt(container, 1);
    expect(tip).toHaveTextContent("Date: Oct 09");
    expect(tip).toHaveTextContent("Peak viewers:0");
    expect(tip.textContent).not.toMatch(/^\s*:/);
  });

  it("non-zero days still read correctly", () => {
    const { container } = show();
    expect(pointAt(container, 0)).toHaveTextContent("Date: Oct 08Peak viewers:2");
    const { container: again } = show();
    expect(pointAt(again, 2)).toHaveTextContent("Date: Oct 10Peak viewers:1,234");
  });
});

describe("Event Analytics Overview", () => {
  const data = {
    summary: { peak_viewers_summed: 4, watch_hours: 0.513, peak: 1, engagement: null },
    trends: { viewership: DAYS },
  };
  const show = (range = "30d") =>
    render(
      <ThemeProvider>
        <AnalyticsOverview data={data} loading={false} error={null} range={range} onRange={() => {}} onRetry={() => {}} />
      </ThemeProvider>
    );

  it("labels its series as the Analytics page does and its buckets as dates", () => {
    const { container } = show();
    const tip = pointAt(container, 1);
    expect(within(tip).getByText("Oct 09")).toBeInTheDocument();
    expect(tip).toHaveTextContent("Date: Oct 09");
    expect(tip).toHaveTextContent("Peak viewers:0");
  });

  it("names a 12-month range's buckets as months", () => {
    const { container } = show("12m");
    expect(pointAt(container, 1)).toHaveTextContent("Month: Oct 09");
  });
});
