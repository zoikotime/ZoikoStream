// Text contrast in the organization console (ZST-ENG-DASH-001: WCAG 2.2 AA, 4.5:1 for normal
// text). jsdom cannot compute Tailwind colours, so these pin the colour STEP each element uses;
// the ratios were measured in Chrome on the rendered console:
//   slate-400 (CONSOLE.faint until the contrast pass) on white 2.63:1, on slate-50 2.51:1 — fails
//   slate-500 (CONSOLE.muted, and now CONSOLE.faint in light mode) on white 4.76:1 — passes
//   slate-500 on the tinted info strip 4.42:1 — fails, so the strip's body is CONSOLE.body
// The colour arithmetic itself is pinned in ui/contrast.test.js.
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { FiRadio, FiUsers } from "react-icons/fi";
import { CONSOLE, actionStops, actionStopsHover } from "../../../ui/tokens";
import { ConsoleButton } from "../../../ui/Button";
import StatsCard from "../../../ui/StatsCard";
import AdminPanel from "../../admin/Panel";
import StatCard from "../../admin/StatCard";
import { Banner, Footnote, KpiTile, ListRow, Panel } from "./ConsoleUI";
import OrganizationPageHeader from "../OrganizationPageHeader";
import Freshness from "../Freshness";

vi.mock("../../../auth/AuthContext", () => ({ useAuth: () => ({ user: null, logout: vi.fn() }) }));
import { RailLink, RailSection } from "../../Dashboard/Sidebar";

const classesOf = (token) => token.split(" ");
const expectStep = (el, token) => expect(el).toHaveClass(...classesOf(token));
const expectNotFaint = (el) => expect(el).not.toHaveClass("text-slate-400");

describe("console primitives put information on an AA step", () => {
  it("a KPI caption is muted, not faint", () => {
    render(<KpiTile icon={FiUsers} label="Live now" value={2} caption="On air now" />);
    const caption = screen.getByText("On air now");
    expectStep(caption, CONSOLE.muted);
    expectNotFaint(caption);
  });

  it("an unmeasured KPI keeps the faint dash, and its caption still reads", () => {
    render(<KpiTile label="Watch time" value={null} caption="Not measured yet" />);
    expectStep(screen.getByText("—"), CONSOLE.faint);
    expectStep(screen.getByText("Not measured yet"), CONSOLE.muted);
  });

  it("an unmeasured figure and a measured zero stay distinct, on screen and to a screen reader", () => {
    const { unmount } = render(<KpiTile label="Watch time" value={null} caption="Not measured yet" />);
    const dash = screen.getByText("—");
    expect(dash).not.toHaveClass("text-slate-400");
    expect(dash).not.toHaveClass(...classesOf(CONSOLE.heading));
    expect(screen.getByRole("group", { name: "Watch time" })).not.toHaveTextContent(/\d/);
    unmount();

    render(<KpiTile label="Watch time" value={0} caption="Measured" />);
    const figure = screen.getByText("0").closest("p");
    expect(figure).toHaveClass(...classesOf(CONSOLE.heading));
    expect(figure).toHaveTextContent("000"); // "00" shown (aria-hidden) + "0" for assistive tech
    expect(screen.getByText("00")).toHaveAttribute("aria-hidden", "true");
    expect(screen.queryByText("—")).not.toBeInTheDocument();
  });

  it("a panel subtitle is muted", () => {
    render(<Panel title="Upcoming events" subtitle="Next 30 days">body</Panel>);
    expectStep(screen.getByText("Next 30 days"), CONSOLE.muted);
  });

  it("a list row's detail is muted", () => {
    render(<ul><ListRow title="Event media library" detail="3 captured assets" /></ul>);
    expectStep(screen.getByText("3 captured assets"), CONSOLE.muted);
  });

  it("a footnote is muted", () => {
    render(<Footnote>Figures are computed on the server</Footnote>);
    expectStep(screen.getByText("Figures are computed on the server"), CONSOLE.muted);
  });

  it("the info strip's body is body copy, because muted on its tint measures 4.42:1", () => {
    render(<Banner title="How access works">Authorization and registration are separate checks.</Banner>);
    const body = screen.getByText("Authorization and registration are separate checks.");
    expectStep(body, CONSOLE.body);
    expect(body).not.toHaveClass("text-slate-500");
  });
});

describe("breadcrumb links", () => {
  it("sit on slate-500, not slate-400 (2.51:1 on the page background)", () => {
    render(
      <MemoryRouter>
        <OrganizationPageHeader title="Events" breadcrumbs={[["Organization", "/organization/manage"], ["Events"]]} />
      </MemoryRouter>
    );
    const trail = screen.getByRole("link", { name: "Organization" }).closest("ol");
    expect(trail).toHaveClass("text-slate-500");
    expectNotFaint(trail);
  });
});

// The rail is dark navy in both themes; its small labels were measured from pixels.
describe("the dark rail", () => {
  it("section labels are white/55 (white/45 measured 4.37:1 at 11px)", () => {
    render(<RailSection>Workspace</RailSection>);
    expect(screen.getByText("Workspace")).toHaveClass("text-white/55");
  });

  it("the active row's hint has a dark backing (bare, it measured 3.0:1 on the ramp's pink end)", () => {
    render(
      <MemoryRouter>
        <RailLink to="/organization/events/e1/live" label="Live Control" icon={FiRadio} active hint="Live" />
      </MemoryRouter>
    );
    expect(screen.getByText("Live")).toHaveClass("bg-black/25", "text-white");
  });

  it("an inactive row's hint stays plain white/50 (4.78:1 on navy)", () => {
    render(
      <MemoryRouter>
        <RailLink to="/organization/events/e1/live" label="Live Control" icon={FiRadio} hint="Live" />
      </MemoryRouter>
    );
    expect(screen.getByText("Live")).toHaveClass("text-white/50");
    expect(screen.getByText("Live")).not.toHaveClass("bg-black/25");
  });
});

// The action ramp's stops are pinned arithmetically in ui/contrast.test.js; these pin that the
// places which paint it actually use them (measured in Chrome: ≥5.3:1 under every label).
describe("the console action ramp where it is painted", () => {
  it("the shared console Button's primary uses the AA stops at rest and on hover", () => {
    render(<ConsoleButton>Grant Admin access</ConsoleButton>);
    const button = screen.getByRole("button", { name: "Grant Admin access" });
    expect(button).toHaveClass(...classesOf(actionStops), ...classesOf(actionStopsHover), "text-white");
    expect(button.className).not.toMatch(/to-pink-500|via-fuchsia-600|hover:from-violet-500/);
  });

  it("the rail's active row uses the same stops", () => {
    render(
      <MemoryRouter>
        <RailLink to="/organization/dashboard" label="Overview" icon={FiUsers} active />
      </MemoryRouter>
    );
    expect(screen.getByRole("link", { name: "Overview" })).toHaveClass(...classesOf(actionStops), "text-white");
  });
});

describe("small print that was literal slate-400, or sat on a tint", () => {
  it("a freshness readout is body copy, legible on white and on the tinted status strips", () => {
    render(<Freshness asOf={new Date().toISOString()} />);
    const asOf = screen.getByText(/^As of /);
    expectStep(asOf, CONSOLE.body);
  });

  it("a panel eyebrow and a stat card's hint use the faint token, not slate-400", () => {
    render(
      <>
        <AdminPanel eyebrow="Last 30d" title="Attendance">body</AdminPanel>
        <StatsCard title="Peak Viewers" value={12} hint="Sum of peak concurrent viewers across events" />
        <StatCard label="Revenue" value={3} hint="Now, at list price — not windowed" />
      </>
    );
    for (const text of ["Last 30d", "Sum of peak concurrent viewers across events", "Now, at list price — not windowed"]) {
      const el = screen.getByText(text);
      expectStep(el, CONSOLE.faint);
      expectNotFaint(el);
    }
  });
});

// Hand-built tables and the Event Workspace breadcrumb live in pages that need a full API
// fixture to render; their header rows and trails are pinned at the source instead, so a new
// table cannot quietly reintroduce the faint step DataTable moved off for the same reason.
const SOURCES = {
  ...import.meta.glob("/src/pages/organization/**/*.jsx", { query: "?raw", import: "default", eager: true }),
  ...import.meta.glob("/src/components/organization/**/*.jsx", { query: "?raw", import: "default", eager: true }),
};
const production = Object.entries(SOURCES).filter(([file]) => !/\.test\.jsx$/.test(file));
// Light-mode slate-400 only: `dark:text-slate-400` on a dark row is a different pair and passes.
const LIGHT_FAINT = /CONSOLE\.faint|(?<![\w:-])text-slate-400/;
const linesMatching = (re) =>
  production.flatMap(([file, src]) => src.split("\n").filter((l) => re.test(l)).map((l) => [file, l.trim()]));

describe("console source guard", () => {
  it("finds the console sources it is meant to guard", () => {
    expect(production.length).toBeGreaterThan(20);
    expect(linesMatching(/<thead\b/).length).toBeGreaterThanOrEqual(6);
  });

  it("no table header row uses the faint step", () => {
    const offenders = linesMatching(/<thead\b/).filter(([, l]) => LIGHT_FAINT.test(l));
    expect(offenders).toEqual([]);
  });

  it("no breadcrumb trail uses the faint step", () => {
    const trails = linesMatching(/<ol className=.*text-\[12\.5px\]/);
    expect(trails.length).toBeGreaterThanOrEqual(2);
    expect(trails.filter(([, l]) => LIGHT_FAINT.test(l))).toEqual([]);
  });
});
