// The recovery code entry, after the 4-vs-6 digit mismatch.
//
// The bug was not cosmetic: the backend mails a SIX digit code, and this page asked for
// four and capped the input at four, so the code in the user's inbox could not physically
// be typed in. The assertions below are written against the real emailed code from the
// report — 071487 — because its leading zero is the second half of the same bug: the code
// is hashed as a STRING server-side, so any numeric parsing turns it into 71487 and it
// never matches.
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api", () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  errMsg: (e, fallback) => e?.response?.data?.detail ?? fallback ?? "Something went wrong.",
}));

vi.mock("../../ui/Toast", () => ({
  notify: { error: vi.fn(), success: vi.fn(), alert: vi.fn() },
}));

import api from "../../api";
import { notify } from "../../ui/Toast";
import { ThemeProvider } from "../../theme/ThemeContext";
import ForgotPassword from "./ForgotPassword";

const EMAIL = "sangepunaveeen@gmail.com";
// The code from the real recovery email in the bug report. Leading zero on purpose.
const CODE = "071487";
const NEW_PASSWORD = "BrandNewPassword456!";

// What the server actually answers: policy, not account state.
const POLICY = {
  message: "If that email exists, a verification code has been sent.",
  expires_in_minutes: 15,
  code_length: 6,
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.post).mockResolvedValue({ data: POLICY });
});

const renderPage = () =>
  render(
    <ThemeProvider>
      <MemoryRouter>
        <ForgotPassword />
      </MemoryRouter>
    </ThemeProvider>
  );

// Walk the email step so the code entry is on screen.
async function reachCodeStep(user, { data = POLICY } = {}) {
  vi.mocked(api.post).mockResolvedValueOnce({ data });
  renderPage();
  await user.type(screen.getByLabelText(/work email/i), EMAIL);
  await user.click(screen.getByRole("button", { name: /send|reset/i }));
  return screen.findByLabelText(/digit code/i);
}

describe("the code field matches what the backend actually mails", () => {
  it("asks for a 6-digit code, not 4", async () => {
    const user = userEvent.setup();
    const field = await reachCodeStep(user);

    expect(screen.getByLabelText(/^6-digit code$/i)).toBeInTheDocument();
    expect(screen.queryByLabelText(/4-digit code/i)).not.toBeInTheDocument();
    expect(field).toHaveAttribute("placeholder", "000000");
    // No DOM maxLength on purpose — see the paste case below. The cap is enforced in the
    // change handler, which counts DIGITS rather than raw characters.
    expect(field).not.toHaveAttribute("maxLength");
    expect(screen.getByText(/we sent a 6-digit code/i)).toBeInTheDocument();
  });

  it("states the real 15-minute TTL, not the stale 10", async () => {
    const user = userEvent.setup();
    await reachCodeStep(user);
    expect(screen.getByText(/expires in 15 minutes/i)).toBeInTheDocument();
    expect(screen.queryByText(/expires in 10 minutes/i)).not.toBeInTheDocument();
  });

  it("follows the server when it reports a different policy", async () => {
    // The point of reading it off the response: the copy cannot drift from the backend
    // again the way "10 minutes" drifted from RECOVERY_TTL_MINUTES = 15.
    const user = userEvent.setup();
    await reachCodeStep(user, { data: { ...POLICY, expires_in_minutes: 20 } });
    expect(screen.getByText(/expires in 20 minutes/i)).toBeInTheDocument();
  });

  it("falls back to the current backend policy when the field is absent", async () => {
    // An older server that predates the field must not leave an unusable input.
    const user = userEvent.setup();
    const field = await reachCodeStep(user, { data: { message: "ok" } });
    await user.type(field, "0714879");
    expect(field).toHaveValue("071487");
    expect(screen.getByText(/expires in 15 minutes/i)).toBeInTheDocument();
  });
});

describe("the code is handled as a string", () => {
  it("accepts all six digits and keeps the leading zero", async () => {
    const user = userEvent.setup();
    const field = await reachCodeStep(user);
    await user.type(field, CODE);
    // Not "71487": a numeric round-trip here is exactly what would break verification.
    expect(field).toHaveValue(CODE);
  });

  it("sends the code verbatim to the reset endpoint", async () => {
    const user = userEvent.setup();
    const field = await reachCodeStep(user);
    await user.type(field, CODE);
    await user.type(screen.getByLabelText(/^new password$/i), NEW_PASSWORD);
    await user.type(screen.getByLabelText(/confirm new password/i), NEW_PASSWORD);
    await user.click(screen.getByRole("button", { name: /reset password/i }));

    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(2));
    const [url, body] = vi.mocked(api.post).mock.calls[1];
    expect(url).toBe("/auth/reset-password");
    expect(body.otp).toBe(CODE);
    // A string, and the same one the server hashed. Both halves matter.
    expect(typeof body.otp).toBe("string");
    expect(body.email).toBe(EMAIL);
    expect(body.password).toBe(NEW_PASSWORD);
  });

  it("rejects letters and symbols rather than sending them", async () => {
    const user = userEvent.setup();
    const field = await reachCodeStep(user);
    await user.type(field, "0a7!1-48 7x");
    expect(field).toHaveValue(CODE);
  });

  it("refuses to take a seventh digit", async () => {
    const user = userEvent.setup();
    const field = await reachCodeStep(user);
    await user.type(field, "0714879");
    expect(field).toHaveValue(CODE);
  });

  it("strips whitespace from a pasted code without eating digits", async () => {
    // Regression: with a DOM maxLength={6} the browser truncated the raw paste to
    // " 071 4" first, and this field ended up holding "0714" — a code the server would
    // reject, for a code the user had copied correctly.
    const user = userEvent.setup();
    const field = await reachCodeStep(user);
    await user.click(field);
    await user.paste(" 071 487 ");
    expect(field).toHaveValue(CODE);
  });
});

describe("validation stops a submit the server would only reject", () => {
  const fillPasswords = async (user) => {
    await user.type(screen.getByLabelText(/^new password$/i), NEW_PASSWORD);
    await user.type(screen.getByLabelText(/confirm new password/i), NEW_PASSWORD);
  };

  it("names the empty case", async () => {
    const user = userEvent.setup();
    await reachCodeStep(user);
    await fillPasswords(user);
    await user.click(screen.getByRole("button", { name: /reset password/i }));

    expect(await screen.findByText(/enter the verification code\./i)).toBeInTheDocument();
    expect(api.post).toHaveBeenCalledTimes(1); // the request step only
  });

  it("names the short case", async () => {
    const user = userEvent.setup();
    const field = await reachCodeStep(user);
    await user.type(field, "0714");
    await fillPasswords(user);
    await user.click(screen.getByRole("button", { name: /reset password/i }));

    expect(await screen.findByText(/enter the 6-digit code\./i)).toBeInTheDocument();
    expect(api.post).toHaveBeenCalledTimes(1);
  });

  it("submits once the code is complete", async () => {
    const user = userEvent.setup();
    const field = await reachCodeStep(user);
    await user.type(field, CODE);
    await fillPasswords(user);
    await user.click(screen.getByRole("button", { name: /reset password/i }));
    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(2));
  });
});

describe("what the server says about the code reaches the user", () => {
  it("surfaces a rejected code instead of claiming success", async () => {
    const user = userEvent.setup();
    const field = await reachCodeStep(user);
    vi.mocked(api.post).mockRejectedValueOnce({
      response: { status: 400, data: { detail: "Invalid or expired code" } },
    });
    await user.type(field, CODE);
    await user.type(screen.getByLabelText(/^new password$/i), NEW_PASSWORD);
    await user.type(screen.getByLabelText(/confirm new password/i), NEW_PASSWORD);
    await user.click(screen.getByRole("button", { name: /reset password/i }));

    await waitFor(() => expect(notify.error).toHaveBeenCalled());
    expect(vi.mocked(notify.error).mock.calls[0][0]).toMatch(/invalid or expired code/i);
    // Still on the code step, with the code intact, so a retry is not from scratch.
    expect(screen.getByLabelText(/6-digit code/i)).toHaveValue(CODE);
  });

  it("surfaces the lockout rather than a generic failure", async () => {
    const user = userEvent.setup();
    const field = await reachCodeStep(user);
    vi.mocked(api.post).mockRejectedValueOnce({
      response: {
        status: 429,
        data: { detail: "Too many incorrect codes. Recovery is locked; request a new code later." },
      },
    });
    await user.type(field, CODE);
    await user.type(screen.getByLabelText(/^new password$/i), NEW_PASSWORD);
    await user.type(screen.getByLabelText(/confirm new password/i), NEW_PASSWORD);
    await user.click(screen.getByRole("button", { name: /reset password/i }));

    await waitFor(() => expect(notify.error).toHaveBeenCalled());
    expect(vi.mocked(notify.error).mock.calls[0][0]).toMatch(/too many incorrect codes/i);
  });
});
