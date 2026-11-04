// /auth/google/callback: the browser half of finishing a Google sign-in. Real AuthContext,
// real session store, real toaster; only HTTP is stubbed.
//
//   matching state   -> POST /auth/google/callback with code + stored verifier -> session -> home
//   state mismatch   -> refused locally, the API is never called
//   no stored attempt (other tab / expired / replay) -> refused locally
//   ?error=access_denied -> "cancelled", nothing called
//   server refusal   -> the server's own words, no session
import { act, render, screen, waitFor } from "@testing-library/react";
import toast from "react-hot-toast";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

vi.setConfig({ testTimeout: 30_000 });

vi.mock("../../api", async (importOriginal) => {
  const real = await importOriginal();
  return { ...real, default: { get: vi.fn(), post: vi.fn() } };
});

import api from "../../api";
import { AuthProvider } from "../../auth/AuthContext";
import { __resetSessionChannelForTests, readToken } from "../../auth/sessionStore";
import AppToaster from "../../ui/AppToaster";
import GoogleCallback from "./GoogleCallback";
import { peekPendingSignIn } from "./googleAuth";

const ADMIN = { id: "u1", full_name: "Ada Admin", email: "ada@example.com", role: "org_admin", org_id: "o1" };
const VERIFIER = "v".repeat(64);

const answered = (status, detail) =>
  Object.assign(new Error(`Request failed with status code ${status}`), { response: { status, data: { detail } }, request: {} });

beforeAll(() => {
  window.matchMedia ??= () => ({ matches: false, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} });
});

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  vi.clearAllMocks();
  vi.mocked(api.get).mockImplementation((url) => {
    if (url !== "/auth/me") return new Promise(() => {});
    return readToken() ? Promise.resolve({ data: ADMIN }) : Promise.reject(answered(401, "Not authenticated"));
  });
  vi.mocked(api.post).mockResolvedValue({ data: { access_token: "jwt-google", token_type: "bearer", user: ADMIN } });
});

afterEach(() => {
  act(() => toast.remove());
  __resetSessionChannelForTests();
  localStorage.clear();
  sessionStorage.clear();
});

const storeAttempt = (over = {}) =>
  sessionStorage.setItem(
    "zs.google-sign-in",
    JSON.stringify({ state: "st-1", codeVerifier: VERIFIER, remember: false, from: null, startedAt: Date.now(), ...over }),
  );

const renderAt = (search) =>
  render(
    <MemoryRouter initialEntries={[`/auth/google/callback${search}`]}>
      <AppToaster />
      <AuthProvider>
        <Routes>
          <Route path="/auth/google/callback" element={<GoogleCallback />} />
          <Route path="/organization/dashboard" element={<h1>Organization dashboard</h1>} />
          <Route path="/organization/settings" element={<h1>Security settings</h1>} />
          <Route path="/login" element={<h1>Login page</h1>} />
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  );

describe("finishing a Google sign-in", () => {
  it("exchanges the code with the stored verifier and lands on the account's home", async () => {
    storeAttempt({ remember: true });
    renderAt("?code=abc&state=st-1");
    expect(await screen.findByText("Organization dashboard", {}, { timeout: 10_000 })).toBeInTheDocument();
    expect(api.post).toHaveBeenCalledWith("/auth/google/callback", {
      code: "abc", state: "st-1", code_verifier: VERIFIER, remember: true,
    });
    // The code is single-use: exactly one exchange (other POSTs are the session keeper's).
    expect(vi.mocked(api.post).mock.calls.filter(([url]) => url === "/auth/google/callback")).toHaveLength(1);
    expect(readToken()).toBe("jwt-google");
    expect(peekPendingSignIn()).toBeNull();                          // the attempt is consumed
  });

  it("refuses a state this tab did not start, without calling the API", async () => {
    storeAttempt();
    renderAt("?code=abc&state=forged");
    expect(await screen.findByText(/expired or was started in another tab/)).toBeInTheDocument();
    expect(api.post).not.toHaveBeenCalled();
    expect(readToken()).toBeNull();
  });

  it("refuses when no attempt was started here (other tab, replay, or expired)", async () => {
    storeAttempt({ startedAt: Date.now() - 11 * 60 * 1000 });          // older than the server's state
    renderAt("?code=abc&state=st-1");
    expect(await screen.findByText(/expired or was started in another tab/)).toBeInTheDocument();
    expect(api.post).not.toHaveBeenCalled();
    expect(peekPendingSignIn()).toBeNull();
  });

  it("reports a cancelled consent screen without calling the API", async () => {
    storeAttempt();
    renderAt("?error=access_denied&state=st-1");
    expect(await screen.findByText("Google sign-in was cancelled.")).toBeInTheDocument();
    expect(api.post).not.toHaveBeenCalled();
    expect(screen.getByRole("link", { name: "Back to login" })).toHaveAttribute("href", "/login");
  });

  it("shows the server's reason and issues no session when the server refuses", async () => {
    vi.mocked(api.post).mockRejectedValue(answered(403, {
      code: "GOOGLE_NOT_LINKED",
      message: "This Google account isn't linked to a ZoikoStream account.",
    }));
    storeAttempt();
    renderAt("?code=abc&state=st-1");
    expect(await screen.findByText(/isn't linked to a ZoikoStream account/)).toBeInTheDocument();
    await waitFor(() => expect(readToken()).toBeNull());
    expect(screen.queryByRole("heading", { name: /dashboard/i })).toBeNull();
  });
});

describe("finishing a Google LINK (signed-in account, Settings > Security)", () => {
  it("completes the link, never the sign-in endpoint, and returns to security settings", async () => {
    vi.mocked(api.post).mockResolvedValue({ data: { available: true, linked: true, email: "ada@example.com" } });
    storeAttempt({ mode: "link", returnTo: "/organization/settings?tab=security" });
    renderAt("?code=abc&state=st-1");
    expect(await screen.findByText("Security settings", {}, { timeout: 10_000 })).toBeInTheDocument();
    expect(api.post).toHaveBeenCalledWith("/auth/google/link/complete", {
      code: "abc", state: "st-1", code_verifier: VERIFIER,
    });
    expect(vi.mocked(api.post).mock.calls.some(([url]) => url === "/auth/google/callback")).toBe(false);
    expect(readToken()).toBeNull();                                  // linking issues no new session
    expect(peekPendingSignIn()).toBeNull();
  });

  it("explains a refused link and offers the way back to settings", async () => {
    vi.mocked(api.post).mockRejectedValue(answered(409, {
      code: "GOOGLE_EMAIL_MISMATCH",
      message: "That Google account uses a different email address.",
    }));
    storeAttempt({ mode: "link", returnTo: "/organization/settings?tab=security" });
    renderAt("?code=abc&state=st-1");
    expect(await screen.findByText("Google account wasn't linked")).toBeInTheDocument();
    expect(screen.getByText(/uses a different email address/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to security settings" }))
      .toHaveAttribute("href", "/organization/settings?tab=security");
  });

  it("never follows a stored return path off the app", async () => {
    vi.mocked(api.post).mockResolvedValue({ data: { linked: true } });
    storeAttempt({ mode: "link", returnTo: "//evil.example/steal" });
    renderAt("?code=abc&state=st-1");
    expect(await screen.findByText("Security settings", {}, { timeout: 10_000 })).toBeInTheDocument();
  });

  it("refuses a link callback whose state this tab did not start", async () => {
    storeAttempt({ mode: "link" });
    renderAt("?code=abc&state=forged");
    expect(await screen.findByText("Google account wasn't linked")).toBeInTheDocument();
    expect(api.post).not.toHaveBeenCalled();
  });
});
