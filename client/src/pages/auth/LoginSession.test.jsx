// The sign-in page's part in session timeout: it says why the previous session ended (once),
// and "Remember me" decides where the new credential is kept — never how long it lasts.
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api", () => ({
  default: { get: vi.fn(), post: vi.fn() },
  AUTH_EXPIRED_EVENT: "zoiko:auth-expired",
  errMsg: (e, fallback) => e?.message ?? fallback ?? "Something went wrong.",
  errCode: () => null,
}));
vi.mock("../../ui/Toast", () => ({ notify: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }));
vi.mock("../../auth/destination", () => ({
  resolvePostLogin: async () => ({ to: "/home", reason: null }),
}));

import api from "../../api";
import { AuthProvider } from "../../auth/AuthContext";
import { __resetSessionChannelForTests, markSessionEnded } from "../../auth/sessionStore";
import Login from "./Login";

const USER = { id: "u1", full_name: "Ada Admin", email: "ada@example.com", role: "org_admin" };

const renderLogin = () =>
  render(
    <MemoryRouter initialEntries={["/login"]}>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="/home" element={<h1>Home</h1>} />
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  );

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  vi.clearAllMocks();
  api.post.mockImplementation((url) =>
    url === "/auth/login"
      ? Promise.resolve({ data: { access_token: "fresh.jwt", token_type: "bearer", user: USER } })
      : Promise.resolve({ data: {} }));
  api.get.mockImplementation((url) =>
    url === "/auth/me" ? Promise.resolve({ data: USER }) : new Promise(() => {}));
});

afterEach(() => {
  __resetSessionChannelForTests();
  localStorage.clear();
  sessionStorage.clear();
});

describe("why the last session ended", () => {
  it("says the session expired due to inactivity", () => {
    markSessionEnded("idle");
    renderLogin();
    expect(screen.getByTestId("session-ended"))
      .toHaveTextContent("Your session expired due to inactivity. Please sign in again.");
  });

  it("names the maximum-length expiry separately", () => {
    markSessionEnded("absolute");
    renderLogin();
    expect(screen.getByTestId("session-ended"))
      .toHaveTextContent("Your session reached its maximum length. Please sign in again.");
  });

  it("is shown once, never as a database or server error", () => {
    markSessionEnded("idle");
    const first = renderLogin();
    expect(screen.getByTestId("session-ended")).not.toHaveTextContent(/database|schema|unavailable/i);
    first.unmount();
    renderLogin();
    expect(screen.queryByTestId("session-ended")).not.toBeInTheDocument();
  });

  it("is absent on an ordinary visit", () => {
    renderLogin();
    expect(screen.queryByTestId("session-ended")).not.toBeInTheDocument();
  });
});

describe("Remember me", () => {
  async function signIn({ remember }) {
    const user = userEvent.setup();
    renderLogin();
    await user.type(screen.getByLabelText(/work email/i), "ada@example.com");
    await user.type(screen.getByLabelText(/^password/i), "correct-horse-battery");
    const box = screen.getByRole("checkbox", { name: /remember me/i });
    if (box.checked !== remember) await user.click(box);
    await user.click(screen.getByRole("button", { name: /^sign in$/i }));
    expect(await screen.findByRole("heading", { name: "Home" })).toBeInTheDocument();
    return api.post.mock.calls.find(([url]) => url === "/auth/login")[1];
  }

  it("on: asks the server to remember, and keeps the credential across restarts", async () => {
    const body = await signIn({ remember: true });
    expect(body.remember).toBe(true);
    expect(localStorage.getItem("token")).toBe("fresh.jwt");
    expect(sessionStorage.getItem("token")).toBeNull();
  });

  it("off: the credential lives only for this browser session", async () => {
    const body = await signIn({ remember: false });
    expect(body.remember).toBe(false);
    await waitFor(() => expect(sessionStorage.getItem("token")).toBe("fresh.jwt"));
    expect(localStorage.getItem("token")).toBeNull();
  });
});
