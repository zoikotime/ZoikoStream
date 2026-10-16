// Members page: promoting someone to Admin needs the acting admin's password, because the
// server says so (403 STEP_UP_REQUIRED). The client reacts to that answer; it does not carry
// its own copy of the rule, and it never claims success the server has not confirmed.
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api", async () => {
  const actual = await vi.importActual("../../api");
  return {
    ...actual,
    default: {
      get: vi.fn(),
      post: vi.fn(),
      patch: vi.fn(),
      delete: vi.fn(),
      interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
    },
  };
});
vi.mock("../../ui/Toast", () => ({
  notify: { error: vi.fn(), success: vi.fn(), warning: vi.fn(), info: vi.fn(), alert: vi.fn() },
}));
vi.mock("../../auth/AuthContext", () => ({
  useAuth: () => ({
    user: { id: "u1", full_name: "Radha Admin", email: "radha@zoikogroup.com", role: "org_admin" },
    logout: vi.fn(),
  }),
}));

import api from "../../api";
import { notify } from "../../ui/Toast";
import InviteMembers from "./InviteMembers";

const USERS = "/organization/users";
const SAM = `${USERS}/u2`;
let server;

const STEP_UP_REQUIRED = {
  response: {
    status: 403,
    data: { detail: { code: "STEP_UP_REQUIRED", purpose: "high_risk_role_grant", message: "Re-verify your password to continue." } },
  },
};
const SPENT = {
  response: {
    status: 403,
    data: { detail: { code: "STEP_UP_REQUIRED", purpose: "high_risk_role_grant", message: "That verification has already been used." } },
  },
};

beforeEach(() => {
  vi.clearAllMocks();
  server = [
    { id: "u1", full_name: "Radha Admin", email: "radha@zoikogroup.com", role: "org_admin", is_active: true, created_at: "2026-09-23T00:00:00Z" },
    { id: "u2", full_name: "Sam Host", email: "sam@example.com", role: "host", is_active: true, created_at: "2026-09-24T00:00:00Z" },
  ];
  api.get.mockImplementation((url) => {
    if (url === USERS) return Promise.resolve({ data: { items: server.map((m) => ({ ...m })), total: server.length } });
    if (url === "/organization/invitations") return Promise.resolve({ data: { items: [], total: 0 } });
    return Promise.reject(new Error(`Unhandled URL: ${url}`));
  });
  // The real rule, server side: promotion to admin needs a valid X-Step-Up reference.
  api.patch.mockImplementation((url, body, config) => {
    if (body.role === "org_admin" && config?.headers?.["X-Step-Up"] !== "ref-1") return Promise.reject(STEP_UP_REQUIRED);
    const m = server.find((x) => url.endsWith(x.id));
    m.role = body.role;
    return Promise.resolve({ data: { ...m } });
  });
});

async function renderPage() {
  render(<MemoryRouter><InviteMembers /></MemoryRouter>);
  await screen.findByText("Sam Host");
}
const samSelect = () => screen.getByLabelText("Role for Sam Host");
const promoteSam = () => fireEvent.change(samSelect(), { target: { value: "org_admin" } });
const stepUpDialog = () => screen.findByRole("dialog", { name: "Confirm admin access" });
const typePassword = (dlg, value) => fireEvent.change(within(dlg).getByLabelText("Your password"), { target: { value } });
const submit = (dlg) => fireEvent.click(within(dlg).getByRole("button", { name: "Grant Admin access" }));

describe("promotion to Admin asks for the acting admin's password", () => {
  it("the server's STEP_UP_REQUIRED opens the dialog, and nothing is claimed yet", async () => {
    await renderPage();
    promoteSam();
    const dlg = await stepUpDialog();
    expect(dlg).toHaveAccessibleDescription(/Granting Sam Host Admin access gives them full control of this organization/);
    expect(within(dlg).getByLabelText("Your password")).toHaveFocus();
    expect(api.patch).toHaveBeenCalledTimes(1);
    expect(api.patch).toHaveBeenCalledWith(SAM, { role: "org_admin" });
    expect(api.post).not.toHaveBeenCalled();
    expect(notify.success).not.toHaveBeenCalled();
    expect(notify.error).not.toHaveBeenCalled();
    expect(samSelect()).toBeDisabled();           // locked while the dialog is up
  });

  it("a valid password: step-up POST, then the same PATCH once with X-Step-Up, then success", async () => {
    api.post.mockResolvedValueOnce({ data: { reference: "ref-1", purpose: "high_risk_role_grant", expires_in_minutes: 5 } });
    await renderPage();
    promoteSam();
    const dlg = await stepUpDialog();
    typePassword(dlg, "correct horse");
    submit(dlg);

    await waitFor(() => expect(notify.success).toHaveBeenCalledWith("Sam Host is now Admin", { id: "member-role:u2" }));
    expect(api.post).toHaveBeenCalledTimes(1);
    expect(api.post).toHaveBeenCalledWith("/auth/step-up", { password: "correct horse", purpose: "high_risk_role_grant" });
    expect(api.patch).toHaveBeenCalledTimes(2);
    expect(api.patch).toHaveBeenLastCalledWith(SAM, { role: "org_admin" }, { headers: { "X-Step-Up": "ref-1" } });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await waitFor(() => expect(samSelect()).not.toBeDisabled());
    expect(samSelect().value).toBe("org_admin");
    expect(notify.error).not.toHaveBeenCalled();
  });

  it("a wrong password: inline error in the dialog, the PATCH is not retried", async () => {
    api.post.mockRejectedValueOnce({ response: { status: 401, data: { detail: "Password re-verification failed." } } });
    await renderPage();
    promoteSam();
    const dlg = await stepUpDialog();
    typePassword(dlg, "nope");
    submit(dlg);

    const alert = await within(dlg).findByRole("alert");
    expect(alert).toHaveTextContent("That password is incorrect. Try again.");
    expect(within(dlg).getByLabelText("Your password")).toHaveAttribute("aria-invalid", "true");
    expect(within(dlg).getByLabelText("Your password")).toHaveValue("");
    expect(api.patch).toHaveBeenCalledTimes(1);   // only the original attempt
    expect(notify.success).not.toHaveBeenCalled();
    expect(notify.error).not.toHaveBeenCalled();  // the dialog says it, not a toast
    expect(samSelect()).toBeDisabled();
  });

  it("too many attempts (429): an inline 'too many attempts' message", async () => {
    api.post.mockRejectedValueOnce({ response: { status: 429, data: { detail: "Too Many Requests" } } });
    await renderPage();
    promoteSam();
    const dlg = await stepUpDialog();
    typePassword(dlg, "again");
    submit(dlg);
    expect(await within(dlg).findByRole("alert")).toHaveTextContent(/too many attempts/i);
    expect(api.patch).toHaveBeenCalledTimes(1);
  });

  it("cancel: the role rolls back and nothing more is sent", async () => {
    await renderPage();
    promoteSam();
    const dlg = await stepUpDialog();
    fireEvent.click(within(dlg).getByRole("button", { name: "Cancel" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await waitFor(() => expect(samSelect()).not.toBeDisabled());
    expect(samSelect().value).toBe("host");
    expect(api.patch).toHaveBeenCalledTimes(1);
    expect(api.post).not.toHaveBeenCalled();
    expect(notify.success).not.toHaveBeenCalled();
    expect(notify.error).not.toHaveBeenCalled();
  });

  it("Escape cancels the same way", async () => {
    await renderPage();
    promoteSam();
    await stepUpDialog();
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await waitFor(() => expect(samSelect().value).toBe("host"));
    expect(api.patch).toHaveBeenCalledTimes(1);
    expect(api.post).not.toHaveBeenCalled();
  });

  it("an expired or already-used reference: one clean error toast and a rollback", async () => {
    api.post.mockResolvedValueOnce({ data: { reference: "ref-stale", purpose: "high_risk_role_grant", expires_in_minutes: 5 } });
    api.patch.mockRejectedValueOnce(STEP_UP_REQUIRED).mockRejectedValueOnce(SPENT);
    await renderPage();
    promoteSam();
    const dlg = await stepUpDialog();
    typePassword(dlg, "correct horse");
    submit(dlg);

    await waitFor(() => expect(notify.error).toHaveBeenCalledTimes(1));
    expect(notify.error).toHaveBeenCalledWith(
      "Your password confirmation expired, so Sam Host's role was not changed. Try again.",
      { id: "member-role:u2" }
    );
    expect(api.patch).toHaveBeenCalledTimes(2);   // the one retry, no loop
    expect(api.patch).toHaveBeenLastCalledWith(SAM, { role: "org_admin" }, { headers: { "X-Step-Up": "ref-stale" } });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await waitFor(() => expect(samSelect()).not.toBeDisabled());
    expect(samSelect().value).toBe("host");
    expect(notify.success).not.toHaveBeenCalled();
  });

  it("the retried PATCH failing for another reason is reported as that failure, with a rollback", async () => {
    api.post.mockResolvedValueOnce({ data: { reference: "ref-1" } });
    api.patch
      .mockRejectedValueOnce(STEP_UP_REQUIRED)
      .mockRejectedValueOnce({ response: { status: 503, data: { detail: "Service temporarily unavailable" } } });
    await renderPage();
    promoteSam();
    const dlg = await stepUpDialog();
    typePassword(dlg, "correct horse");
    submit(dlg);
    await waitFor(() => expect(notify.error).toHaveBeenCalledWith("Service temporarily unavailable", { id: "member-role:u2" }));
    await waitFor(() => expect(samSelect().value).toBe("host"));
  });
});

describe("ordinary role changes are untouched", () => {
  it("Host -> Viewer: one plain PATCH, no dialog, no step-up request", async () => {
    await renderPage();
    fireEvent.change(samSelect(), { target: { value: "viewer" } });
    await waitFor(() => expect(notify.success).toHaveBeenCalledWith("Sam Host is now Viewer", { id: "member-role:u2" }));
    expect(api.patch).toHaveBeenCalledTimes(1);
    expect(api.patch.mock.calls[0]).toEqual([SAM, { role: "viewer" }]);
    expect(api.post).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("a plain 403 (not STEP_UP_REQUIRED) is just an error, no dialog", async () => {
    api.patch.mockRejectedValueOnce({ response: { status: 403, data: { detail: "Cannot modify a super admin" } } });
    await renderPage();
    promoteSam();
    await waitFor(() => expect(notify.error).toHaveBeenCalledWith("Cannot modify a super admin", { id: "member-role:u2" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(api.post).not.toHaveBeenCalled();
    await act(async () => {});
    expect(samSelect().value).toBe("host");
  });
});
