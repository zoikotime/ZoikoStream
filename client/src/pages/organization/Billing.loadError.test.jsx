// What Billing says when it cannot load at all.
//
// ── THE BUG ─────────────────────────────────────────────────────────────────────────────
// The page showed "Couldn't load billing information. Try refreshing the page." for every
// cause, and that advice cannot work for any of the real ones. The case actually hit: the dev
// client's fallback API base named a different port from the one the API ran on. With
// VITE_API_URL not loaded — a dev server started before that line reached .env, since Vite reads
// env files once at startup — every request went to a port with nothing behind it. The backend
// was healthy the whole time and the page said nothing that would lead anyone to the port.
// (The local API now runs on 8001, because 8000 belongs to another local API on the dev machine.)
//
// An expired session and a restricted organization land in the same branch and need completely
// different fixes, so the message has to tell them apart.
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

// The REAL diagnoseLoadError and API_BASE: the wording and the port are what is under test.
vi.mock("../../api", async (importOriginal) => {
  const real = await importOriginal();
  return { ...real, default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() } };
});
vi.mock("../../ui/Toast", () => ({ notify: { success: vi.fn(), error: vi.fn() } }));

import api, { API_BASE } from "../../api";
import { ThemeProvider } from "../../theme/ThemeContext";
import OrganizationBilling from "./Billing";

/** axios's shape for a request that reached no server at all. */
const unreachable = () =>
  Object.assign(new Error("Network Error"), { request: {}, response: undefined });

const answered = (status) =>
  Object.assign(new Error("Request failed"), { response: { status, data: {} } });

function mountBilling() {
  render(
    <MemoryRouter>
      <ThemeProvider>
        <OrganizationBilling />
      </ThemeProvider>
    </MemoryRouter>
  );
}

const failOverviewWith = (err) => {
  api.get.mockImplementation((url) =>
    url === "/organization/overview" ? Promise.reject(err) : Promise.resolve({ data: [] }));
};

beforeEach(() => {
  vi.clearAllMocks();
  window.history.replaceState({}, "", "/organization/billing");
  api.post.mockResolvedValue({ data: {} });
});

describe("the dev API base", () => {
  it("points at the port this project actually serves the API on", () => {
    // Guards the regression directly: a base on any other port reaches no server (or another
    // project's), so every call fails and looks exactly like the API being down. The local API
    // runs on 8001 (server/run-dev.ps1); VITE_API_URL in the repo-root .env must name it too.
    expect(API_BASE).toMatch(/:8001(\/|$)/);
  });
});

describe("when the API cannot be reached", () => {
  beforeEach(() => failOverviewWith(unreachable()));

  it("says the API is unreachable rather than telling the reader to refresh", async () => {
    mountBilling();
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(/unreachable/i);
    expect(alert.textContent).not.toMatch(/try refreshing the page/i);
  });

  it("names the setting that actually fixes it", async () => {
    mountBilling();
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(/VITE_API_URL/);
  });

  it("offers a retry that re-requests instead of needing a page reload", async () => {
    mountBilling();
    await screen.findByRole("alert");
    const before = api.get.mock.calls.filter(([u]) => u === "/organization/overview").length;

    await userEvent.click(screen.getByRole("button", { name: /retry/i }));
    await waitFor(() => {
      const after = api.get.mock.calls.filter(([u]) => u === "/organization/overview").length;
      expect(after).toBeGreaterThan(before);
    });
  });

  it("recovers when the API comes back", async () => {
    mountBilling();
    await screen.findByRole("alert");

    api.get.mockImplementation((url) => {
      if (url === "/organization/overview") {
        return Promise.resolve({
          data: {
            organization: { name: "Northwind" },
            entitlements: {
              plan: "Developer", plan_slug: "developer", status: "active",
              checkout_locked: true, items: [],
            },
          },
        });
      }
      return Promise.resolve({ data: [] });
    });
    await userEvent.click(screen.getByRole("button", { name: /retry/i }));

    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
  });
});

describe("a failure the server did answer", () => {
  it("reports an expired session as an authorisation problem", async () => {
    failOverviewWith(answered(403));
    mountBilling();
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).not.toMatch(/unreachable/i);
  });

  it("reports an older server as a missing endpoint", async () => {
    failOverviewWith(answered(404));
    mountBilling();
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(/older build|doesn.t have/i);
  });

  it("names the status for anything else", async () => {
    failOverviewWith(answered(500));
    mountBilling();
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(/500/);
  });
});
