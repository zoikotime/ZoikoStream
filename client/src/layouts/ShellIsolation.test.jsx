// The organization shell was rebuilt; the other three must not have moved.
//
// Admin, the legacy /dashboard shell and the host/viewer areas each have their own chrome,
// and none of them shares a component with the organization rail — AdminSidebar mentions
// components/Dashboard/Sidebar in a COMMENT only. These tests hold that boundary, because
// "I only changed the org console" is the kind of claim that quietly stops being true.
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../api", () => ({
  default: { get: vi.fn(), post: vi.fn() },
  errMsg: (e) => e?.message ?? "Something went wrong.",
}));

vi.mock("../auth/AuthContext", () => ({
  useAuth: () => ({ user: { full_name: "Sam Admin", email: "sam@example.com", role: "super_admin" }, logout: vi.fn() }),
}));

import api from "../api";
import { ThemeProvider } from "../theme/ThemeContext";
import AdminLayout from "./AdminLayout";
import MainLayout from "./MainLayout";

beforeEach(() => {
  vi.mocked(api.get).mockResolvedValue({
    data: { health: { status: "ok" }, badges: {}, counts: {} },
  });
});

function renderLayout(Layout, at, label) {
  return render(
    <ThemeProvider>
      <MemoryRouter initialEntries={[at]}>
        <Routes>
          <Route element={<Layout />}>
            <Route path={at} element={<h1>{label}</h1>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </ThemeProvider>
  );
}

// Labels unique to the organization rail. If one of these turns up in another console, the
// shells have started sharing something they should not.
const ORG_ONLY = [/playback & access/i, /live inputs/i, /streaming sessions/i];

describe("the Super Admin console is untouched", () => {
  it("keeps its own grouped navigation", async () => {
    renderLayout(AdminLayout, "/admin/dashboard", "admin page");
    await screen.findByText("admin page");
    const rail = screen.getAllByRole("navigation")[0];
    // The admin rail has always been grouped, and this change did not touch it.
    expect(within(rail).getByText(/^operate$/i)).toBeInTheDocument();
    expect(within(rail).getByRole("link", { name: /command center/i })).toBeInTheDocument();
  });

  it("does not pick up the organization rail", async () => {
    renderLayout(AdminLayout, "/admin/dashboard", "admin page");
    await screen.findByText("admin page");
    const rail = screen.getAllByRole("navigation")[0];
    for (const label of ORG_ONLY) {
      expect(within(rail).queryByText(label)).not.toBeInTheDocument();
    }
  });
});

describe("the legacy /dashboard shell is untouched", () => {
  it("keeps its own navigation and does not pick up the organization rail", async () => {
    renderLayout(MainLayout, "/dashboard", "legacy page");
    await screen.findByText("legacy page");
    const rail = screen.getAllByRole("navigation")[0];
    for (const label of ORG_ONLY) {
      expect(within(rail).queryByText(label)).not.toBeInTheDocument();
    }
  });
});
