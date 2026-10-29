// Focused tests for Contact form "What can we help with?" dropdown layering, visual elevation,
// positioning, styling, keyboard navigation, and interaction.
import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../api", () => ({
  default: { get: vi.fn(), post: vi.fn() },
  errMsg: (e) => e?.message ?? "Something went wrong.",
}));

import Contact from "./Contact";

function renderContact() {
  return render(
    <MemoryRouter initialEntries={["/contact"]}>
      <Contact />
    </MemoryRouter>
  );
}

describe("Contact form - 'What can we help with?' dropdown elevation and layering", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    Element.prototype.scrollIntoView = vi.fn();
  });

  it("opens directly beneath trigger with consistent alignment and full width", () => {
    renderContact();
    const trigger = screen.getByRole("button", { name: /what can we help with/i });
    expect(trigger).toHaveAttribute("aria-expanded", "false");

    fireEvent.click(trigger);
    expect(trigger).toHaveAttribute("aria-expanded", "true");

    const listbox = screen.getByRole("listbox", { name: /what can we help with/i });
    expect(listbox).toBeInTheDocument();

    // Menu styling: matches trigger width (w-full), positioned beneath (top-full mt-1.5 left-0)
    expect(listbox.className).toContain("w-full");
    expect(listbox.className).toContain("left-0");
    expect(listbox.className).toContain("top-full");
    expect(listbox.className).toContain("mt-1.5");
  });

  it("has defined surface, border, rounded corners, ring, and visible shadow elevation", () => {
    renderContact();
    const trigger = screen.getByRole("button", { name: /what can we help with/i });
    fireEvent.click(trigger);

    const listbox = screen.getByRole("listbox", { name: /what can we help with/i });

    // Solid theme-appropriate background, border, rounded corners, and elevated shadow + ring
    expect(listbox.className).toContain("bg-white");
    expect(listbox.className).toContain("border-slate-200");
    expect(listbox.className).toContain("rounded-xl");
    expect(listbox.className).toContain("shadow-xl");
    expect(listbox.className).toContain("shadow-slate-900/15");
    expect(listbox.className).toContain("ring-1");
    expect(listbox.className).toContain("ring-slate-900/5");
    expect(listbox.className).toContain("dark:bg-neutral-950");
    expect(listbox.className).toContain("dark:border-white/10");
  });

  it("applies deliberate stacking level so the menu appears above neighbouring form controls", () => {
    renderContact();
    const trigger = screen.getByRole("button", { name: /what can we help with/i });
    const container = trigger.parentElement;

    // Before open: container has no elevated z-index
    expect(container.className).not.toContain("z-30");

    fireEvent.click(trigger);

    // When open: container is elevated to z-30, and menu has z-40
    expect(container.className).toContain("z-30");
    const listbox = screen.getByRole("listbox", { name: /what can we help with/i });
    expect(listbox.className).toContain("z-40");
  });

  it("renders readable options with text-sm typography and violet brand selected state", () => {
    renderContact();
    const trigger = screen.getByRole("button", { name: /what can we help with/i });
    fireEvent.click(trigger);

    const listbox = screen.getByRole("listbox", { name: /what can we help with/i });
    const options = within(listbox).getAllByRole("option");
    expect(options.length).toBeGreaterThanOrEqual(9);

    // Options use text-sm matching public form fields (size="lg")
    expect(options[0].className).toContain("text-sm");

    // Select first option
    fireEvent.click(options[0]);

    // Reopen menu to inspect selected state
    fireEvent.click(trigger);
    const updatedOptions = within(screen.getByRole("listbox")).getAllByRole("option");
    const selected = updatedOptions[0];
    expect(selected).toHaveAttribute("aria-selected", "true");
    expect(selected.className).toContain("bg-violet-100");
    expect(selected.className).toContain("text-violet-950");
    expect(selected.className).toContain("font-semibold");

    // Hover over a different option
    fireEvent.mouseEnter(updatedOptions[1]);
    expect(selected.className).toContain("bg-violet-50/70");
    expect(selected.className).toContain("text-violet-900");
    expect(updatedOptions[1].className).toContain("bg-slate-100");

    // Trigger shows selected label with text-slate-800
    expect(trigger).toHaveTextContent("Platform evaluation & enterprise sales");
  });

  it("closes on Escape and returns focus to the trigger", () => {
    renderContact();
    const trigger = screen.getByRole("button", { name: /what can we help with/i });
    fireEvent.click(trigger);

    const listbox = screen.getByRole("listbox");
    expect(listbox).toBeInTheDocument();

    fireEvent.keyDown(listbox, { key: "Escape" });
    expect(screen.queryByRole("listbox")).toBeNull();
    expect(trigger).toHaveFocus();
  });

  it("closes on outside click without shifting form layout", () => {
    renderContact();
    const trigger = screen.getByRole("button", { name: /what can we help with/i });
    fireEvent.click(trigger);
    expect(screen.getByRole("listbox")).toBeInTheDocument();

    fireEvent.mouseDown(document.body);
    expect(screen.queryByRole("listbox")).toBeNull();
  });

  it("supports keyboard navigation with ArrowDown and Enter", () => {
    renderContact();
    const trigger = screen.getByRole("button", { name: /what can we help with/i });

    // Open via ArrowDown on trigger
    fireEvent.keyDown(trigger, { key: "ArrowDown" });
    const listbox = screen.getByRole("listbox");
    expect(listbox).toBeInTheDocument();

    // Navigate to next option and commit with Enter
    fireEvent.keyDown(listbox, { key: "ArrowDown" });
    fireEvent.keyDown(listbox, { key: "Enter" });

    expect(screen.queryByRole("listbox")).toBeNull();
    expect(trigger).toHaveTextContent("API, SDK & integrations");
  });
});
