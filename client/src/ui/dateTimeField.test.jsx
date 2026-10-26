// The custom-window fields show the platform's date format, not the browser's.
//
// The native control renders dd-mm-yyyy (or mm/dd/yyyy, or whatever the browser's UI language
// says) and cannot be told otherwise, so DateTimeField paints "Oct 18, 2026, 10:05 AM" over
// it. What is pinned here is that the paint is ONLY paint: the value the field holds, and
// therefore what the Command Center serialises, is byte-identical to what it was before.
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import DateTimeField from "./DateTimeField";
import { formatLocalInput } from "./formatLocalInput";
import CommandFilters from "../components/admin/sections/CommandFilters";
import { commandCenterParams } from "../components/admin/sections/commandWindow";

// ICU puts a narrow no-break space before AM/PM in some versions and a plain one in others,
// so every assertion compares on normalised whitespace rather than on one of the two.
const flat = (s) => String(s).replace(/\s+/g, " ").trim();

describe("the displayed format", () => {
  it("is MMM DD, YYYY — the format the rest of the console uses", () => {
    expect(flat(formatLocalInput("2026-10-18T10:05"))).toBe("Oct 18, 2026, 10:05 AM");
    expect(flat(formatLocalInput("2026-10-19T10:05"))).toBe("Oct 19, 2026, 10:05 AM");
  });

  it("does not depend on the machine's locale", () => {
    // The bug being fixed was a browser-chosen format. Pinning the exact string is the
    // assertion: a locale-driven implementation would render 18/10/2026 somewhere.
    expect(flat(formatLocalInput("2026-10-18T10:05"))).not.toMatch(/18[/-]10/);
  });

  it("crosses a month boundary without moving the day", () => {
    expect(flat(formatLocalInput("2026-10-31T23:00"))).toBe("Oct 31, 2026, 11:00 PM");
    expect(flat(formatLocalInput("2026-11-01T01:00"))).toBe("Nov 1, 2026, 1:00 AM");
  });

  it("crosses a year boundary without moving the year", () => {
    expect(flat(formatLocalInput("2026-12-31T23:30"))).toBe("Dec 31, 2026, 11:30 PM");
    expect(flat(formatLocalInput("2027-01-01T00:15"))).toBe("Jan 1, 2027, 12:15 AM");
  });

  it("keeps midnight on its own date rather than rolling it back a day", () => {
    // The off-by-one this class of change usually introduces: 00:00 parsed as UTC and
    // rendered in a behind-UTC zone shows the previous day. The value is wall clock, so the
    // date shown is the date typed.
    expect(flat(formatLocalInput("2026-10-18T00:00"))).toBe("Oct 18, 2026, 12:00 AM");
    expect(flat(formatLocalInput("2026-01-01T00:00"))).toBe("Jan 1, 2026, 12:00 AM");
  });

  it("shows nothing for an empty or half-typed value, so the native field is left alone", () => {
    expect(formatLocalInput("")).toBe("");
    expect(formatLocalInput(null)).toBe("");
    expect(formatLocalInput("2026-10-")).toBe("");
    expect(formatLocalInput("not a date")).toBe("");
  });
});

describe("the field underneath", () => {
  it("still holds the datetime-local value, unchanged", () => {
    render(<DateTimeField aria-label="Window start" value="2026-10-18T10:05" onChange={() => {}} />);
    const input = screen.getByLabelText("Window start");
    expect(input).toHaveAttribute("type", "datetime-local");
    expect(input).toHaveValue("2026-10-18T10:05");
  });

  it("reports edits in the same format it always did", () => {
    // Read inside the handler: the field is controlled, so React restores the DOM node's
    // value before an assertion made after the event could see it.
    const seen = [];
    const onChange = vi.fn((e) => seen.push(e.target.value));
    render(<DateTimeField aria-label="Window start" value="2026-10-18T10:05" onChange={onChange} />);
    fireEvent.change(screen.getByLabelText("Window start"), { target: { value: "2026-11-02T08:30" } });
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(seen).toEqual(["2026-11-02T08:30"]);
  });

  it("passes min/max through, so the native clamping still applies", () => {
    render(
      <DateTimeField aria-label="Window end" value="2026-10-19T10:05" min="2026-10-18T10:05" onChange={() => {}} />
    );
    expect(screen.getByLabelText("Window end")).toHaveAttribute("min", "2026-10-18T10:05");
  });

  it("uncovers the real control while it is being edited, then formats again on blur", () => {
    const { container } = render(
      <DateTimeField aria-label="Window start" value="2026-10-18T10:05" onChange={() => {}} />
    );
    const input = screen.getByLabelText("Window start");
    const overlay = () => container.querySelector("[aria-hidden='true']");

    expect(flat(overlay().textContent)).toBe("Oct 18, 2026, 10:05 AM");
    fireEvent.focus(input);
    expect(overlay()).toBeNull();          // typing is never done through a mask
    fireEvent.blur(input);
    expect(flat(overlay().textContent)).toBe("Oct 18, 2026, 10:05 AM");
  });

  it("keeps the formatted text out of the accessibility tree — the input carries the value", () => {
    const { container } = render(
      <DateTimeField aria-label="Window start" value="2026-10-18T10:05" onChange={() => {}} />
    );
    expect(container.querySelector("[aria-hidden='true']")).toBeTruthy();
  });
});

describe("in the Command Center filter bar", () => {
  const filters = { range: "custom", from: "2026-10-18T10:05", to: "2026-10-19T10:05", region: null, scope: "core_live", include_test: false };
  const show = (onChange = () => {}) =>
    render(
      <CommandFilters
        value={filters}
        onChange={onChange}
        regions={[{ code: "na", label: "NA" }]}
        onRefresh={() => {}}
        onExport={() => {}}
      />
    );

  it("formats BOTH the start and the end field", () => {
    const { container } = show();
    const shown = [...container.querySelectorAll("[aria-hidden='true']")].map((n) => flat(n.textContent));
    expect(shown).toContain("Oct 18, 2026, 10:05 AM");
    expect(shown).toContain("Oct 19, 2026, 10:05 AM");
  });

  it("leaves the instants the request carries exactly as they were", () => {
    // The contract the server sees. Display formatting must not reach this.
    expect(commandCenterParams(filters)).toMatchObject({
      range: "custom",
      from: new Date("2026-10-18T10:05").toISOString(),
      to: new Date("2026-10-19T10:05").toISOString(),
    });
  });

  it("still refuses an inverted window", () => {
    show();
    fireEvent.change(screen.getByLabelText("Window end"), { target: { value: "2026-10-17T10:05" } });
    expect(screen.getByRole("button", { name: "Apply" })).toBeDisabled();
    expect(screen.getByRole("alert")).toHaveTextContent(/start must be before the end/i);
  });

  it("leaves the other controls untouched", () => {
    show();
    for (const name of ["Live", "1h", "24h", "7d", "Custom", "Apply", "Refresh", "Export snapshot"]) {
      expect(screen.getByRole("button", { name })).toBeInTheDocument();
    }
    expect(screen.getByLabelText("Region")).toBeInTheDocument();
    expect(screen.getByLabelText("Scope")).toBeInTheDocument();
    expect(screen.getByLabelText("Include test mode")).toBeInTheDocument();
  });
});
