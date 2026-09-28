// Editing a platform account is governance. It is no longer gated on the customer.
//
// ── WHAT THIS FILE USED TO ASSERT ───────────────────────────────────────────────────────
// That the Edit User modal refused every change until the organization approved a support
// session: a blocking banner, a disabled Save, and a "Request support access" button. That
// was an accurate test of the behaviour, and the behaviour was wrong. A super admin could not
// correct a typo in a name, or grant someone the super_admin role, without asking a tenant
// for permission — and the tenant has no standing to approve who operates the platform.
//
// PATCH /admin/users/{id} is now platform governance (see routers/admin.update_user):
// super_admin at the router, an "identity" elevation for the high-risk subset, the
// active-super-admin floor, and an audit row either way. ORG-009 still governs DELETE,
// because destroying a customer's member record is not governance of a platform account.
//
// So this file now pins two things: the modal lets an operator work, and the support-state
// VOCABULARY is still read honestly — that helper survives because the modal still reports
// an active session when there is one, it just no longer waits for one.
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api", () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  errMsg: (e) => e?.response?.data?.detail ?? e?.message ?? "Something went wrong.",
}));

const notify = { error: vi.fn(), success: vi.fn(), alert: vi.fn() };
vi.mock("../../ui/Toast", () => ({
  notify: {
    error: (...a) => notify.error(...a),
    success: (...a) => notify.success(...a),
    alert: (...a) => notify.alert(...a),
  },
}));

import api from "../../api";
import { ThemeProvider } from "../../theme/ThemeContext";
import UserModal from "./UserModal";
import { readSupportState } from "./supportAccess";

const USER = {
  id: "aaaaaaaa-1111-2222-3333-444444444444",
  full_name: "Hana Host",
  email: "hana@northwind.com",
  organization_name: "Northwind",
  org_id: "o-north",
  role: "org_admin",
  is_active: true,
};

// What /admin/support-access/state returns. `null` is the common case — no support session
// has ever been requested for this organization, which is exactly the screenshot that
// prompted this change.
const serve = (supportState = null) => {
  vi.mocked(api.get).mockImplementation((url) =>
    url === "/admin/support-access/state"
      ? Promise.resolve({ data: supportState })
      : Promise.resolve({ data: {} })
  );
  vi.mocked(api.patch).mockResolvedValue({ data: { ...USER, full_name: "Hana Renamed" } });
};

const open = (onSaved = vi.fn()) =>
  render(
    <ThemeProvider>
      <UserModal open user={USER} onClose={vi.fn()} onSaved={onSaved} />
    </ThemeProvider>
  );

beforeEach(() => {
  vi.clearAllMocks();
  serve();
});

// ── the operator can actually work ─────────────────────────────────────────────────────

describe("with no support session at all", () => {
  it("does not demand organization approval", async () => {
    open();
    await screen.findByDisplayValue(USER.full_name);

    expect(screen.queryByText(/Organization approval is required/i)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Request support access/i })).not.toBeInTheDocument();
  });

  it("leaves Save enabled", async () => {
    open();
    await screen.findByDisplayValue(USER.full_name);

    expect(screen.getByRole("button", { name: /Save changes/i })).toBeEnabled();
  });

  it("lets the name be edited and saved through the real endpoint", async () => {
    const onSaved = vi.fn();
    open(onSaved);
    const name = await screen.findByDisplayValue(USER.full_name);

    await userEvent.clear(name);
    await userEvent.type(name, "Hana Renamed");
    await userEvent.click(screen.getByRole("button", { name: /Save changes/i }));

    await waitFor(() =>
      expect(api.patch).toHaveBeenCalledWith(
        `/admin/users/${USER.id}`,
        expect.objectContaining({ full_name: "Hana Renamed" })
      )
    );
    // The table is told, so the row updates without a reload.
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
  });

  it("offers only the canonical platform roles", async () => {
    open();
    await screen.findByDisplayValue(USER.full_name);
    const select = screen.getAllByRole("combobox")[0];
    const values = [...select.querySelectorAll("option")].map((o) => o.value);

    expect(values).toContain("super_admin");
    expect(values).toContain("org_admin");
    expect(values).not.toContain("Super Admin");
  });

  it("keeps email and organization read-only", async () => {
    // Neither is editable from this modal, and showing them as inputs that cannot be used is
    // better than implying a capability that does not exist.
    open();
    await screen.findByDisplayValue(USER.full_name);

    expect(screen.getByDisplayValue(USER.email)).toBeDisabled();
    expect(screen.getByDisplayValue(USER.organization_name)).toBeDisabled();
  });
});

describe("the server stays the authority", () => {
  it("surfaces a refusal rather than pretending the change landed", async () => {
    // The high-risk subset still needs an "identity" elevation, and the modal must report
    // that verbatim instead of swallowing it.
    vi.mocked(api.patch).mockRejectedValue({
      response: { status: 403, data: { detail: "This change (platform role change org_admin -> super_admin) needs an active 'identity' elevation." } },
    });
    const onSaved = vi.fn();
    open(onSaved);
    await screen.findByDisplayValue(USER.full_name);

    await userEvent.click(screen.getByRole("button", { name: /Save changes/i }));

    await waitFor(() => expect(notify.error).toHaveBeenCalled());
    expect(String(notify.error.mock.calls[0][0])).toMatch(/elevation/i);
    expect(onSaved).not.toHaveBeenCalled();
  });
});

// ── an active session is still reported, it is just not a precondition ─────────────────

describe("when a support session IS active", () => {
  it("says so, without making it the reason Save works", async () => {
    serve({ can_write_members: true, mine: { status: "active", expires_at: "2027-01-01T00:00:00Z" } });
    open();

    expect(await screen.findByText(/Support session active/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Save changes/i })).toBeEnabled();
  });
});

// ── the status vocabulary is still read honestly ───────────────────────────────────────
//
// readSupportState survives the change: the modal still uses it to report an active session.
// These stay because a backend status must never be mislabelled by the console.

describe("support status vocabulary", () => {
  it.each([
    ["requested", /Awaiting organization approval/i],
    ["approved", /Approved/i],
    ["active", /Active/i],
    ["denied", /Denied/i],
    ["expired", /Expired/i],
  ])("reports %s as itself", (status, label) => {
    expect(readSupportState({ mine: { status } }).label).toMatch(label);
  });

  it("shows an unrecognised backend status rather than guessing", () => {
    const state = readSupportState({ mine: { status: "some_new_status" } });
    expect(state.canWrite).toBe(false);
  });

  it("treats a failed read as no access, never as granted", () => {
    // The safe direction: if the state could not be read, the console must not claim a
    // session is active.
    expect(readSupportState(null).canWrite).toBe(false);
  });
});
