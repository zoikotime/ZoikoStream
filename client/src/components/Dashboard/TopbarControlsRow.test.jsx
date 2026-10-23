// The organization topbar's right-hand controls (+ Request live event, theme toggle, account
// avatar) on the Android app.
//
// Reported from a device screenshot taken before the status-bar fix: these controls sat in the
// status-bar strip, as the hamburger did. They need no offset of their own. They share the
// hamburger's row, and that row is pushed below the status bar by ONE padding on the header
// (pt-[env(safe-area-inset-top)]). What must not regress: a second padding, or an absolute/
// negative/translate offset on a control, which would split the row or double the clearance.
// Measured in Chromium (320–768px, simulated insets 0/24/40/48): all four controls share one
// centre line that moves by exactly the inset. jsdom has no layout, so this pins the structure.
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

vi.mock("../../api", () => ({
  default: { get: vi.fn(() => Promise.resolve({ data: {} })), post: vi.fn() },
  errMsg: (e) => e?.message ?? "Something went wrong.",
}));

vi.mock("../../auth/AuthContext", () => ({
  useAuth: () => ({ user: { full_name: "Vihari Owner", email: "owner@example.com" }, logout: vi.fn() }),
}));

import { ThemeProvider } from "../../theme/ThemeContext";
import Topbar from "./Topbar";

const renderTopbar = () =>
  render(
    <ThemeProvider>
      <MemoryRouter>
        <Topbar state={{ health: { status: "ok" } }} unknown={false} onRetry={vi.fn()} onMenuClick={vi.fn()} />
      </MemoryRouter>
    </ThemeProvider>
  );

const classesOf = (el) => (el.getAttribute("class") || "").split(/\s+/).filter(Boolean);
// Anything that would move a control independently of the header's flex row.
const OFFSET = /^(-?m[ty]-|-?top-|absolute$|fixed$|-?translate-y|pt-)/;

const controls = () => ({
  hamburger: screen.getByRole("button", { name: "Open navigation menu" }),
  plus: screen.getByRole("button", { name: /request live event/i }),
  theme: screen.getByRole("switch", { name: /switch to (light|dark) theme/i }),
  avatar: screen.getByRole("button", { name: "Account menu" }),
});

describe("the topbar's right-hand controls", () => {
  it("sit in the hamburger's row: one header, one centred flex line", () => {
    renderTopbar();
    const header = screen.getByRole("banner");
    expect(classesOf(header)).toEqual(expect.arrayContaining(["flex", "items-center"]));
    const { hamburger, plus, theme, avatar } = controls();
    expect(hamburger.parentElement).toBe(header);
    const group = plus.parentElement;
    expect(classesOf(group)).toEqual(expect.arrayContaining(["ml-auto", "flex", "items-center"]));
    expect(group.parentElement).toBe(header);
    expect(theme.parentElement).toBe(group);
    expect(avatar.closest("div.ml-auto")).toBe(group);
  });

  it("carry no offset of their own between themselves and the header", () => {
    renderTopbar();
    const header = screen.getByRole("banner");
    for (const [name, el] of Object.entries(controls())) {
      for (let n = el; n && n !== header; n = n.parentElement) {
        const offsets = classesOf(n).filter((c) => OFFSET.test(c));
        expect(offsets, `${name}: ${n.tagName.toLowerCase()} carries ${offsets.join(" ")}`).toEqual([]);
      }
    }
  });

  it("are cleared of the status bar by the header's single safe-area padding", () => {
    renderTopbar();
    const header = screen.getByRole("banner");
    const paddings = classesOf(header).filter((c) => /^pt-/.test(c));
    expect(paddings).toEqual(["pt-[env(safe-area-inset-top)]"]);
    expect(classesOf(header)).toContain("h-[calc(4rem+env(safe-area-inset-top))]");
  });
});
