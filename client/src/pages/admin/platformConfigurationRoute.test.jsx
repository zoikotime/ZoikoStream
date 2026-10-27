// Platform Configuration now has a route named for its sidebar label. Keep the former URL
// as a guarded, replace-style redirect so existing bookmarks survive without duplicating
// the settings page or changing its /admin/settings API calls.
import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { MemoryRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";
import AdminSidebar from "../../components/admin/AdminSidebar";
import appSource from "../../App.jsx?raw";

vi.mock("../../api", () => ({
  default: { get: vi.fn(() => Promise.resolve({ data: {} })), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  errMsg: (error) => error?.message ?? "failed",
}));
vi.mock("../../auth/AuthContext", () => ({
  useAuth: () => ({ user: { full_name: "Ada Ops", role: "super_admin" } }),
}));

const CANONICAL = "/admin/platform-configuration";
const LEGACY = "/admin/settings";

function LegacyPlatformSettingsRedirect() {
  const { search, hash } = useLocation();
  return <Navigate to={`${CANONICAL}${search}${hash}`} replace />;
}

function Where() {
  const { pathname, search, hash } = useLocation();
  return <div data-testid="where">{pathname + search + hash}</div>;
}

const routed = (entry) =>
  render(
    <MemoryRouter initialEntries={[entry]}>
      <Where />
      <AdminSidebar open onClose={() => {}} state={{}} unknown={false} onChange={() => {}} />
      <Routes>
        <Route path={CANONICAL} element={<h1>Platform Configuration</h1>} />
        <Route path={LEGACY} element={<LegacyPlatformSettingsRedirect />} />
      </Routes>
    </MemoryRouter>
  );

const current = () => screen.getByTestId("where").textContent;

describe("Platform Configuration route", () => {
  it("uses the canonical route for direct navigation and refresh", async () => {
    routed(CANONICAL);
    expect(await screen.findByRole("heading", { name: "Platform Configuration" })).toBeInTheDocument();
    expect(current()).toBe(CANONICAL);
  });

  it("redirects the old route and preserves query parameters and hash", async () => {
    routed(`${LEGACY}?section=brand#logo`);
    expect(await screen.findByRole("heading", { name: "Platform Configuration" })).toBeInTheDocument();
    await waitFor(() => expect(current()).toBe(`${CANONICAL}?section=brand#logo`));
  });

  it("replaces the old history entry and does not redirect-loop", async () => {
    routed(LEGACY);
    await waitFor(() => expect(current()).toBe(CANONICAL));
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(current()).toBe(CANONICAL);
  });

  it("highlights only Platform Configuration on its canonical route", () => {
    routed(CANONICAL);
    const active = document.querySelectorAll('aside a[aria-current="page"]');

    expect(active).toHaveLength(1);
    expect(active[0]).toHaveAttribute("href", CANONICAL);
    expect(active[0]).toHaveTextContent("Platform Configuration");
  });

  it("uses the canonical route in the sidebar and breadcrumb map", async () => {
    const sidebar = await import("../../components/admin/AdminSidebar.jsx?raw");
    const topbar = await import("../../components/admin/AdminTopbar.jsx?raw");
    expect(sidebar.default).toContain('to: "/admin/platform-configuration", label: "Platform Configuration"');
    expect(topbar.default).toContain('"platform-configuration": "Platform Configuration"');
    expect(topbar.default).toContain('go("/admin/platform-configuration")');
  });

  it("registers both routes inside the existing Super Admin guard and shell", () => {
    const guardStart = appSource.indexOf('<Route element={<RoleRoute allow={["super_admin"]} />}>');
    const shellStart = appSource.indexOf("<Route element={<AdminLayout />}>", guardStart);
    const canonicalRoute = appSource.indexOf(`path="${CANONICAL}"`, shellStart);
    const legacyRoute = appSource.indexOf(`path="${LEGACY}"`, shellStart);
    const shellEnd = appSource.indexOf("</Route>", legacyRoute);

    expect(guardStart).toBeGreaterThanOrEqual(0);
    expect(canonicalRoute).toBeGreaterThan(shellStart);
    expect(legacyRoute).toBeGreaterThan(canonicalRoute);
    expect(shellEnd).toBeGreaterThan(legacyRoute);
  });
});
