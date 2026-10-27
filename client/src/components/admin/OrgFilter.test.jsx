// OrgFilter's menu: how wide it is, which way it opens, and what a long name does in it.
//
// The menu was `w-full` of a 220px field, so "Jamaica Bible Church Inc. Trinity Ville" wrapped
// onto two lines. It now grows to fit the names it shows, up to a cap, into the room that
// actually exists — and a name past the cap truncates, with the full name in its title.
//
// jsdom has no layout, so the field's geometry is stubbed (as ui/dropdownPlacement.test.jsx
// does for Dropdown): these assertions drive the real measuring code with rects it cannot
// compute for itself. Whether text wraps is a property of real layout; it is checked in a
// browser, not here — what is pinned here is the CSS contract and the placement maths.
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api", () => ({
  default: { get: vi.fn() },
}));

import api from "../../api";
import OrgFilter from "./OrgFilter";

const LONG = "Jamaica Bible Church Inc. Trinity Ville";
const ORGS = [
  { id: "o1", name: LONG },
  { id: "o2", name: "Acme" },
  { id: "o3", name: "The Very Long Organization Name That Will Not Fit In Any Sensible Menu Width Ltd" },
];

const realRect = Element.prototype.getBoundingClientRect;
const realWidth = window.innerWidth;

// Put the field at a chosen horizontal position in a chosen viewport. Optionally inside a
// console-style <main> scroll container whose visible width excludes its scrollbar.
const placeField = ({ left, width = 220, viewport = 1440, main }) => {
  window.innerWidth = viewport;
  Element.prototype.getBoundingClientRect = function rect() {
    if (this.tagName === "INPUT") {
      return { left, right: left + width, width, top: 100, bottom: 136, height: 36, x: left, y: 100 };
    }
    if (this.tagName === "MAIN" && main) {
      return { left: main.left, right: main.left + main.width, width: main.width, top: 0, bottom: 800, height: 800, x: main.left, y: 0 };
    }
    return { left: 0, right: 0, width: 0, top: 0, bottom: 0, height: 0, x: 0, y: 0 };
  };
};

const openMenu = async () => {
  fireEvent.focus(screen.getByLabelText("Filter by organization"));
  const list = await screen.findByRole("listbox");
  await within(list).findAllByRole("option");
  return list;
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.get).mockResolvedValue({ data: { items: ORGS } });
});

afterEach(() => {
  Element.prototype.getBoundingClientRect = realRect;
  window.innerWidth = realWidth;
});

describe("OrgFilter — long names", () => {
  it("keeps every name on one line, with the full name available", async () => {
    placeField({ left: 300 });
    render(<OrgFilter value={null} onChange={() => {}} />);
    await openMenu();
    for (const { name } of ORGS) {
      const option = screen.getByRole("option", { name });
      expect(option).toHaveClass("truncate");   // nowrap + ellipsis, never a second line
      expect(option).toHaveAttribute("title", name);
    }
  });

  it("is no longer pinned to the field's width", async () => {
    placeField({ left: 300 });
    render(<OrgFilter value={null} onChange={() => {}} />);
    const list = await openMenu();
    expect(list).not.toHaveClass("w-full");
    expect(list).toHaveClass("w-max", "min-w-full");   // fits its names, never narrower than the field
    // Truncation, not a horizontal scrollbar, absorbs a name past the cap; height still scrolls.
    expect(list).toHaveClass("overflow-x-hidden", "overflow-y-auto", "max-h-64");
  });
});

describe("OrgFilter — placement", () => {
  it("grows rightward up to the cap when there is room", async () => {
    placeField({ left: 300, viewport: 1440 });
    render(<OrgFilter value={null} onChange={() => {}} />);
    const list = await openMenu();
    expect(list).toHaveClass("left-0");
    expect(list.style.maxWidth).toBe("384px");
  });

  it("opens leftward when the field sits at the right edge of its row", async () => {
    // SupportAccessPanel pushes the filter right with ml-auto: 1440 - 8 - 1180 = 252px of room
    // to the right, far more to the left.
    placeField({ left: 1180, viewport: 1440 });
    render(<OrgFilter value={null} onChange={() => {}} />);
    const list = await openMenu();
    expect(list).toHaveClass("right-0");
    expect(list.style.maxWidth).toBe("384px");
  });

  it("stays inside a phone-width viewport", async () => {
    // 375px wide, field on its own line 36px in. Right room 375 - 8 - 36 = 331 beats left room
    // 311 - 8 = 303, so it grows right — and stops 8px short of the edge.
    placeField({ left: 36, width: 275, viewport: 375 });
    render(<OrgFilter value={null} onChange={() => {}} />);
    const list = await openMenu();
    expect(list).toHaveClass("left-0");
    expect(list.style.maxWidth).toBe("331px");
    expect(36 + parseFloat(list.style.maxWidth)).toBeLessThanOrEqual(375 - 8);
  });

  it("is never narrower than the field", async () => {
    placeField({ left: 20, width: 300, viewport: 320 });
    render(<OrgFilter value={null} onChange={() => {}} />);
    const list = await openMenu();
    expect(parseFloat(list.style.maxWidth)).toBeGreaterThanOrEqual(300);
  });

  // The console's main region is its own scroll container (see ui/Dropdown), so ITS visible box
  // is the edge — clientWidth excludes its scrollbar, and its left edge is the sidebar's right.
  const inMain = (main, clientWidth) => {
    render(<main><OrgFilter value={null} onChange={() => {}} /></main>);
    Object.defineProperty(document.querySelector("main"), "clientWidth", { configurable: true, value: clientWidth });
  };

  it("keeps clear of the console scroll container's scrollbar", async () => {
    // main: 240px in, 1200 wide, 1185 visible (15px scrollbar). Window maths sees 1440 - 8 -
    // 1042 = 390px to the right and would open a 384px menu into the scrollbar; main's visible
    // edge leaves 1425 - 8 - 1042 = 375px, so the menu opens leftward.
    placeField({ left: 1042, viewport: 1440, main: { left: 240, width: 1200 } });
    inMain({ left: 240 }, 1185);
    const list = await openMenu();
    expect(list).toHaveClass("right-0");
    expect(list.style.maxWidth).toBe("384px");
  });

  it("never opens leftward behind the sidebar", async () => {
    // A narrow main (240..560). Window maths would flip left with 528px of "room" — 88px of it
    // behind the sidebar. Bounded by main's left edge, left room is 536 - 248 = 288, which loses
    // to the 296px on the right.
    placeField({ left: 256, width: 280, viewport: 560, main: { left: 240, width: 320 } });
    inMain({ left: 240 }, 320);
    const list = await openMenu();
    expect(list).toHaveClass("left-0");
    expect(list.style.maxWidth).toBe("296px");
  });

  it("re-measures when the window is resized", async () => {
    placeField({ left: 300, viewport: 1440 });
    render(<OrgFilter value={null} onChange={() => {}} />);
    const list = await openMenu();
    expect(list.style.maxWidth).toBe("384px");
    placeField({ left: 20, width: 230, viewport: 320 });
    fireEvent(window, new Event("resize"));
    await waitFor(() => expect(list.style.maxWidth).toBe("292px"));   // 320 - 8 - 20
  });
});

describe("OrgFilter — behaviour unchanged", () => {
  it("searches the server and selects an organization", async () => {
    placeField({ left: 300 });
    const onChange = vi.fn();
    render(<OrgFilter value={null} onChange={onChange} />);
    fireEvent.change(screen.getByLabelText("Filter by organization"), { target: { value: "jam" } });
    await waitFor(() => expect(api.get).toHaveBeenLastCalledWith("/admin/organizations",
      { params: { q: "jam", page_size: 20 } }));
    fireEvent.click(await screen.findByRole("option", { name: LONG }));
    expect(onChange).toHaveBeenCalledWith({ id: "o1", name: LONG });
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it("closes on Escape", async () => {
    placeField({ left: 300 });
    render(<OrgFilter value={null} onChange={() => {}} />);
    await openMenu();
    fireEvent.keyDown(screen.getByLabelText("Filter by organization"), { key: "Escape" });
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it("says so when nothing matches", async () => {
    placeField({ left: 300 });
    vi.mocked(api.get).mockResolvedValue({ data: { items: [] } });
    render(<OrgFilter value={null} onChange={() => {}} />);
    fireEvent.focus(screen.getByLabelText("Filter by organization"));
    expect(await screen.findByText("No matching organizations")).toBeInTheDocument();
  });

  it("shows a selected long name on one line, in full on hover, and clears", () => {
    const onChange = vi.fn();
    render(<OrgFilter value={{ id: "o1", name: LONG }} onChange={onChange} />);
    const chip = screen.getByTestId("org-filter-selected");
    expect(chip).toHaveTextContent(LONG);
    const name = within(chip).getByText(LONG);
    expect(name).toHaveClass("truncate");
    expect(name).toHaveAttribute("title", LONG);
    fireEvent.click(screen.getByRole("button", { name: "Clear organization filter" }));
    expect(onChange).toHaveBeenCalledWith(null);
  });
});
