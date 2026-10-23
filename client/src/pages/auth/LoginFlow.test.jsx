// Signing in, end to end through the real AuthContext, the real error wording (api.errMsg)
// and the real toaster — only the HTTP calls are stubbed.
//
// What prompted it: a dev client whose API address was answered by ANOTHER local project
// (port 8000 taken), so every sign-in was CORS-blocked — which the browser reports to the page
// exactly like a dropped connection — and Enter-then-click stacked two identical red toasts.
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import toast from "react-hot-toast";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

// Typing a full email and password with user-event is slow under a busy test run; these are
// flows, not unit checks, so they get room to finish rather than flaking at the 5 s default.
vi.setConfig({ testTimeout: 30_000 });

vi.mock("../../api", async (importOriginal) => {
  const real = await importOriginal();
  return { ...real, default: { get: vi.fn(), post: vi.fn() } };
});

import api from "../../api";
import { AuthProvider } from "../../auth/AuthContext";
import { __resetSessionChannelForTests, readToken } from "../../auth/sessionStore";
import AppToaster from "../../ui/AppToaster";
import Login from "./Login";

const ADMIN = { id: "u1", full_name: "Ada Admin", email: "ada@example.com", role: "org_admin", org_id: "o1" };
const STAFF = { id: "u2", full_name: "Sam Staff", email: "sam@zoikostream.com", role: "super_admin", org_id: null };

beforeAll(() => {
  window.matchMedia ??= () => ({ matches: false, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} });
});

const signedIn = (user) => ({ data: { access_token: `jwt-for-${user.id}`, token_type: "bearer", user } });
const answered = (status, detail) => Object.assign(new Error(`Request failed with status code ${status}`), {
  response: { status, data: { detail } }, request: {},
});
// What axios reports for a request the browser blocked (CORS) or could not deliver: no response.
const unreachable = () => Object.assign(new Error("Network Error"), { code: "ERR_NETWORK", request: {} });

let me = null;
beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  vi.clearAllMocks();
  me = ADMIN;
  vi.mocked(api.get).mockImplementation((url) => {
    if (url !== "/auth/me") return new Promise(() => {});
    if (!readToken()) return Promise.reject(answered(401, "Not authenticated"));
    return me instanceof Error ? Promise.reject(me) : Promise.resolve({ data: me });
  });
  vi.mocked(api.post).mockResolvedValue(signedIn(ADMIN));
});

afterEach(() => {
  act(() => toast.remove());
  __resetSessionChannelForTests();
  localStorage.clear();
  sessionStorage.clear();
});

const renderLogin = () =>
  render(
    <MemoryRouter initialEntries={["/login"]}>
      <AppToaster />
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="/organization/dashboard" element={<h1>Organization dashboard</h1>} />
          <Route path="/admin/dashboard" element={<h1>Super Admin dashboard</h1>} />
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  );

async function fillAndSubmit(user, { email = "ada@example.com", password = "correct-horse" } = {}) {
  await user.type(screen.getByLabelText(/work email/i), email);
  await user.type(screen.getByLabelText(/^password$/i), password);
  await user.click(screen.getByRole("button", { name: /sign in/i }));
}

const toastsWith = (text) => screen.queryAllByText(text);

describe("a valid sign-in", () => {
  it("authenticates, keeps the credential, confirms it with /auth/me and opens the organization dashboard", async () => {
    const user = userEvent.setup();
    renderLogin();
    await fillAndSubmit(user);
    expect(await screen.findByRole("heading", { name: "Organization dashboard" })).toBeInTheDocument();
    expect(api.post).toHaveBeenCalledWith("/auth/login",
      { identifier: "ada@example.com", password: "correct-horse", remember: true });
    expect(readToken()).toBe("jwt-for-u1");
    expect(api.get).toHaveBeenCalledWith("/auth/me");
    expect(await screen.findByText("Welcome back, Ada Admin!")).toBeInTheDocument();
  });

  it("sends each role to its own home — the server's role, not the form's", async () => {
    vi.mocked(api.post).mockResolvedValue(signedIn(STAFF));
    me = STAFF;
    const user = userEvent.setup();
    renderLogin();
    await fillAndSubmit(user, { email: "sam@zoikostream.com" });
    expect(await screen.findByRole("heading", { name: "Super Admin dashboard" })).toBeInTheDocument();
  });
});

describe("a sign-in that fails", () => {
  it("wrong credentials: the server's own words, once, and no session", async () => {
    vi.mocked(api.post).mockRejectedValue(answered(401, "Incorrect email or password"));
    const user = userEvent.setup();
    renderLogin();
    await fillAndSubmit(user, { password: "wrong" });
    expect(await screen.findByText("Incorrect email or password")).toBeInTheDocument();
    expect(toastsWith("Incorrect email or password")).toHaveLength(1);
    expect(readToken()).toBeNull();
    expect(screen.getByRole("button", { name: /sign in/i })).toBeEnabled();
    expect(screen.queryByRole("heading", { name: /dashboard/i })).toBeNull();
  });

  it("an organization that refuses the account (403) is reported as the server says, not as a bad password", async () => {
    vi.mocked(api.post).mockRejectedValue(answered(403, "This account has been deactivated. Contact your organization administrator."));
    const user = userEvent.setup();
    renderLogin();
    await fillAndSubmit(user);
    expect(await screen.findByText(/This account has been deactivated/)).toBeInTheDocument();
    expect(screen.queryByText(/incorrect email or password/i)).toBeNull();
    expect(readToken()).toBeNull();
  });

  it("an unreachable or CORS-blocked API is a connection error that names the API address (development)", async () => {
    vi.mocked(api.post).mockRejectedValue(unreachable());
    const user = userEvent.setup();
    renderLogin();
    await fillAndSubmit(user);
    const msg = await screen.findByText(/Couldn't reach the ZoikoStream server, so nothing was submitted/);
    expect(msg.textContent).toMatch(/the request went to https?:\/\/\S+/);
    expect(msg.textContent).not.toMatch(/incorrect|invalid|password/i);
  });

  it("a server error (5xx) is never reported as invalid credentials", async () => {
    vi.mocked(api.post).mockRejectedValue(answered(500, "The server hit an unexpected error. Nothing was changed."));
    const user = userEvent.setup();
    renderLogin();
    await fillAndSubmit(user);
    expect(await screen.findByText("The server hit an unexpected error. Nothing was changed.")).toBeInTheDocument();
    expect(screen.queryByText(/incorrect email or password|couldn't reach/i)).toBeNull();
  });

  it("a response without a usable session (no token, or /auth/me refusing it) never greets or redirects", async () => {
    me = answered(401, "Not authenticated");
    const user = userEvent.setup();
    renderLogin();
    await fillAndSubmit(user);
    expect(await screen.findByText("Signed in, but the session could not be confirmed. Please try again.")).toBeInTheDocument();
    expect(screen.queryByText(/Welcome back, /)).toBeNull();         // no success toast (the page title is "Welcome back")
    expect(screen.queryByRole("heading", { name: /dashboard/i })).toBeNull();
    expect(readToken()).toBeNull();                                  // the refused credential is gone
  });

  it("a session that /auth/me cannot confirm because the server failed is not treated as signed in", async () => {
    me = answered(503, "Temporarily unavailable");
    const user = userEvent.setup();
    renderLogin();
    await fillAndSubmit(user);
    expect(await screen.findByText(/session could not be confirmed/)).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /dashboard/i })).toBeNull();
  });
});

describe("one attempt at a time, one notification per failure", () => {
  it("Enter then a click while the first request is pending sends ONE request", async () => {
    let settle;
    vi.mocked(api.post).mockImplementation(() => new Promise((resolve) => { settle = resolve; }));
    const user = userEvent.setup();
    renderLogin();
    await user.type(screen.getByLabelText(/work email/i), "ada@example.com");
    const button = screen.getByRole("button", { name: /sign in/i });   // its label changes while busy
    await user.type(screen.getByLabelText(/^password$/i), "correct-horse{Enter}");
    await user.click(button);
    await user.keyboard("{Enter}");
    expect(api.post).toHaveBeenCalledTimes(1);
    expect(button).toBeDisabled();                                  // disabled while in flight
    await act(async () => settle(signedIn(ADMIN)));
    expect(await screen.findByRole("heading", { name: "Organization dashboard" })).toBeInTheDocument();
  });

  it("two submits in the same tick — before React can disable the button — still send ONE request", async () => {
    vi.mocked(api.post).mockImplementation(() => new Promise(() => {}));
    const user = userEvent.setup();
    renderLogin();
    await user.type(screen.getByLabelText(/work email/i), "ada@example.com");
    await user.type(screen.getByLabelText(/^password$/i), "correct-horse");
    const form = screen.getByRole("button", { name: /sign in/i }).closest("form");
    act(() => {
      fireEvent.submit(form);
      fireEvent.submit(form);
      fireEvent.submit(form);
    });
    expect(api.post).toHaveBeenCalledTimes(1);
  });

  it("two failed attempts in a row leave exactly one visible error, not two stacked copies", async () => {
    vi.mocked(api.post).mockRejectedValue(unreachable());
    const user = userEvent.setup();
    renderLogin();
    await fillAndSubmit(user);
    await screen.findByText(/Couldn't reach the ZoikoStream server/);
    await user.click(screen.getByRole("button", { name: /sign in/i }));       // try again
    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.getAllByText(/Couldn't reach the ZoikoStream server/)).toHaveLength(1));
  });

  it("a success after a failure clears the stale error", async () => {
    vi.mocked(api.post).mockRejectedValueOnce(answered(401, "Incorrect email or password"));
    const user = userEvent.setup();
    renderLogin();
    await fillAndSubmit(user, { password: "wrong" });
    await screen.findByText("Incorrect email or password");
    await user.clear(screen.getByLabelText(/^password$/i));
    await user.type(screen.getByLabelText(/^password$/i), "correct-horse");
    await user.click(screen.getByRole("button", { name: /sign in/i }));
    expect(await screen.findByRole("heading", { name: "Organization dashboard" })).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText("Incorrect email or password")).toBeNull());
  });
});
