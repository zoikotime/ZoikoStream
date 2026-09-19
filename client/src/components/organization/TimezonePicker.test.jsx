// The timezone field is searchable, and still submits an IANA identifier.
//
// It was a plain <select> over ~65 zones in eight <optgroup>s, so choosing one meant scrolling
// a list taller than the modal. What must NOT change is the value: the API stores `timezone`
// verbatim and the console hands it to Intl.DateTimeFormat({ timeZone }), which throws a
// RangeError on anything that is not an IANA name. So every test that touches selection
// asserts the ZONE, never the label.
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ThemeProvider } from "../../theme/ThemeContext";
import TimezonePicker from "./TimezonePicker";
import { TIMEZONE_ROWS, filterTimezones, groupTimezones } from "../../data/timezoneSearch";

const open = async (u) => {
  await u.click(screen.getByRole("button", { name: /coordinated universal time|select a timezone|—/i }));
  return screen.getByRole("listbox");
};

const renderPicker = (props = {}) => {
  const onChange = vi.fn();
  render(
    <ThemeProvider>
      <TimezonePicker value="UTC" onChange={onChange} {...props} />
    </ThemeProvider>
  );
  return { onChange };
};

const search = () => screen.getByLabelText(/search timezone/i);
const options = () => within(screen.getByRole("listbox")).queryAllByRole("option");
const optionText = () => options().map((o) => o.textContent);

// ── the pure filter ────────────────────────────────────────────────────────────────────

describe("filterTimezones", () => {
  it("finds India by country", () => {
    const hits = filterTimezones("India");
    expect(hits.some((r) => r.zone === "Asia/Kolkata")).toBe(true);
  });

  it("finds India by city, even one the label does not show first", () => {
    expect(filterTimezones("Mumbai").some((r) => r.zone === "Asia/Kolkata")).toBe(true);
    // "Kolkata" appears only in the IANA identifier, not in the visible label.
    expect(filterTimezones("Kolkata").some((r) => r.zone === "Asia/Kolkata")).toBe(true);
  });

  it("finds New York, and treats the words as an AND", () => {
    const hits = filterTimezones("New York");
    expect(hits.some((r) => r.zone === "America/New_York")).toBe(true);
    // Not every zone containing "new" OR "york".
    expect(hits.every((r) => r.haystack.includes("new") && r.haystack.includes("york"))).toBe(true);
  });

  it("finds UTC", () => {
    expect(filterTimezones("UTC").some((r) => r.zone === "UTC")).toBe(true);
  });

  it("finds a zone by GMT offset, in several spellings", () => {
    for (const q of ["GMT+5:30", "gmt+5:30", "UTC+5:30", "+5:30", "+530"]) {
      expect(filterTimezones(q).some((r) => r.zone === "Asia/Kolkata")).toBe(true);
    }
  });

  it("finds a whole region by its heading", () => {
    const hits = filterTimezones("Europe");
    expect(hits.length).toBeGreaterThan(5);
    expect(hits.every((r) => r.group === "Europe" || r.haystack.includes("europe"))).toBe(true);
  });

  it("is case-insensitive", () => {
    const lower = filterTimezones("india").map((r) => r.zone);
    const upper = filterTimezones("INDIA").map((r) => r.zone);
    const mixed = filterTimezones("InDiA").map((r) => r.zone);
    expect(lower).toEqual(upper);
    expect(lower).toEqual(mixed);
    expect(lower.length).toBeGreaterThan(0);
  });

  it("returns everything for an empty query", () => {
    expect(filterTimezones("")).toHaveLength(TIMEZONE_ROWS.length);
    expect(filterTimezones("   ")).toHaveLength(TIMEZONE_ROWS.length);
  });

  it("returns nothing for a query that matches nothing", () => {
    expect(filterTimezones("zzzznowhere")).toEqual([]);
  });

  it("every row carries an IANA identifier, never an abbreviation", () => {
    // The whole contract: "Asia/Kolkata", not "IST".
    for (const r of TIMEZONE_ROWS) {
      expect(r.zone === "UTC" || r.zone.includes("/")).toBe(true);
    }
  });
});

// ── the control ────────────────────────────────────────────────────────────────────────

describe("the picker", () => {
  it("shows the current selection's label before opening", () => {
    renderPicker({ value: "Asia/Kolkata" });
    expect(screen.getByRole("button", { name: /IST \(India\)/ })).toBeInTheDocument();
  });

  it("opens a searchable listbox", async () => {
    const u = userEvent.setup();
    renderPicker();
    await open(u);
    expect(search()).toBeInTheDocument();
    expect(search()).toHaveAttribute("placeholder", expect.stringMatching(/search timezone/i));
  });

  it("filters as the user types", async () => {
    const u = userEvent.setup();
    renderPicker();
    await open(u);
    const before = options().length;

    await u.type(search(), "India");

    expect(options().length).toBeLessThan(before);
    expect(optionText().join(" ")).toMatch(/IST \(India\)/);
  });

  it.each([
    ["India", /IST \(India\)/],
    ["Mumbai", /IST \(India\)/],
    ["New York", /EST\/EDT/],
    ["UTC", /Coordinated Universal Time/],
  ])("search %s surfaces the right zone", async (query, expected) => {
    const u = userEvent.setup();
    renderPicker();
    await open(u);
    await u.type(search(), query);
    expect(optionText().join(" ")).toMatch(expected);
  });

  it("is case-insensitive in the UI too", async () => {
    const u = userEvent.setup();
    renderPicker();
    await open(u);
    await u.type(search(), "mUmBaI");
    expect(optionText().join(" ")).toMatch(/IST \(India\)/);
  });

  it("shows a no-results state rather than an empty list", async () => {
    const u = userEvent.setup();
    renderPicker();
    await open(u);
    await u.type(search(), "zzzznowhere");

    expect(options()).toHaveLength(0);
    expect(screen.getByText(/no timezones found/i)).toBeInTheDocument();
  });

  it("hands back the IANA zone, not the label", async () => {
    const u = userEvent.setup();
    const { onChange } = renderPicker();
    await open(u);
    await u.type(search(), "Mumbai");
    await u.click(within(screen.getByRole("listbox")).getAllByRole("option")[0]);

    expect(onChange).toHaveBeenCalledWith("Asia/Kolkata");
    // Never the abbreviation — Intl.DateTimeFormat would throw on it.
    expect(onChange).not.toHaveBeenCalledWith("IST");
    expect(onChange).not.toHaveBeenCalledWith(expect.stringMatching(/GMT|IST \(/));
  });

  it("keeps the selection visible after the list closes", async () => {
    const u = userEvent.setup();
    const onChange = vi.fn();
    const { rerender } = render(
      <ThemeProvider><TimezonePicker value="UTC" onChange={onChange} /></ThemeProvider>
    );
    await u.click(screen.getByRole("button", { name: /coordinated universal time/i }));
    await u.type(search(), "Mumbai");
    await u.click(within(screen.getByRole("listbox")).getAllByRole("option")[0]);

    // The parent owns the value; re-render with what it was handed.
    rerender(
      <ThemeProvider>
        <TimezonePicker value={onChange.mock.calls[0][0]} onChange={onChange} />
      </ThemeProvider>
    );
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /IST \(India\)/ })).toBeInTheDocument();
  });

  it("supports keyboard navigation and Enter to choose", async () => {
    const u = userEvent.setup();
    const { onChange } = renderPicker();
    await open(u);
    await u.type(search(), "India");
    await u.keyboard("{Enter}");

    expect(onChange).toHaveBeenCalledWith("Asia/Kolkata");
  });

  it("closes on Escape without changing the value", async () => {
    const u = userEvent.setup();
    const { onChange } = renderPicker();
    await open(u);
    await u.keyboard("{Escape}");

    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    expect(onChange).not.toHaveBeenCalled();
  });

  it("renders an unknown stored zone as itself rather than blank or a guess", () => {
    renderPicker({ value: "Antarctica/Troll" });
    expect(screen.getByRole("button", { name: "Antarctica/Troll" })).toBeInTheDocument();
  });
});

// ── the original grouped layout, restored ──────────────────────────────────────────────

describe("the previous grouped layout", () => {
  it("keeps the region headings the old <optgroup>s had", async () => {
    const u = userEvent.setup();
    renderPicker();
    await open(u);

    for (const heading of ["Universal", "Americas", "Europe", "Asia"]) {
      expect(screen.getByText(heading)).toBeInTheDocument();
    }
  });

  it("shows the full readable label, not a compacted row", async () => {
    const u = userEvent.setup();
    renderPicker();
    await open(u);
    await u.type(search(), "India");

    // The whole tzLabel(): abbreviation — cities (offset). Nothing truncated away.
    expect(screen.getByRole("option", { name: /IST \(India\) — Mumbai, Delhi, Bengaluru, Colombo \(GMT\+5:30\)/ }))
      .toBeInTheDocument();
  });

  it("keeps groups in their original order", async () => {
    const u = userEvent.setup();
    renderPicker();
    await open(u);

    const order = groupTimezones("").map(([g]) => g);
    expect(order[0]).toBe("Universal");
    expect(order).toEqual([...new Set(order)]);   // each heading appears once
  });

  it("drops a heading whose rows all filtered out", () => {
    const groups = groupTimezones("India").map(([g]) => g);
    // Only the region actually holding a match survives — no empty "Americas" heading.
    expect(groups).toEqual(["Asia"]);
    expect(groups).not.toContain("Americas");
  });

  it("keeps the heading for a group that still has matches", () => {
    const groups = groupTimezones("New York");
    expect(groups).toHaveLength(1);
    expect(groups[0][0]).toBe("Americas");
    expect(groups[0][1].map((r) => r.zone)).toContain("America/New_York");
  });

  it("groups hold every matching row and nothing more", () => {
    const flat = filterTimezones("Europe");
    const grouped = groupTimezones("Europe").flatMap(([, rows]) => rows);
    expect(grouped).toHaveLength(flat.length);
  });

  it("arrowing walks across group boundaries, as the flat select did", async () => {
    const u = userEvent.setup();
    const { onChange } = renderPicker();
    await open(u);
    // Universal holds exactly one zone (UTC), so two ArrowDowns must land in Americas.
    await u.keyboard("{ArrowDown}{ArrowDown}{Enter}");

    expect(onChange).toHaveBeenCalled();
    expect(onChange.mock.calls[0][0]).not.toBe("UTC");
  });
});

// ── the redesigned surface ─────────────────────────────────────────────────────────────
//
// Presentation only: these assert the contract the design depends on (a panel wider than the
// field, a sticky search, a marked selected row, no truncation, no horizontal overflow) — not
// exact colour utilities, which would make the file a snapshot of the stylesheet.

describe("the dropdown surface", () => {
  // The panel is portalled to document.body and positioned with inline fixed coordinates,
  // so it is not a class-positioned child of the trigger.
  const panel = () => screen.getByRole("listbox").closest("[style*='position: fixed']");

  it("floats over the modal as its own card, not clipped inside it", async () => {
    const u = userEvent.setup();
    renderPicker();
    await open(u);

    // Portalled: its ancestor chain reaches body without passing through the trigger, which
    // is what lets it escape the Create Event modal's overflow-y-auto body.
    expect(panel()).toBeTruthy();
    expect(panel().style.position).toBe("fixed");
    expect(panel().className).toMatch(/rounded-xl/);
    expect(panel().className).toMatch(/shadow-xl/);
  });

  it("is wider than the field but never wider than the viewport", async () => {
    const u = userEvent.setup();
    renderPicker();
    await open(u);

    const width = parseInt(panel().style.width, 10);
    expect(width).toBeGreaterThan(300);              // roomy enough for a full label
    expect(width).toBeLessThanOrEqual(544);          // the design cap
    expect(width).toBeLessThanOrEqual(window.innerWidth - 32);   // always inside the viewport
  });

  it("keeps itself on screen horizontally", async () => {
    const u = userEvent.setup();
    renderPicker();
    await open(u);

    const left = parseInt(panel().style.left, 10);
    const width = parseInt(panel().style.width, 10);
    expect(left).toBeGreaterThanOrEqual(0);
    expect(left + width).toBeLessThanOrEqual(window.innerWidth);
  });

  it("never lets an option truncate its label", async () => {
    const u = userEvent.setup();
    renderPicker();
    await open(u);

    for (const opt of options()) {
      expect(opt.className).not.toMatch(/(^|\s)truncate(\s|$)/);
    }
  });

  it("keeps the search box pinned while the list scrolls", async () => {
    const u = userEvent.setup();
    renderPicker();
    await open(u);

    // Pinned by flex layout rather than `position: sticky`: the header is a shrink-0 SIBLING
    // of the scroll container, so it is outside anything that scrolls. Stickiness inside the
    // list would only have held while the list had room — which is precisely the case that
    // failed when the panel opened upward.
    const list = screen.getByRole("listbox");
    expect(list.contains(search())).toBe(false);
    expect(search().closest("div").className).toMatch(/shrink-0/);
  });

  it("scrolls internally with a thin scrollbar rather than growing the modal", async () => {
    const u = userEvent.setup();
    renderPicker();
    await open(u);

    const list = screen.getByRole("listbox");
    expect(list.className).toMatch(/overflow-y-auto/);
    expect(list.className).toMatch(/zk-scroll-thin/);
    // No horizontal scrolling anywhere in the panel.
    expect(list.className).toMatch(/overflow-x-hidden/);
  });

  it("marks the selected row distinctly from the merely-hovered one", async () => {
    const u = userEvent.setup();
    renderPicker({ value: "Asia/Kolkata" });
    await u.click(screen.getByRole("button", { name: /IST \(India\)/ }));

    const chosen = options().find((o) => o.getAttribute("aria-selected") === "true");
    expect(chosen).toBeTruthy();
    expect(chosen.className).toMatch(/violet/);
    // And it carries a check, so selection is not conveyed by colour alone.
    expect(within(chosen).queryByRole("img", { hidden: true }) || chosen.querySelector("svg")).toBeTruthy();
  });

  it("still exposes the listbox/option/aria-selected contract", async () => {
    const u = userEvent.setup();
    renderPicker({ value: "UTC" });
    await open(u);

    expect(screen.getByRole("listbox")).toBeInTheDocument();
    expect(options().length).toBeGreaterThan(0);
    expect(options().filter((o) => o.getAttribute("aria-selected") === "true")).toHaveLength(1);
    expect(search()).toHaveAttribute("aria-activedescendant");
  });

  it("groups are labelled for assistive tech, not just styled", async () => {
    const u = userEvent.setup();
    renderPicker();
    await open(u);

    const groups = screen.getAllByRole("group");
    expect(groups.length).toBeGreaterThan(1);
    expect(groups[0]).toHaveAttribute("aria-label");
  });
});

// ── size, placement, and the search bar surviving both directions ──────────────────────
//
// The bug these exist for: the height cap was applied to the scrolling LIST, so the panel's
// real height was list + search header + borders. Opened upward near the top of the window it
// overflowed past y=0 and took the search box off-screen — the one control that must never
// scroll away. jsdom reports zeroed rects, so the trigger's geometry is stubbed to place the
// field deliberately near the top or the bottom of the viewport.

describe("size and placement", () => {
  const VIEW_H = 768;
  const VIEW_W = 1280;

  const panel = () => screen.getByRole("listbox").closest("[style*='position: fixed']");
  const header = () => search().closest("div");

  /** Render with the trigger pinned at a chosen y, so up/down placement is deterministic. */
  const renderAt = (top, height = 38) => {
    window.innerHeight = VIEW_H;
    window.innerWidth = VIEW_W;
    const spy = vi
      .spyOn(Element.prototype, "getBoundingClientRect")
      .mockReturnValue({
        top, bottom: top + height, left: 700, right: 900,
        width: 200, height, x: 700, y: top, toJSON: () => {},
      });
    const out = renderPicker();
    return { ...out, restore: () => spy.mockRestore() };
  };

  const px = (v) => parseInt(v, 10);

  it("is 420–460px wide", async () => {
    const u = userEvent.setup();
    const { restore } = renderAt(200);
    await open(u);
    const w = px(panel().style.width);
    expect(w).toBeGreaterThanOrEqual(420);
    expect(w).toBeLessThanOrEqual(460);
    restore();
  });

  it("caps the WHOLE panel at 360–420px, not just the list", async () => {
    const u = userEvent.setup();
    const { restore } = renderAt(120);          // plenty of room below
    await open(u);

    const h = px(panel().style.maxHeight);
    expect(h).toBeGreaterThanOrEqual(360);
    expect(h).toBeLessThanOrEqual(420);
    // The cap is on the panel; the list carries none of its own.
    expect(screen.getByRole("listbox").style.maxHeight).toBe("");
    restore();
  });

  it("opens below the field when there is room", async () => {
    const u = userEvent.setup();
    const { restore } = renderAt(120);
    await open(u);

    expect(panel().style.top).not.toBe("");
    expect(panel().style.bottom).toBe("");
    expect(px(panel().style.top)).toBeGreaterThan(120);
    restore();
  });

  it("opens upward when the field sits near the bottom", async () => {
    const u = userEvent.setup();
    const { restore } = renderAt(VIEW_H - 60);   // almost no room below
    await open(u);

    expect(panel().style.bottom).not.toBe("");
    expect(panel().style.top).toBe("");
    restore();
  });

  it.each([
    ["downward", 120],
    ["upward", VIEW_H - 60],
  ])("keeps the panel inside the viewport when opening %s", async (_dir, top) => {
    const u = userEvent.setup();
    const { restore } = renderAt(top);
    await open(u);

    const st = panel().style;
    const h = px(st.maxHeight);
    // Resolve the box to absolute viewport coordinates either way it was anchored.
    const y0 = st.top !== "" ? px(st.top) : VIEW_H - px(st.bottom) - h;
    const y1 = y0 + h;

    expect(y0).toBeGreaterThanOrEqual(0);      // never clipped off the top — the reported bug
    expect(y1).toBeLessThanOrEqual(VIEW_H);    // nor off the bottom
    restore();
  });

  it.each([
    ["downward", 120],
    ["upward", VIEW_H - 60],
  ])("keeps the search bar visible and outside the scroll area when opening %s", async (_dir, top) => {
    const u = userEvent.setup();
    const { restore } = renderAt(top);
    await open(u);

    // The header is a SIBLING of the scroll container, not a child of it, so no amount of
    // list scrolling can move it — and it is shrink-0, so a short panel cannot collapse it.
    const list = screen.getByRole("listbox");
    expect(list.contains(search())).toBe(false);
    expect(header().className).toMatch(/shrink-0/);
    expect(search()).toBeVisible();
    restore();
  });

  it("scrolls the list and only the list", async () => {
    const u = userEvent.setup();
    const { restore } = renderAt(120);
    await open(u);

    const list = screen.getByRole("listbox");
    expect(list.className).toMatch(/overflow-y-auto/);
    expect(list.className).toMatch(/flex-1/);      // fills the leftover height
    expect(list.className).toMatch(/min-h-0/);     // ...and is allowed to shrink to do it
    expect(list.className).toMatch(/zk-scroll-thin/);
    // The panel itself clips rather than scrolls, so there is no second scrollbar.
    expect(panel().className).toMatch(/overflow-hidden/);
    restore();
  });

  it("stays within the viewport margin horizontally", async () => {
    const u = userEvent.setup();
    const { restore } = renderAt(200);
    await open(u);

    const left = px(panel().style.left);
    const w = px(panel().style.width);
    expect(left).toBeGreaterThanOrEqual(12);
    expect(left + w).toBeLessThanOrEqual(VIEW_W - 12);
    restore();
  });
});
