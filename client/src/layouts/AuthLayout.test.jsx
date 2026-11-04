// The auth shell: Login / Register / Reset Password tabs are real routes, and the floating
// hero scene is decoration that assistive tech never reaches.
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";
import AuthLayout from "./AuthLayout";
import AuthCard from "../pages/auth/AuthCard";

const renderAt = (path) =>
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route element={<AuthLayout />}>
          <Route path="/login" element={<AuthCard tabs title="Welcome back" />} />
          <Route path="/signup" element={<AuthCard tabs title="Create your organization" />} />
          <Route path="/forgot-password" element={<AuthCard tabs title="Reset your password" />} />
          <Route path="/verify-email" element={<AuthCard title="Verify email" />} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );

const tabs = () => within(screen.getByRole("navigation", { name: "Authentication" }));

describe("auth tabs", () => {
  it("link Login, Register and Reset Password to their existing routes and mark the current one", () => {
    renderAt("/login");
    expect(tabs().getByRole("link", { name: "Login" })).toHaveAttribute("href", "/login");
    expect(tabs().getByRole("link", { name: "Register" })).toHaveAttribute("href", "/signup");
    expect(tabs().getByRole("link", { name: "Reset Password" })).toHaveAttribute("href", "/forgot-password");
    expect(tabs().getByRole("link", { name: "Login" })).toHaveAttribute("aria-current", "page");
  });

  it("switch the form by navigating, so back/forward and deep links keep working", async () => {
    const user = userEvent.setup();
    renderAt("/login");
    await user.click(tabs().getByRole("link", { name: "Register" }));
    expect(screen.getByRole("heading", { level: 1, name: "Create your organization" })).toBeInTheDocument();
    expect(tabs().getByRole("link", { name: "Register" })).toHaveAttribute("aria-current", "page");

    await user.click(tabs().getByRole("link", { name: "Reset Password" }));
    expect(screen.getByRole("heading", { level: 1, name: "Reset your password" })).toBeInTheDocument();
  });

  it("are not shown on pages outside the three-way switch", () => {
    renderAt("/verify-email");
    expect(screen.getByRole("heading", { level: 1, name: "Verify email" })).toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "Authentication" })).toBeNull();
  });
});

describe("auth layout", () => {
  it("keeps exactly one page heading — the hero headline is not an <h1>", () => {
    renderAt("/login");
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
  });

  it("states the platform's capabilities as readable text", () => {
    renderAt("/login");
    for (const label of ["Live & on-demand events", "Global audience reach", "Secure streaming", "Engagement and analytics"]) {
      // The desktop column and the < lg header both carry the tiles (CSS shows one).
      expect(screen.getAllByText(label).length).toBeGreaterThan(0);
    }
  });

  it("hides the floating mock product cards from assistive tech", () => {
    renderAt("/login");
    // Mock UI copy exists in the DOM for sighted users, but only inside aria-hidden layers.
    for (const text of ["Global Product Keynote", "Main stage · Live now"]) {
      for (const node of screen.queryAllByText(text)) {
        expect(node.closest('[aria-hidden="true"]')).not.toBeNull();
      }
    }
  });
});
