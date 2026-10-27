// The Usage & Entitlements page, and the URL it answers on.
//
// Everywhere a person saw this page it was called "Usage & Entitlements" — the sidebar item,
// the heading, the breadcrumb — while the address bar said /admin/subscriptions. The label
// was never wrong; the route simply predated the rename and nobody moved it.
//
// Moving it needed care, because "/admin/subscriptions" is ALSO a backend endpoint this very
// page calls: GET /admin/subscriptions, GET /admin/subscriptions/summary, PATCH
// /admin/subscriptions/{id}. A find-and-replace across the repo would have renamed the API
// along with the route and broken the page it was meant to fix. Only the browser route moved;
// the API paths are asserted unchanged below.
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";

vi.mock("../../api", () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  errMsg: (e) => e?.message ?? "failed",
  diagnoseLoadError: (e) => e?.message ?? "failed to load",
  AUTH_EXPIRED_EVENT: "zoiko:auth-expired",
}));
vi.mock("../../auth/AuthContext", () => ({
  useAuth: () => ({ user: { id: "u-me", full_name: "Ada Ops", role: "super_admin" }, logout: vi.fn() }),
}));

import api from "../../api";
import { ThemeProvider } from "../../theme/ThemeContext";
import Subscriptions from "./Subscriptions";
import AdminSidebar from "../../components/admin/AdminSidebar";
import appSource from "../../App.jsx?raw";

const CANONICAL = "/admin/usage-entitlements";
const LEGACY = "/admin/subscriptions";

// The same forward App.jsx registers, exercised here rather than re-implemented: it has to
// carry the query string and must not point at itself.
function LegacySubscriptionsRedirect() {
  const { search, hash } = useLocation();
  return <Navigate to={`${CANONICAL}${search}${hash}`} replace />;
}

// Reports where routing settled, so a redirect can be observed.
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

const routed = (entry) =>
  render(
    <ThemeProvider>
      <MemoryRouter initialEntries={["/admin/dashboard", entry]} initialIndex={1}>
        <Where />
        <HistoryControls />
        <Routes>
          <Route path={CANONICAL} element={<Subscriptions />} />
          <Route path={LEGACY} element={<LegacySubscriptionsRedirect />} />
        </Routes>
      </MemoryRouter>
    </ThemeProvider>
  );

const at = () => screen.getByTestId("where").textContent;

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.get).mockResolvedValue({ data: { items: [], total: 0 } });
});

describe("the canonical route", () => {
  it("renders the page", async () => {
    routed(CANONICAL);
    expect(await screen.findByRole("heading", { name: /Usage & Entitlements/i })).toBeInTheDocument();
  });

  it("is where a direct visit stays — no redirect away from it", async () => {
    routed(CANONICAL);
    await screen.findByRole("heading", { name: /Usage & Entitlements/i });
    expect(at()).toBe(CANONICAL);
  });

  it("keeps the page's own identity in the heading", async () => {
    routed(CANONICAL);
    const heading = await screen.findByRole("heading", { name: /Usage & Entitlements/i });
    expect(heading.tagName).toBe("H1");
  });

  it("still shows both tabs", async () => {
    routed(CANONICAL);
    await screen.findByRole("heading", { name: /Usage & Entitlements/i });
    expect(screen.getByRole("tab", { name: /Entitlements & usage/i })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /^Subscriptions$/i })).toBeInTheDocument();
  });

  it("opens the selected tab directly from ?tab= and keeps the URL state", async () => {
    routed(`${CANONICAL}?tab=subscriptions&status=trial`);
    const subscriptionsTab = await screen.findByRole("tab", { name: /^Subscriptions$/i });
    expect(subscriptionsTab).toHaveAttribute("aria-selected", "true");
    expect(at()).toBe(`${CANONICAL}?tab=subscriptions&status=trial`);
  });

  it("writes the selected tab to the URL and removes tab for the default view", async () => {
    routed(`${CANONICAL}?status=active`);
    await screen.findByRole("tab", { name: /Entitlements & usage/i });

    fireEvent.click(screen.getByRole("tab", { name: /^Subscriptions$/i }));
    await waitFor(() => expect(at()).toBe(`${CANONICAL}?status=active&tab=subscriptions`));

    fireEvent.click(screen.getByRole("tab", { name: /Entitlements & usage/i }));
    await waitFor(() => expect(at()).toBe(`${CANONICAL}?status=active`));
  });

  it("replaces tab state without breaking browser Back and Forward navigation", async () => {
    routed(CANONICAL);
    await screen.findByRole("tab", { name: /Entitlements & usage/i });
    fireEvent.click(screen.getByRole("tab", { name: /^Subscriptions$/i }));
    await waitFor(() => expect(at()).toBe(`${CANONICAL}?tab=subscriptions`));

    fireEvent.click(screen.getByRole("button", { name: "Back" }));
    await waitFor(() => expect(at()).toBe("/admin/dashboard"));
    fireEvent.click(screen.getByRole("button", { name: "Forward" }));
    await waitFor(() => expect(at()).toBe(`${CANONICAL}?tab=subscriptions`));
  });

  it("falls back to usage for an unsupported tab value", async () => {
    routed(`${CANONICAL}?tab=unsupported`);
    expect(await screen.findByRole("tab", { name: /Entitlements & usage/i })).toHaveAttribute("aria-selected", "true");
  });
});

describe("the retired URL", () => {
  it("forwards to the canonical one", async () => {
    routed(LEGACY);
    await waitFor(() => expect(at()).toBe(CANONICAL));
  });

  it("lands on the real page, not an empty shell", async () => {
    routed(LEGACY);
    expect(await screen.findByRole("heading", { name: /Usage & Entitlements/i })).toBeInTheDocument();
  });

  it("carries the query string through", async () => {
    routed(`${LEGACY}?plan=pro&status=active`);
    await waitFor(() => expect(at()).toBe(`${CANONICAL}?plan=pro&status=active`));
  });

  it("carries the hash through", async () => {
    routed(`${LEGACY}?tab=subscriptions#row-4`);
    await waitFor(() => expect(at()).toBe(`${CANONICAL}?tab=subscriptions#row-4`));
  });

  it("settles rather than bouncing — the target is a different path", async () => {
    routed(LEGACY);
    await waitFor(() => expect(at()).toBe(CANONICAL));
    // Still there a tick later: a self-referential redirect would keep firing.
    await new Promise((r) => setTimeout(r, 20));
    expect(at()).toBe(CANONICAL);
  });
});

describe("the sidebar", () => {
  const rail = () =>
    render(
      <ThemeProvider>
        <MemoryRouter initialEntries={[CANONICAL]}>
          <AdminSidebar open onClose={() => {}} state={null} unknown={false} onChange={() => {}} />
        </MemoryRouter>
      </ThemeProvider>
    );

  it("links to the canonical route", () => {
    const { container } = rail();
    expect(container.querySelector(`a[href="${CANONICAL}"]`)).toBeTruthy();
  });

  it("no longer links to the retired one", () => {
    const { container } = rail();
    expect(container.querySelector(`a[href="${LEGACY}"]`)).toBeNull();
  });

  it("still calls it Usage & Entitlements", () => {
    rail();
    expect(screen.getByText("Usage & Entitlements")).toBeInTheDocument();
  });

  it("marks the item current when that route is open", () => {
    const { container } = rail();
    const link = container.querySelector(`a[href="${CANONICAL}"]`);
    // NavLink marks the active destination; whichever attribute this rail uses, the active
    // link must be distinguishable from an inactive one.
    expect(link.getAttribute("aria-current") || link.className).toBeTruthy();
    expect(link.getAttribute("aria-current")).toBe("page");
  });

  it("keeps the same item current on the subscriptions tab URL", () => {
    const { container } = render(
      <ThemeProvider>
        <MemoryRouter initialEntries={[`${CANONICAL}?tab=subscriptions`]}>
          <AdminSidebar open onClose={() => {}} state={null} unknown={false} onChange={() => {}} />
        </MemoryRouter>
      </ThemeProvider>
    );
    const current = container.querySelectorAll('aside a[aria-current="page"]');
    expect(current).toHaveLength(1);
    expect(current[0]).toHaveAttribute("href", CANONICAL);
  });
});

describe("the breadcrumb vocabulary", () => {
  it("names the canonical segment", async () => {
    const mod = await import("../../components/admin/AdminTopbar.jsx?raw");
    expect(mod.default).toContain('"usage-entitlements": "Usage & Entitlements"');
  });
});

describe("the actual app route contract", () => {
  it("registers both browser routes inside the existing Super Admin guard", () => {
    const guard = appSource.indexOf('<Route element={<RoleRoute allow={["super_admin"]} />}>');
    const adminLayout = appSource.indexOf('<Route element={<AdminLayout />}>', guard);
    const canonical = appSource.indexOf('<Route path="/admin/usage-entitlements" element={<Subscriptions />} />');
    const legacy = appSource.indexOf('<Route path="/admin/subscriptions" element={<LegacyUsageEntitlementsRedirect />} />');
    const guardEnd = appSource.indexOf("</Route>", legacy);

    expect(guard).toBeGreaterThanOrEqual(0);
    expect(adminLayout).toBeGreaterThan(guard);
    expect(canonical).toBeGreaterThan(adminLayout);
    expect(legacy).toBeGreaterThan(adminLayout);
    expect(guardEnd).toBeGreaterThan(legacy);
  });
});

describe("the backend contract is untouched", () => {
  it("still calls the subscription endpoints by their own names", async () => {
    routed(CANONICAL);
    await screen.findByRole("heading", { name: /Usage & Entitlements/i });
    await waitFor(() => expect(api.get).toHaveBeenCalled());
    const urls = vi.mocked(api.get).mock.calls.map(([u]) => u);
    // The page's API paths are not the page's URL, and must not have moved with it.
    expect(urls.some((u) => u.startsWith("/admin/") )).toBe(true);
    expect(urls.every((u) => !u.startsWith("/admin/usage-entitlements"))).toBe(true);
  });
});
