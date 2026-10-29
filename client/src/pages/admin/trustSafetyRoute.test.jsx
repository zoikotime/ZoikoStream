import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";

vi.mock("../../api", () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  errMsg: (error) => error?.message ?? "failed",
  diagnoseLoadError: (error) => error?.message ?? "failed to load",
  AUTH_EXPIRED_EVENT: "zoiko:auth-expired",
}));
vi.mock("../../auth/AuthContext", () => ({
  useAuth: () => ({ user: { id: "u-me", full_name: "Ada Ops", role: "super_admin" }, logout: vi.fn() }),
}));

import api from "../../api";
import AdminSidebar from "../../components/admin/AdminSidebar";
import { ThemeProvider } from "../../theme/ThemeContext";
import Security from "./Security";
import appSource from "../../App.jsx?raw";

const CANONICAL = "/admin/trust-safety";

function Where() {
  const { pathname, search, hash } = useLocation();
  return <div data-testid="where">{pathname + search + hash}</div>;
}

function HistoryControls() {
  const navigate = useNavigate();
  return (
    <>
      <button onClick={() => navigate(-1)}>Back</button>
      <button onClick={() => navigate(1)}>Forward</button>
    </>
  );
}

function LegacySecurityRedirect() {
  const { search, hash } = useLocation();
  return <Navigate to={`${CANONICAL}${search}${hash}`} replace />;
}

const routed = (entry) =>
  render(
    <ThemeProvider>
      <MemoryRouter initialEntries={["/admin/dashboard", entry]} initialIndex={1}>
        <Where />
        <HistoryControls />
        <AdminSidebar open onClose={() => {}} state={{}} unknown={false} onChange={() => {}} />
        <Routes>
          <Route path="/admin/trust-safety" element={<Security />} />
          <Route path="/admin/security" element={<LegacySecurityRedirect />} />
          <Route path="/admin/audit" element={<h1>Audit</h1>} />
          <Route path="/admin/dashboard" element={<h1>Command Center</h1>} />
        </Routes>
      </MemoryRouter>
    </ThemeProvider>
  );

const current = () => screen.getByTestId("where").textContent;

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.get).mockImplementation((url) =>
    url === "/admin/incidents"
      ? Promise.resolve({ data: { items: [], total: 0 } })
      : Promise.resolve({ data: { items: [], total: 0 } })
  );
});

describe("Trust & Safety route", () => {
  it("uses /admin/trust-safety for direct navigation and refresh", async () => {
    routed(CANONICAL);
    expect(await screen.findByRole("heading", { name: "Trust & Safety" })).toBeInTheDocument();
    expect(current()).toBe(CANONICAL);
  });

  it("redirects legacy /admin/security links while preserving query and hash", async () => {
    routed("/admin/security?status=resolved#case-1");
    expect(await screen.findByRole("heading", { name: "Trust & Safety" })).toBeInTheDocument();
    expect(current()).toBe(`${CANONICAL}?status=resolved#case-1`);
  });

  it("highlights only Trust & Safety on its canonical route", async () => {
    routed(CANONICAL);
    await screen.findByRole("heading", { name: "Trust & Safety" });
    const active = document.querySelectorAll('aside a[aria-current="page"]');

    expect(active).toHaveLength(1);
    expect(active[0]).toHaveAttribute("href", CANONICAL);
    expect(active[0]).toHaveTextContent("Trust & Safety");
  });

  it("keeps the active sidebar feature in sync through neighboring routes and history", async () => {
    routed("/admin/audit");
    expect(await screen.findByRole("heading", { name: "Audit" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("link", { name: "Trust & Safety" }));
    expect(await screen.findByRole("heading", { name: "Trust & Safety" })).toBeInTheDocument();
    expect(current()).toBe(CANONICAL);

    fireEvent.click(screen.getByRole("button", { name: "Back" }));
    await waitFor(() => expect(current()).toBe("/admin/audit"));
    expect(await screen.findByRole("heading", { name: "Audit" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Forward" }));
    await waitFor(() => expect(current()).toBe(CANONICAL));
    expect(await screen.findByRole("heading", { name: "Trust & Safety" })).toBeInTheDocument();
  });

  it("replaces a legacy URL in history rather than creating a redirect loop", async () => {
    routed("/admin/security?tab=cases");
    expect(await screen.findByRole("heading", { name: "Trust & Safety" })).toBeInTheDocument();
    expect(current()).toBe(`${CANONICAL}?tab=cases`);
    fireEvent.click(screen.getByRole("button", { name: "Back" }));
    await waitFor(() => expect(current()).toBe("/admin/dashboard"));
    expect(await screen.findByRole("heading", { name: "Command Center" })).toBeInTheDocument();
  });

  it("uses the feature label in the breadcrumb and routes old URLs through a redirect", async () => {
    const topbar = await import("../../components/admin/AdminTopbar.jsx?raw");
    const sidebar = await import("../../components/admin/AdminSidebar.jsx?raw");

    expect(topbar.default).toContain('"trust-safety": "Trust & Safety"');
    expect(sidebar.default).toContain('to: "/admin/trust-safety", label: "Trust & Safety"');
    expect(appSource).toContain('<Route path="/admin/trust-safety" element={<TrustSafety />} />');
    expect(appSource).toContain('<Route path="/admin/security" element={<LegacyTrustSafetyRedirect />} />');
    expect(appSource).toContain('to={`/admin/trust-safety${search}${hash}`} replace');
  });

  it("keeps the page under the existing Super Admin guard and AdminLayout", () => {
    const guard = appSource.indexOf('<Route element={<RoleRoute allow={["super_admin"]} />}>');
    const shell = appSource.indexOf("<Route element={<AdminLayout />}>", guard);
    const route = appSource.indexOf('<Route path="/admin/trust-safety" element={<TrustSafety />} />', shell);
    const legacy = appSource.indexOf('<Route path="/admin/security" element={<LegacyTrustSafetyRedirect />} />', route);
    const shellEnd = appSource.indexOf("</Route>", legacy);

    expect(guard).toBeGreaterThanOrEqual(0);
    expect(shell).toBeGreaterThan(guard);
    expect(route).toBeGreaterThan(shell);
    expect(legacy).toBeGreaterThan(route);
    expect(shellEnd).toBeGreaterThan(legacy);
  });
});
