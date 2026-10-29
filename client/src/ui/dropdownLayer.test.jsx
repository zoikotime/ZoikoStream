// ui/Dropdown as a dismissable layer, and its type-ahead — for every variant, not only the
// Create Event field that needed them first.
//
// A dialog's Escape (ui/Overlay) and Android's Back (ui/dismissStack.dismissTop) close the TOP
// layer. An open menu must be that layer, or one Escape closes the menu and the dialog under it.
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import Dropdown from "./Dropdown";
import Modal from "./Modal";
import { dismissTop } from "./dismissStack";

const OPTIONS = ["Alpha", "Bravo", "Charlie", "Delta", "Echo"].map((l) => ({ value: l.toLowerCase(), label: l }));

function InDialog({ onClose, variant }) {
  const [value, setValue] = useState("alpha");
  return (
    <Modal open onClose={onClose} title="Settings">
      <span id="pick-label">Pick</span>
      <Dropdown label="Pick" labelId={variant === "field" ? "pick-label" : undefined} variant={variant}
        value={value} onChange={setValue} options={OPTIONS} />
    </Modal>
  );
}

const trigger = (variant) => screen.getByRole(variant === "field" ? "combobox" : "button", { name: "Pick" });
const menu = () => screen.queryByRole("listbox", { name: "Pick" });

beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn();
});

describe.each(["console", "field"])("variant %s inside a dialog", (variant) => {
  it("Escape closes the menu and leaves the dialog; the next Escape is the dialog's", () => {
    const onClose = vi.fn();
    render(<InDialog onClose={onClose} variant={variant} />);
    fireEvent.click(trigger(variant));
    fireEvent.keyDown(menu(), { key: "Escape" });
    expect(menu()).toBeNull();
    expect(onClose).not.toHaveBeenCalled();
    expect(trigger(variant)).toHaveFocus();

    fireEvent.keyDown(trigger(variant), { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("Back (the dismiss stack) closes the menu first, then the dialog", () => {
    const onClose = vi.fn();
    render(<InDialog onClose={onClose} variant={variant} />);
    fireEvent.click(trigger(variant));
    // Back arrives from outside React (the native bridge), hence act().
    let handled;
    act(() => { handled = dismissTop(); });
    expect(handled).toBe(true);
    expect(menu()).toBeNull();
    expect(onClose).not.toHaveBeenCalled();
    act(() => { handled = dismissTop(); });
    expect(handled).toBe(true);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("type-ahead: a letter jumps to its option and Enter takes it", () => {
    render(<InDialog onClose={vi.fn()} variant={variant} />);
    fireEvent.click(trigger(variant));
    const active = () => document.getElementById(menu().getAttribute("aria-activedescendant"));
    fireEvent.keyDown(menu(), { key: "d" });
    expect(active()).toHaveTextContent("Delta");
    fireEvent.keyDown(menu(), { key: "Enter" });
    expect(trigger(variant)).toHaveTextContent("Delta");
  });
});

describe("type-ahead details", () => {
  const ROWS = ["Sports", "Social", "Summit", "Webinar"].map((l) => ({ value: l, label: l }));
  function Plain() {
    const [value, setValue] = useState("Webinar");
    return <Dropdown label="Kind" value={value} onChange={setValue} options={ROWS} />;
  }
  const open = () => fireEvent.click(screen.getByRole("button", { name: "Kind" }));
  const active = () => document.getElementById(screen.getByRole("listbox").getAttribute("aria-activedescendant"));

  it("repeating one letter cycles through the options that start with it", () => {
    render(<Plain />);
    open();
    const list = screen.getByRole("listbox");
    fireEvent.keyDown(list, { key: "s" });
    expect(active()).toHaveTextContent("Sports");
    fireEvent.keyDown(list, { key: "s" });
    expect(active()).toHaveTextContent("Social");
    fireEvent.keyDown(list, { key: "s" });
    expect(active()).toHaveTextContent("Summit");
  });

  it("letters typed together search as a prefix", () => {
    render(<Plain />);
    open();
    const list = screen.getByRole("listbox");
    fireEvent.keyDown(list, { key: "s" });
    fireEvent.keyDown(list, { key: "u" });
    expect(active()).toHaveTextContent("Summit");
  });

  it("a key with no match leaves the active option where it was", () => {
    render(<Plain />);
    open();
    fireEvent.keyDown(screen.getByRole("listbox"), { key: "z" });
    expect(active()).toHaveTextContent("Webinar");
  });

  it("does not hijack shortcuts (Ctrl/Cmd + letter)", () => {
    render(<Plain />);
    open();
    fireEvent.keyDown(screen.getByRole("listbox"), { key: "s", ctrlKey: true });
    expect(active()).toHaveTextContent("Webinar");
  });
});

describe("naming", () => {
  it("a field without a visible label id falls back to its label prop", () => {
    render(<Dropdown variant="field" label="Region" value="a" onChange={() => {}} options={[{ value: "a", label: "A" }]} />);
    expect(screen.getByRole("combobox", { name: "Region" })).toHaveTextContent("A");
  });

  it("the console variant is unchanged: a button named by its label, its menu in place", () => {
    render(<Dropdown label="Region" value="a" onChange={() => {}} options={[{ value: "a", label: "A" }]} />);
    const btn = screen.getByRole("button", { name: "Region" });
    fireEvent.click(btn);
    const list = screen.getByRole("listbox", { name: "Region" });
    expect(btn.parentElement.contains(list)).toBe(true);
    expect(list.className).toContain("absolute");
    expect(within(list).getByRole("option", { name: "A" })).toHaveAttribute("aria-selected", "true");
  });
});
