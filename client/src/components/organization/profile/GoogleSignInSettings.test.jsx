// Settings > Security > Google sign-in: linking needs a password step-up and goes to Google;
// unlinking is confirmed inline; availability comes only from the server.
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../../api", async (importOriginal) => {
  const real = await importOriginal();
  return { ...real, default: { get: vi.fn(), post: vi.fn(), delete: vi.fn() } };
});
vi.mock("../../../ui/Toast", () => ({
  notify: { error: vi.fn(), success: vi.fn(), alert: vi.fn(), warning: vi.fn(), info: vi.fn() },
}));

import api from "../../../api";
import { notify } from "../../../ui/Toast";
import * as googleAuth from "../../../pages/auth/googleAuth";
import GoogleSignInSettings from "./GoogleSignInSettings";

const STEP_UP_REQUIRED = Object.assign(new Error("Request failed with status code 403"), {
  response: { status: 403, data: { detail: { code: "STEP_UP_REQUIRED", purpose: "google_link", message: "Re-verify your password to continue." } } },
});
const GOOGLE_URL = "https://accounts.google.com/o/oauth2/v2/auth?x=1";

let redirect;
beforeEach(() => {
  vi.clearAllMocks();
  sessionStorage.clear();
  redirect = vi.spyOn(googleAuth.browser, "redirect").mockImplementation(() => {});
});
afterEach(() => {
  redirect.mockRestore();
  sessionStorage.clear();
});

const unlinked = () => vi.mocked(api.get).mockResolvedValue({ data: { available: true, linked: false } });

const serverWantsStepUp = () =>
  vi.mocked(api.post).mockImplementation((url, body, config) => {
    if (url === "/auth/google/link/start") {
      return config?.headers?.["X-Step-Up"] === "ref-1"
        ? Promise.resolve({ data: { authorization_url: GOOGLE_URL, state: "st-link", code_verifier: "v".repeat(64) } })
        : Promise.reject(STEP_UP_REQUIRED);
    }
    if (url === "/auth/step-up") {
      return Promise.resolve({ data: { reference: "ref-1", purpose: body.purpose, expires_in_minutes: 5 } });
    }
    return Promise.reject(new Error(`unexpected ${url}`));
  });

describe("linking a Google account", () => {
  it("asks for the password, retries with the step-up reference, then goes to Google", async () => {
    unlinked();
    serverWantsStepUp();
    render(<GoogleSignInSettings />);
    fireEvent.click(await screen.findByRole("button", { name: /Link Google account/ }));

    // The server's STEP_UP_REQUIRED becomes a password prompt for purpose google_link.
    fireEvent.change(await screen.findByLabelText("Your password"), { target: { value: "s3cret" } });
    fireEvent.click(screen.getByRole("button", { name: "Confirm and continue" }));

    await waitFor(() => expect(redirect).toHaveBeenCalledWith(GOOGLE_URL));
    expect(api.post).toHaveBeenCalledWith("/auth/step-up", { password: "s3cret", purpose: "google_link" });
    expect(api.post).toHaveBeenCalledWith("/auth/google/link/start", null, { headers: { "X-Step-Up": "ref-1" } });
    expect(googleAuth.peekPendingSignIn()).toMatchObject({
      mode: "link", state: "st-link", codeVerifier: "v".repeat(64), returnTo: "/organization/settings?tab=security",
    });
  });

  it("does nothing when the password prompt is cancelled", async () => {
    unlinked();
    serverWantsStepUp();
    render(<GoogleSignInSettings />);
    fireEvent.click(await screen.findByRole("button", { name: /Link Google account/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Cancel" }));

    await waitFor(() => expect(screen.getByRole("button", { name: /Link Google account/ })).toBeEnabled());
    expect(redirect).not.toHaveBeenCalled();
    expect(googleAuth.peekPendingSignIn()).toBeNull();
    expect(api.post).not.toHaveBeenCalledWith("/auth/step-up", expect.anything());
  });

  it("shows the server's reason when linking cannot start", async () => {
    unlinked();
    vi.mocked(api.post).mockRejectedValue(Object.assign(new Error("409"), {
      response: { status: 409, data: { detail: { code: "GOOGLE_ALREADY_LINKED", message: "A Google account is already linked." } } },
    }));
    render(<GoogleSignInSettings />);
    fireEvent.click(await screen.findByRole("button", { name: /Link Google account/ }));
    await waitFor(() => expect(notify.error).toHaveBeenCalledWith("A Google account is already linked."));
    expect(redirect).not.toHaveBeenCalled();
  });
});

describe("a linked Google account", () => {
  it("shows the linked address and unlinks after an inline confirmation", async () => {
    vi.mocked(api.get).mockResolvedValue({
      data: { available: true, linked: true, email: "ada@acme.example", linked_at: "2026-11-01T10:00:00Z", last_used_at: null },
    });
    vi.mocked(api.delete).mockResolvedValue({ data: { available: true, linked: false } });
    render(<GoogleSignInSettings />);

    expect(await screen.findByText("Linked to ada@acme.example")).toBeInTheDocument();
    expect(screen.getByText(/Not used to sign in yet/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Unlink" }));
    expect(api.delete).not.toHaveBeenCalled();                     // confirmation first
    fireEvent.click(screen.getByRole("button", { name: "Remove" }));

    await waitFor(() => expect(api.delete).toHaveBeenCalledWith("/auth/google/identity"));
    expect(await screen.findByRole("button", { name: /Link Google account/ })).toBeInTheDocument();
    expect(notify.success).toHaveBeenCalledWith("Google sign-in removed from your account.");
  });
});

describe("availability comes from the server", () => {
  it("offers no link when Google sign-in is unavailable for this account", async () => {
    vi.mocked(api.get).mockResolvedValue({ data: { available: false, linked: false } });
    render(<GoogleSignInSettings />);
    expect(await screen.findByText(/isn't available for this account/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Link Google account/ })).toBeNull();
  });

  it("degrades quietly when the status cannot be loaded", async () => {
    vi.mocked(api.get).mockRejectedValue(new Error("404"));
    render(<GoogleSignInSettings />);
    expect(await screen.findByText(/status isn't available right now/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Link Google account/ })).toBeNull();
  });
});
