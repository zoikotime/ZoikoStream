// ORG-009, made visible instead of merely enforced.
//
// Editing a tenant's member is support access to that tenant, so routers/admin.py gates every
// write behind ctx.authorize(org_id, CAP_MEMBERS_WRITE). The gate was already correct and is
// untouched. What was wrong was the console: Edit, Activate/Deactivate and Delete rendered as
// ordinary buttons, every one of them returned 403 "This Organization has not approved an
// active support session", and nothing on screen said what state you were in or what to do.
//
// These pin the surface that replaced that, and — more importantly — pin that it is only a
// surface. The server still decides; disabling Save is a courtesy, never the control.
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api", () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  errMsg: (e) => e?.response?.data?.detail ?? e?.message ?? "Something went wrong.",
}));

const notify = { error: vi.fn(), success: vi.fn(), alert: vi.fn() };
vi.mock("../../ui/Toast", () => ({ notify: { error: (...a) => notify.error(...a), success: (...a) => notify.success(...a), alert: (...a) => notify.alert(...a) } }));

import api from "../../api";
import { ThemeProvider } from "../../theme/ThemeContext";
import UserModal from "./UserModal";
import { CAP_MEMBERS_WRITE, readSupportState } from "./supportAccess";

const USER = {
  id: "aaaaaaaa-1111-2222-3333-444444444444",
  full_name: "Hana Host",
  email: "hana@northwind.com",
  organization_name: "Northwind",
  org_id: "o-north",
  role: "host",
  is_active: true,
};

const state = (over = {}) => ({
  org_id: "o-north",
  can_write_members: false,
  other_engineer_active: false,
  mine: null,
  capability: CAP_MEMBERS_WRITE,
  ...over,
});

const serve = (supportState) => {
  vi.mocked(api.get).mockImplementation((url) =>
    url === "/admin/support-access/state"
      ? Promise.resolve({ data: supportState })
      : Promise.resolve({ data: {} })
  );
};

const open = () =>
  render(
    <ThemeProvider>
      <UserModal open user={USER} onClose={vi.fn()} onSaved={vi.fn()} />
    </ThemeProvider>
  );

const save = () => screen.getByRole("button", { name: /save changes/i });

beforeEach(() => {
  vi.clearAllMocks();
  notify.error.mockClear();
  notify.success.mockClear();
  vi.mocked(api.patch).mockResolvedValue({ data: {} });
  vi.mocked(api.post).mockResolvedValue({ data: {} });
});

describe("no session", () => {
  beforeEach(() => serve(state()));

  it("says approval is required instead of letting the operator guess", async () => {
    open();
    expect(await screen.findByText(/organization approval is required/i)).toBeInTheDocument();
    expect(screen.getByText(/no support access requested/i)).toBeInTheDocument();
  });

  it("disables Save", async () => {
    open();
    await screen.findByText(/organization approval is required/i);
    expect(save()).toBeDisabled();
  });

  it("offers the request action", async () => {
    open();
    expect(await screen.findByRole("button", { name: /request support access/i })).toBeInTheDocument();
  });
});

describe("requesting access", () => {
  beforeEach(() => serve(state()));

  it("calls the real endpoint with the capability the gate demands", async () => {
    const u = userEvent.setup();
    open();
    await u.click(await screen.findByRole("button", { name: /request support access/i }));

    await waitFor(() => expect(api.post).toHaveBeenCalled());
    const [url, body] = vi.mocked(api.post).mock.calls[0];
    expect(url).toBe("/admin/support-access");
    expect(body.org_id).toBe("o-north");
    expect(body.allowed_actions).toEqual([CAP_MEMBERS_WRITE]);
    // Bounded, per the model's SUPPORT_MAX_MINUTES ceiling.
    expect(body.minutes).toBeLessThanOrEqual(480);
  });

  it("does not approve anything itself — Save stays disabled after requesting", async () => {
    const u = userEvent.setup();
    open();
    await u.click(await screen.findByRole("button", { name: /request support access/i }));
    await waitFor(() => expect(api.post).toHaveBeenCalled());

    // The organization has to answer. A request is not an approval.
    expect(save()).toBeDisabled();
    expect(api.patch).not.toHaveBeenCalled();
  });
});

describe("pending / denied / expired are reported as themselves", () => {
  it.each([
    ["requested", /awaiting organization approval/i, false],
    ["approved", /approved — not started/i, false],
    ["denied", /denied/i, true],
    ["expired", /expired/i, true],
    ["ended", /ended/i, true],
  ])("status %s", async (status, copy, canRequestAgain) => {
    serve(state({ mine: { id: "s1", status } }));
    open();

    expect(await screen.findByText(copy)).toBeInTheDocument();
    expect(save()).toBeDisabled();
    const requestBtn = screen.queryByRole("button", { name: /request support access/i });
    expect(Boolean(requestBtn)).toBe(canRequestAgain);
  });

  it("an unrecognised backend status is shown, not mislabelled", async () => {
    serve(state({ mine: { id: "s1", status: "quarantined" } }));
    open();
    expect(await screen.findByText(/quarantined/i)).toBeInTheDocument();
    expect(save()).toBeDisabled();
  });
});

describe("a live session held by someone else", () => {
  it("says so rather than showing 'no access'", async () => {
    serve(state({ other_engineer_active: true }));
    open();
    expect(await screen.findByText(/another engineer holds the active session/i)).toBeInTheDocument();
    expect(save()).toBeDisabled();
  });
});

describe("active session", () => {
  beforeEach(() =>
    serve(state({ can_write_members: true, mine: { id: "s1", status: "active", expires_at: "2030-01-01T00:00:00Z" } })));

  it("enables Save and says the session is active", async () => {
    open();
    expect(await screen.findByText(/support session active/i)).toBeInTheDocument();
    await waitFor(() => expect(save()).not.toBeDisabled());
  });

  it("lets an edit through to the API", async () => {
    const u = userEvent.setup();
    open();
    await waitFor(() => expect(save()).not.toBeDisabled());

    const name = screen.getByDisplayValue("Hana Host");
    await u.clear(name);
    await u.type(name, "Hana H");
    await u.click(save());

    await waitFor(() => expect(api.patch).toHaveBeenCalled());
    const [, body] = vi.mocked(api.patch).mock.calls[0];
    expect(body.full_name).toBe("Hana H");
    // The legacy role still rides through untouched.
    expect(body.role).toBe("host");
  });
});

describe("the gate is the server's, not the button's", () => {
  it("treats a failed state read as NO access, never as granted", async () => {
    vi.mocked(api.get).mockRejectedValue(new Error("network"));
    open();
    await waitFor(() => expect(save()).toBeDisabled());
    expect(await screen.findByText(/organization approval is required/i)).toBeInTheDocument();
  });

  it("surfaces the server's 403 when it refuses anyway", async () => {
    // The UI believed a session was live; the server disagreed. The refusal must reach the
    // operator rather than being swallowed into an apparent success.
    serve(state({ can_write_members: true, mine: { id: "s1", status: "active" } }));
    vi.mocked(api.patch).mockRejectedValue({
      response: { status: 403, data: { detail: "This Organization has not approved an active support session for you." } },
    });

    const u = userEvent.setup();
    open();
    await waitFor(() => expect(save()).not.toBeDisabled());
    await u.click(save());

    await waitFor(() => expect(notify.error).toHaveBeenCalled());
    expect(String(notify.error.mock.calls[0][0])).toMatch(/has not approved an active support session/i);
  });
});

describe("readSupportState maps honestly", () => {
  it("no state at all is no access", () => {
    expect(readSupportState(null).canWrite).toBe(false);
  });

  it("canWrite comes from the server's own answer, not from the status string", () => {
    // "active" alone is not authority — it may be another engineer's session, or expired.
    const s = readSupportState(state({ can_write_members: false, mine: { status: "active" } }));
    expect(s.canWrite).toBe(false);
  });

  it("an approved-but-not-started session does not grant writes", () => {
    expect(readSupportState(state({ mine: { status: "approved" } })).canWrite).toBe(false);
  });
});
