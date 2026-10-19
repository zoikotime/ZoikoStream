// The shared ui/charts AreaChart (and LineChart, which is the same component) keeps its Y-axis
// labels inside the chart. Its only consumer is /admin/analytics (four charts).
//
// THE BUG: a fixed 44px Y axis with a NEGATIVE left margin (-12). The axis anchors its labels
// to its inner edge, which that put ~24px from the SVG's border, so a wider label started off
// the canvas and was clipped: in Chrome "0.025" and "0.075" lost 2.1px, "1050k" 5.2px.
//
// jsdom lays nothing out, so every axis label is given a measured width (a conservative 6.6px
// per character at the axis's 11px size); Recharts' width="auto" sizes the axis from exactly
// that measurement. Each label's box must then sit inside the SVG.
import { act, render } from "@testing-library/react";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";

const layout = vi.hoisted(() => ({ width: 520 }));

vi.mock("recharts", async (importOriginal) => {
  const real = await importOriginal();
  const { Children, cloneElement } = await import("react");
  return {
    ...real,
    // A fixed-size chart: ResponsiveContainer measures its parent, which jsdom reports as 0x0.
    ResponsiveContainer: ({ children }) => cloneElement(Children.only(children), { width: layout.width, height: 256 }),
  };
});

import { ThemeProvider } from "../../theme/ThemeContext";
import { AreaChart, LineChart } from "./index";

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

const series = (max) => [
  { label: "Oct 1", value: 0 }, { label: "Oct 5", value: max * 0.4 },
  { label: "Oct 10", value: max * 0.7 }, { label: "Oct 15", value: max },
];
const COLOR = "#7c3aed";

async function draw(Chart, max, width = 520) {
  layout.width = width;
  const { container, unmount } = render(
    <ThemeProvider>
      <Chart title="New contracted MRR" data={series(max)} keys={[{ key: "value", name: "Contracted MRR", color: COLOR }]} />
    </ThemeProvider>,
  );
  // width="auto" measures the rendered labels and re-lays the axis on the next pass.
  await act(async () => { await new Promise((r) => setTimeout(r, 50)); });
  const svg = [...container.querySelectorAll("svg.recharts-surface")]
    .sort((a, b) => Number(b.getAttribute("width")) - Number(a.getAttribute("width")))[0];
  const svgWidth = Number(svg.getAttribute("width"));
  const labels = [...svg.querySelectorAll(`text.${TICK}-value`)]
    .filter((t) => t.getAttribute("text-anchor") === "end")            // the Y axis
    .map((t) => ({ text: t.textContent, left: Number(t.getAttribute("x")) - labelWidth(t.textContent), right: Number(t.getAttribute("x")) }));
  const clipped = labels.filter((l) => l.left < -0.5 || l.right > svgWidth + 0.5).map((l) => l.text);
  return { container, svg, labels: labels.map((l) => l.text), clipped, unmount };
}

describe.each([["AreaChart", AreaChart], ["LineChart", LineChart]])("the shared %s's Y axis", (_name, Chart) => {
  it.each([
    ["short labels", 4, ["0", "1", "2", "3", "4"]],
    ["decimal labels", 0.1, ["0", "0.025", "0.05", "0.075", "0.1"]],
    ["compact labels", 12500, ["0", "3.5k", "7k", "10.5k", "14k"]],
    ["larger values", 1250000, ["0", "350k", "700k", "1050k", "1400k"]],
  ])("keeps %s whole and inside the chart", async (_case, max, expected) => {
    const { labels, clipped, unmount } = await draw(Chart, max);
    expect(labels).toEqual(expected);       // the axis formatting itself is unchanged
    expect(clipped).toEqual([]);
    unmount();
  });

  it("keeps them inside a narrow chart too", async () => {
    const { clipped, unmount } = await draw(Chart, 1250000, 280);
    expect(clipped).toEqual([]);
    unmount();
  });
});

describe("what the fix leaves alone", () => {
  it("draws the series in its own colour, over the same points", async () => {
    const { container, unmount } = await draw(AreaChart, 0.1);
    const curve = container.querySelector(".recharts-area-curve");
    expect(curve).toHaveAttribute("stroke", COLOR);
    expect(container.querySelector(`linearGradient#grad-value stop`)).toHaveAttribute("stop-color", COLOR);
    unmount();
  });
});
