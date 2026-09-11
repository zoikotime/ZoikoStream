// The in-platform password change.
//
// The properties worth pinning are the ones a plausible refactor would quietly break: the
// request carries the two fields the endpoint accepts and NOTHING that identifies an
// account, the values never outlive the submit, and each failure mode says the one true
// thing about itself rather than collapsing into "something went wrong".
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../../api", () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  errMsg: (e) => e?.message ?? "Something went wrong.",
}));

import api from "../../../api";
import { ThemeProvider } from "../../../theme/ThemeContext";
import ChangePasswordForm from "./ChangePasswordForm";

const OLD = "OldPassword123!";
const NEW = "BrandNewPassword456!";

const rejectWith = (status, detail) =>
  Object.assign(new Error(`Request failed with status code ${status}`), {
    response: { status, data: { detail } },
  });

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.patch).mockResolvedValue({ data: { message: "Password updated successfully" } });
});

const renderForm = (props = {}) =>
  render(
    <ThemeProvider>
      <ChangePasswordForm {...props} />
    </ThemeProvider>
  );

const fields = () => ({
  current: screen.getByLabelText(/current password/i),
  next: screen.getByLabelText(/^new password$/i),
  confirm: screen.getByLabelText(/confirm new password/i),
});

const submitBtn = () => screen.getByRole("button", { name: /update password/i });

async function fillIn(user, { current = OLD, next = NEW, confirm = NEW } = {}) {
  const f = fields();
  if (current) await user.type(f.current, current);
  if (next) await user.type(f.next, next);
  if (confirm) await user.type(f.confirm, confirm);
}

describe("the form", () => {
  it("renders three password inputs, each masked by default", () => {
    renderForm();
    for (const el of Object.values(fields())) expect(el).toHaveAttribute("type", "password");
  });

  it("gives every field its own independent reveal toggle", async () => {
    const user = userEvent.setup();
    renderForm();
    const toggles = screen.getAllByRole("button", { name: /show password/i });
    expect(toggles).toHaveLength(3);

    // Revealing one must not reveal the others — a single shared `show` flag is the easy
    // mistake here, and it puts a password on screen the user never asked to see.
    await user.click(toggles[0]);
    const f = fields();
    expect(f.current).toHaveAttribute("type", "text");
    expect(f.next).toHaveAttribute("type", "password");
    expect(f.confirm).toHaveAttribute("type", "password");
  });

  it("stays disabled until all three fields are filled", async () => {
    const user = userEvent.setup();
    renderForm();
    expect(submitBtn()).toBeDisabled();
    await user.type(fields().current, OLD);
    expect(submitBtn()).toBeDisabled();
    await user.type(fields().next, NEW);
    expect(submitBtn()).toBeDisabled();
    await user.type(fields().confirm, NEW);
    expect(submitBtn()).toBeEnabled();
  });

  it("refuses to submit when the confirmation does not match", async () => {
    const user = userEvent.setup();
    renderForm();
    await fillIn(user, { confirm: "SomethingElse999!" });
    expect(screen.getByText(/passwords do not match/i)).toBeInTheDocument();
    expect(submitBtn()).toBeDisabled();
    expect(api.patch).not.toHaveBeenCalled();
  });

  it("shows the organization minimum as a hint when it is known, and omits it otherwise", () => {
    const { unmount } = renderForm({ minLength: 12 });
    expect(screen.getByText(/at least 12 characters/i)).toBeInTheDocument();
    unmount();

    renderForm();
    expect(screen.queryByText(/at least .* characters/i)).not.toBeInTheDocument();
  });
});

describe("the request", () => {
  it("PATCHes /auth/password with exactly the two fields the endpoint accepts", async () => {
    const user = userEvent.setup();
    renderForm();
    await fillIn(user);
    await user.click(submitBtn());

    await waitFor(() => expect(api.patch).toHaveBeenCalledTimes(1));
    const [url, body] = vi.mocked(api.patch).mock.calls[0];
    expect(url).toBe("/auth/password");
    // No email, no user id, no org id: the account comes from the bearer token, so the
    // form cannot be aimed at another account even by a caller editing the payload.
    expect(Object.keys(body).sort()).toEqual(["current_password", "new_password"]);
    expect(body.current_password).toBe(OLD);
    expect(body.new_password).toBe(NEW);
  });

  it("confirms success and empties every field", async () => {
    const user = userEvent.setup();
    renderForm();
    await fillIn(user);
    await user.click(submitBtn());

    expect(await screen.findByRole("status")).toHaveTextContent(/password updated/i);
    const f = fields();
    // Nothing is left holding either password on a screen someone may walk away from.
    expect(f.current).toHaveValue("");
    expect(f.next).toHaveValue("");
    expect(f.confirm).toHaveValue("");
    expect(submitBtn()).toBeDisabled();
  });

  it("writes nothing to browser storage", async () => {
    const user = userEvent.setup();
    const setLocal = vi.spyOn(Storage.prototype, "setItem");
    renderForm();
    await fillIn(user);
    await user.click(submitBtn());
    await screen.findByRole("status");

    for (const [key, value] of setLocal.mock.calls) {
      expect(String(value)).not.toContain(OLD);
      expect(String(value)).not.toContain(NEW);
      expect(String(key)).not.toMatch(/password/i);
    }
    setLocal.mockRestore();
  });
});

describe("refusals say the true thing", () => {
  it("400 blames the current password, on that field", async () => {
    vi.mocked(api.patch).mockRejectedValue(rejectWith(400, "Current password is incorrect"));
    const user = userEvent.setup();
    renderForm();
    await fillIn(user);
    await user.click(submitBtn());

    expect(await screen.findByText(/current password is incorrect/i)).toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("422 with a written reason shows the server's own policy message", async () => {
    // This is org_policy.password_violation talking. Restating it in the client would be
    // the second policy this component deliberately does not have.
    vi.mocked(api.patch).mockRejectedValue(
      rejectWith(422, "Password must be at least 12 characters for this Organization.")
    );
    const user = userEvent.setup();
    renderForm();
    await fillIn(user);
    await user.click(submitBtn());

    expect(await screen.findByText(/at least 12 characters for this organization/i)).toBeInTheDocument();
  });

  it("422 from schema validation is not shown raw", async () => {
    // Pydantic sends a list. "String should have at least 8 characters" is not a sentence
    // to put in front of a person.
    vi.mocked(api.patch).mockRejectedValue(
      rejectWith(422, [{ loc: ["body", "new_password"], msg: "String should have at least 8 characters" }])
    );
    const user = userEvent.setup();
    renderForm();
    await fillIn(user);
    await user.click(submitBtn());

    expect(await screen.findByText(/does not meet security requirements/i)).toBeInTheDocument();
    expect(screen.queryByText(/String should have at least/i)).not.toBeInTheDocument();
  });

  it("429 surfaces the rate limit rather than a generic failure", async () => {
    vi.mocked(api.patch).mockRejectedValue(
      rejectWith(429, "Too many attempts. Please wait a moment and try again.")
    );
    const user = userEvent.setup();
    renderForm();
    await fillIn(user);
    await user.click(submitBtn());

    expect(await screen.findByRole("alert")).toHaveTextContent(/too many attempts/i);
  });

  it("anything else falls back to one honest generic message", async () => {
    vi.mocked(api.patch).mockRejectedValue(rejectWith(500, "Internal Server Error"));
    const user = userEvent.setup();
    renderForm();
    await fillIn(user);
    await user.click(submitBtn());

    expect(await screen.findByRole("alert")).toHaveTextContent(/unable to update password/i);
  });

  it("keeps what was typed after a failure, so the retry is not from scratch", async () => {
    vi.mocked(api.patch).mockRejectedValue(rejectWith(400, "Current password is incorrect"));
    const user = userEvent.setup();
    renderForm();
    await fillIn(user);
    await user.click(submitBtn());
    await screen.findByText(/current password is incorrect/i);

    expect(fields().next).toHaveValue(NEW);
    expect(submitBtn()).toBeEnabled();
  });

  it("retires the previous outcome as soon as typing resumes", async () => {
    const user = userEvent.setup();
    renderForm();
    await fillIn(user);
    await user.click(submitBtn());
    await screen.findByRole("status");

    await user.type(fields().current, "x");
    // A stale "Password updated" above a half-typed retry is a thing people act on.
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });
});
