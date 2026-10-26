// The organization console's hamburger and navigation drawer (Topbar + Sidebar, every
// /organization/* route).
//
// Reported on an Android device: taps on the hamburger sometimes did nothing, and it sat too
// high. The app was drawn under the status bar, and Android keeps that strip's taps for
// itself, so only the lower part of the button worked. The fix pads the topbar and drawer by
// env(safe-area-inset-top) (non-zero only in the native build, which sets viewport-fit=cover),
// gives the hamburger a 44×44 target, and lifts the drawer above the topbar so its own Close
// button can be tapped. jsdom has no layout: these pin the markup. Geometry was measured in
// Chromium (320–768px, inset 0 and 40px, plus a simulated status-bar strip).
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../api", () => ({
  default: { get: vi.fn(), post: vi.fn() },
  errMsg: (e) => e?.message ?? "Something went wrong.",
}));

vi.mock("../auth/AuthContext", () => ({
  useAuth: () => ({ user: { full_name: "Vihari Owner", email: "owner@example.com" }, logout: vi.fn() }),
}));

import api from "../api";
import { ThemeProvider } from "../theme/ThemeContext";
import OrganizationLayout from "./OrganizationLayout";

const CONSOLE_STATE = {
  organization: { name: "Northwind", plan: "Growth" },
  workspace: { label: "production", slug: "production" },
  workspaces: [{ label: "production", slug: "production" }],
  badges: {},
  user: { name: "Vihari Owner", email: "owner@example.com", role: "Organization Owner" },
  health: { status: "ok" },
};

beforeEach(() => {
  vi.mocked(api.get).mockResolvedValue({ data: CONSOLE_STATE });
  localStorage.clear();
});

function renderShell(at = "/organization/sessions") {
  return render(
    <ThemeProvider>
      <MemoryRouter initialEntries={[at]}>
        <Routes>
          <Route element={<OrganizationLayout />}>
            <Route path="/organization/sessions" element={<h1>Streaming Sessions page</h1>} />
            <Route path="/organization/dashboard" element={<h1>Dashboard page</h1>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </ThemeProvider>
  );
}

const classesOf = (el) => (el.getAttribute("class") || "").split(/\s+/);
const hamburger = () => screen.getByRole("button", { name: /navigation menu$/ });
const drawer = () => document.querySelector("aside");
const backdrop = () => document.querySelector("div.fixed.inset-0");
const isOpen = () => classesOf(drawer()).includes("translate-x-0");

describe("the hamburger", () => {
  it("is a real button with a 44×44 target whose icon stays where it was", async () => {
    renderShell();
    await screen.findByRole("heading", { name: "Streaming Sessions page" });
    const btn = hamburger();
    expect(btn).toHaveAttribute("type", "button");
    expect(classesOf(btn)).toEqual(expect.arrayContaining(["h-11", "w-11", "-ml-1", "lg:hidden"]));
    expect(classesOf(btn)).not.toContain("h-9");
    expect(btn.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
    expect(btn.className).toMatch(/focus-visible:/);
  });

  it("says what a tap will do, and reports the drawer's state", async () => {
    const user = userEvent.setup();
    renderShell();
    await screen.findByRole("heading", { name: "Streaming Sessions page" });
    expect(hamburger()).toHaveAccessibleName("Open navigation menu");
    expect(hamburger()).toHaveAttribute("aria-expanded", "false");
    await user.click(hamburger());
    expect(hamburger()).toHaveAccessibleName("Close navigation menu");
    expect(hamburger()).toHaveAttribute("aria-expanded", "true");
  });

  it("toggles exactly once per tap, however many times it is used", async () => {
    const user = userEvent.setup();
    renderShell();
    await screen.findByRole("heading", { name: "Streaming Sessions page" });
    for (let i = 0; i < 6; i++) {
      await user.click(hamburger());
      expect(isOpen()).toBe(i % 2 === 0);
    }
  });

  it("opens from the keyboard", async () => {
    const user = userEvent.setup();
    renderShell();
    await screen.findByRole("heading", { name: "Streaming Sessions page" });
    hamburger().focus();
    await user.keyboard("{Enter}");
    expect(isOpen()).toBe(true);
  });

  it("is the same control on another organization page", async () => {
    renderShell("/organization/dashboard");
    await screen.findByRole("heading", { name: "Dashboard page" });
    expect(hamburger()).toHaveAccessibleName("Open navigation menu");
  });
});

describe("the open drawer", () => {
  it("closes from its own Close button and from the backdrop", async () => {
    const user = userEvent.setup();
    renderShell();
    await screen.findByRole("heading", { name: "Streaming Sessions page" });
    await user.click(hamburger());
    await user.click(within(drawer()).getByRole("button", { name: "Close menu" }));
    expect(isOpen()).toBe(false);
    await user.click(hamburger());
    await user.click(backdrop());
    expect(isOpen()).toBe(false);
  });

  it("sits above the topbar on mobile (z.overlay over z.header), and on its z-30 rail on desktop", async () => {
    const user = userEvent.setup();
    renderShell();
    await screen.findByRole("heading", { name: "Streaming Sessions page" });
    await user.click(hamburger());
    expect(classesOf(screen.getByRole("banner"))).toContain("z-50");
    expect(classesOf(backdrop())).toContain("z-[60]");
    expect(classesOf(drawer())).toEqual(expect.arrayContaining(["z-[60]", "lg:z-30"]));
  });
});

describe("status-bar clearance", () => {
  it("pads the topbar and drawer by the top safe-area inset, keeping the 64px control row", async () => {
    renderShell();
    await screen.findByRole("heading", { name: "Streaming Sessions page" });
    expect(classesOf(screen.getByRole("banner"))).toEqual(
      expect.arrayContaining(["h-[calc(4rem+env(safe-area-inset-top))]", "pt-[env(safe-area-inset-top)]"])
    );
    expect(classesOf(drawer())).toContain("pt-[env(safe-area-inset-top)]");
  });

  it("leaves the desktop collapse control at its original 36px size", async () => {
    renderShell();
    await screen.findByRole("heading", { name: "Streaming Sessions page" });
    const collapse = screen.getByRole("button", { name: /collapse sidebar|expand sidebar/i });
    expect(classesOf(collapse)).toEqual(expect.arrayContaining(["h-9", "w-9"]));
  });
});
