// Where the console's dropdown opens, and how tall it is allowed to be.
//
// The Users page's organization filter lists EVERY organization — useAllOrganizations walks
// the endpoint to the end — and it was a native <select>, whose option list is browser
// chrome: the OS decides its height and position, so it ran the full height of the viewport
// and no CSS of ours could bound it. It is now ui/Dropdown, whose panel is real DOM.
//
// A fixed max-h-72 was not enough on its own. That is a guess about the page, not about the
// screen: a trigger low in a short viewport still produced a menu running off the bottom. The
// panel is now measured against the actual space below and above the trigger, and opens
// upward when that is the side with room.
//
// jsdom has no layout, so the trigger's geometry is stubbed here — which is the point: these
// assertions drive the real measuring code with rects it cannot compute for itself.
import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import Dropdown from "./Dropdown";

const OPTIONS = Array.from({ length: 60 }, (_, i) => ({ value: `o${i}`, label: `Organization ${i}` }));

const realRect = Element.prototype.getBoundingClientRect;
const realHeight = window.innerHeight;

// Put the trigger at a chosen vertical position in a chosen viewport.
const placeTrigger = ({ top, height = 36, viewport = 800 }) => {
  window.innerHeight = viewport;
  Element.prototype.getBoundingClientRect = function rect() {
    // Only the trigger's position matters to the placement maths.
    if (this.tagName === "BUTTON") {
      return { top, bottom: top + height, left: 0, right: 200, width: 200, height, x: 0, y: top };
    }
    return { top: 0, bottom: 0, left: 0, right: 0, width: 0, height: 0, x: 0, y: 0 };
  };
};

const show = (props = {}) =>
  render(
    <Dropdown
      label="Filter by organization"
      value="all"
      onChange={() => {}}
      options={[{ value: "all", label: "All organizations" }, ...OPTIONS]}
      {...props}
    />
  );

const openMenu = () => {
  fireEvent.click(screen.getByLabelText("Filter by organization"));
  return screen.getByRole("listbox", { name: "Filter by organization" });
};

beforeEach(() => {
  // jsdom does not implement it, and the component scrolls the active row into view.
  Element.prototype.scrollIntoView = vi.fn();
});

afterEach(() => {
  Element.prototype.getBoundingClientRect = realRect;
  window.innerHeight = realHeight;
});

describe("the menu is bounded by the viewport, not by a guess", () => {
  it("opens downward and takes the full ceiling when there is room", () => {
    placeTrigger({ top: 100, viewport: 900 });   // ~750px below
    show();
    const list = openMenu();
    expect(list.className).toContain("top-full");
    expect(list.className).not.toContain("bottom-full");
    expect(list.style.maxHeight).toBe("288px");   // the former max-h-72, unchanged
  });

  it("never exceeds the space below the trigger", () => {
    placeTrigger({ top: 500, viewport: 700 });   // 700 - 536 - 6 - 8 = 150 below
    show();
    const list = openMenu();
    const max = parseInt(list.style.maxHeight, 10);
    expect(max).toBeLessThanOrEqual(288);
    // and it is never so short as to be unusable — it scrolls instead
    expect(max).toBeGreaterThanOrEqual(160);
  });

  it("opens upward when below is the worse side", () => {
    placeTrigger({ top: 640, viewport: 700 });   // ~18px below, ~626px above
    show();
    const list = openMenu();
    expect(list.className).toContain("bottom-full");
    expect(list.className).not.toContain("top-full");
  });

  it("does not flip for a few pixels — only when above is genuinely better", () => {
    placeTrigger({ top: 300, viewport: 900 });   // ~558 below vs ~286 above
    show();
    expect(openMenu().className).toContain("top-full");
  });

  it("stays bounded in a short viewport, where neither side is generous", () => {
    placeTrigger({ top: 160, viewport: 360 });
    show();
    const list = openMenu();
    const max = parseInt(list.style.maxHeight, 10);
    expect(max).toBeGreaterThan(0);
    expect(max).toBeLessThanOrEqual(288);
  });

  it.each([400, 600, 800, 1080])("resolves a placement at viewport height %ipx", (viewport) => {
    placeTrigger({ top: Math.round(viewport / 2), viewport });
    show();
    const list = openMenu();
    expect(list.style.maxHeight).toMatch(/^\d+px$/);
    expect(list.className).toMatch(/top-full|bottom-full/);
  });
});

describe("the list scrolls inside itself", () => {
  it("scrolls vertically and cannot widen horizontally", () => {
    placeTrigger({ top: 100 });
    show();
    const list = openMenu();
    expect(list.className).toContain("overflow-y-auto");
    expect(list.className).toContain("overflow-x-hidden");
  });

  it("does not hand the scroll to the page when the list ends", () => {
    placeTrigger({ top: 100 });
    show();
    expect(openMenu().className).toContain("overscroll-contain");
  });

  it("offers every organization — the list is not shortened to fit", () => {
    placeTrigger({ top: 100 });
    show();
    const list = openMenu();
    expect(within(list).getAllByRole("option")).toHaveLength(OPTIONS.length + 1);
    expect(within(list).getByText("Organization 59")).toBeInTheDocument();
  });
});

describe("what the migration had to keep", () => {
  it("opens on the current selection and brings it into view", () => {
    placeTrigger({ top: 100 });
    show({ value: "o40" });
    const list = openMenu();
    expect(list.querySelector('[data-active="true"]')).toBeTruthy();
    expect(Element.prototype.scrollIntoView).toHaveBeenCalled();
  });

  it("marks the selected organization", () => {
    placeTrigger({ top: 100 });
    show({ value: "o7" });
    const chosen = within(openMenu()).getByRole("option", { name: /Organization 7$/ });
    expect(chosen).toHaveAttribute("aria-selected", "true");
  });

  it("selecting one reports its value and closes", () => {
    placeTrigger({ top: 100 });
    const onChange = vi.fn();
    show({ onChange });
    fireEvent.click(within(openMenu()).getByText("Organization 3"));
    expect(onChange).toHaveBeenCalledWith("o3");
    expect(screen.queryByRole("listbox")).toBeNull();
  });

  it("keeps the keyboard contract a native select gave for free", () => {
    placeTrigger({ top: 100 });
    const onChange = vi.fn();
    show({ onChange });
    const trigger = screen.getByLabelText("Filter by organization");
    fireEvent.keyDown(trigger, { key: "ArrowDown" });
    const list = screen.getByRole("listbox", { name: "Filter by organization" });
    expect(list).toHaveAttribute("aria-activedescendant");
    fireEvent.keyDown(list, { key: "ArrowDown" });
    fireEvent.keyDown(list, { key: "Enter" });
    expect(onChange).toHaveBeenCalledWith("o0");
  });

  it("Escape closes without selecting", () => {
    placeTrigger({ top: 100 });
    const onChange = vi.fn();
    show({ onChange });
    const list = openMenu();
    fireEvent.keyDown(list, { key: "Escape" });
    expect(screen.queryByRole("listbox")).toBeNull();
    expect(onChange).not.toHaveBeenCalled();
  });

  it("keeps the dark panel styling every other consumer relies on", () => {
    placeTrigger({ top: 100 });
    show();
    const list = openMenu();
    expect(list.className).toContain("dark:bg-neutral-950");
    expect(list.className).toContain("shadow-xl");
    expect(list.className).toContain("z-40");
  });
});
