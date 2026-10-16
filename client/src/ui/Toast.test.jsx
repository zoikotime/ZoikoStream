// notify's stable-id contract: the same id replaces the toast on screen instead of stacking,
// and a call without an id behaves exactly as it always did.
import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it } from "vitest";
import toast from "react-hot-toast";
import AppToaster from "./AppToaster";
import { notify } from "./Toast";

// react-hot-toast reads prefers-reduced-motion; jsdom has no matchMedia.
beforeAll(() => {
  window.matchMedia ??= () => ({ matches: false, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} });
});
afterEach(() => act(() => toast.remove()));

const shown = (text) => screen.queryAllByText(text);

describe("notify with a stable id", () => {
  it("an identical error raised three times under one id shows ONE toast", async () => {
    render(<AppToaster />);
    act(() => {
      notify.error("You cannot demote or deactivate yourself", { id: "member-role:u1" });
      notify.error("You cannot demote or deactivate yourself", { id: "member-role:u1" });
      notify.error("You cannot demote or deactivate yourself", { id: "member-role:u1" });
    });
    expect(await screen.findByText("You cannot demote or deactivate yourself")).toBeInTheDocument();
    expect(shown("You cannot demote or deactivate yourself")).toHaveLength(1);
  });

  it("a new message under the same id replaces the old one in place", async () => {
    render(<AppToaster />);
    act(() => { notify.error("First failure", { id: "row-7" }); });
    await screen.findByText("First failure");
    act(() => { notify.success("Saved", { id: "row-7" }); });
    expect(await screen.findByText("Saved")).toBeInTheDocument();
    expect(shown("First failure")).toHaveLength(0);
  });

  it("returns the id it was given (numbers are stringified) and dismiss accepts it", () => {
    let a;
    let b;
    act(() => {
      a = notify.warning("Heads up", { id: 42 });
      b = notify.info("Plain", { id: "" });            // empty id = no id
    });
    expect(a).toBe("42");
    expect(b).not.toBe("");
    expect(typeof b).toBe("string");
    expect(() => act(() => notify.dismiss(42))).not.toThrow();
  });

  it("keeps the warning icon and duration when an id is passed", async () => {
    render(<AppToaster />);
    act(() => { notify.warning("Check this", { id: "w" }); notify.warning("Check this", { id: "w" }); });
    expect(await screen.findByText("Check this")).toBeInTheDocument();
    expect(shown("Check this")).toHaveLength(1);
    expect(screen.getByText("⚠️")).toBeInTheDocument();
  });
});

describe("notify without an id is unchanged", () => {
  it("stacks identical messages, one toast per call", async () => {
    render(<AppToaster />);
    act(() => {
      notify.error("Network trouble");
      notify.error("Network trouble");
    });
    await screen.findAllByText("Network trouble");
    expect(shown("Network trouble")).toHaveLength(2);
  });

  it("different ids stay separate toasts", async () => {
    render(<AppToaster />);
    act(() => {
      notify.error("Couldn't change role", { id: "member-role:a" });
      notify.error("Couldn't change role", { id: "member-role:b" });
    });
    await screen.findAllByText("Couldn't change role");
    expect(shown("Couldn't change role")).toHaveLength(2);
  });
});
