// Identity & Access grants two roles, and converts nobody.
//
// The role selector offered all six of User.ROLES, so the platform console looked like the
// place to hand out an organization's own membership. Billing Admin, Host, Speaker and Viewer
// are granted inside an org (invitations, member management) or per event — that is where
// their forms and their enforcement live. Only Super Admin and Org Admin are platform
// decisions, so only those two can be GRANTED here.
//
// The part worth testing is what happens to the accounts that already hold the other four.
// Nothing: their role is not in the assignable list, so a controlled <select> would render
// BLANK for them, and an operator who opened the modal to fix a typo in a name would be
// invited to pick a role just to fill the field in. The current value stays visible and
// selected instead, and saving re-sends it unchanged.
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api", () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  errMsg: (e) => e?.message ?? "Something went wrong.",
}));

vi.mock("../../ui/Toast", () => ({
  notify: { error: vi.fn(), success: vi.fn(), alert: vi.fn() },
}));

import api from "../../api";
import { ThemeProvider } from "../../theme/ThemeContext";
import UserModal from "./UserModal";
import { ASSIGNABLE_PLATFORM_ROLES, ROLES } from "./roleInfo";

const REMOVED = ["billing_admin", "host", "speaker", "viewer"];

const userWithRole = (role) => ({
  id: "u1",
  full_name: "Virat",
  email: "virat@example.com",
  organization_name: "zoiko",
  role,
  is_active: true,
});

const openModal = (user) =>
  render(
    <ThemeProvider>
      <UserModal open user={{ org_id: "o1", ...user }} onClose={vi.fn()} onSaved={vi.fn()} />
    </ThemeProvider>
  );

/** Wait for the support-access read to settle, so Save is enabled before clicking it. */
const saveButton = async () => {
  const btn = screen.getByRole("button", { name: /save changes/i });
  await waitFor(() => expect(btn).not.toBeDisabled());
  return btn;
};

// The modal's <Label> is not bound to the control by id, so getByLabelText cannot find it.
// Role is the first combobox in the form (the commercial-scope select, when present, follows
// it), which is what the "clears the commercial scope" test below relies on.
const roleSelect = () => screen.getAllByRole("combobox")[0];

const optionTexts = () =>
  within(roleSelect()).getAllByRole("option").map((o) => o.textContent.trim());

// Editing a tenant's member is ORG-009 support access, so the modal now reads
// GET /admin/support-access/state and disables Save until an approved session is live. These
// tests are about ROLE handling, so they grant that session and assert the role behaviour on
// top of it; the gate itself is covered in identityAccessSupportGate.test.jsx.
const ACTIVE_SESSION = {
  org_id: "o1",
  can_write_members: true,
  other_engineer_active: false,
  mine: { id: "s1", status: "active", expires_at: "2030-01-01T00:00:00Z" },
};

const serveSupport = (state = ACTIVE_SESSION) => {
  vi.mocked(api.get).mockImplementation((url) =>
    url === "/admin/support-access/state"
      ? Promise.resolve({ data: state })
      : Promise.resolve({ data: {} })
  );
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.patch).mockResolvedValue({ data: {} });
  serveSupport();
});

describe("what the selector offers", () => {
  it("offers only Super Admin and Org Admin", () => {
    openModal(userWithRole("org_admin"));
    expect(optionTexts()).toEqual(["Super Admin", "Org Admin"]);
  });

  it.each(REMOVED)("does not offer %s as a new assignment", (role) => {
    openModal(userWithRole("super_admin"));
    const values = within(roleSelect()).getAllByRole("option").map((o) => o.value);
    expect(values).not.toContain(role);
  });

  it("assignable list is exactly the two platform roles", () => {
    expect(ASSIGNABLE_PLATFORM_ROLES).toEqual(["super_admin", "org_admin"]);
  });
});

describe("an account that already holds one of the removed roles", () => {
  it.each(REMOVED)("keeps %s visible and selected rather than blank", (role) => {
    openModal(userWithRole(role));

    // The bug this prevents: no matching <option> means selectedIndex === -1 and an empty box.
    expect(roleSelect().value).toBe(role);
    expect(roleSelect().selectedIndex).toBeGreaterThanOrEqual(0);
    expect(optionTexts()).toContain(`${role.split("_").map((w) => w[0].toUpperCase() + w.slice(1)).join(" ")} (current)`);
  });

  it.each(REMOVED)("re-sends %s unchanged when only the name is edited", async (role) => {
    const u = userEvent.setup();
    openModal(userWithRole(role));

    const name = screen.getByDisplayValue("Virat");
    await u.clear(name);
    await u.type(name, "Virat Kohli");
    await u.click(await saveButton());

    await waitFor(() => expect(api.patch).toHaveBeenCalled());
    const [, body] = vi.mocked(api.patch).mock.calls[0];
    // No automatic conversion: the role that goes back is the role that came in.
    expect(body.role).toBe(role);
    expect(body.full_name).toBe("Virat Kohli");
  });

  it("marks only the legacy role as current, never the two assignable ones", () => {
    openModal(userWithRole("host"));
    const texts = optionTexts();
    expect(texts).toEqual(["Super Admin", "Org Admin", "Host (current)"]);
    expect(texts.filter((t) => t.includes("(current)"))).toHaveLength(1);
  });

  it("still allows a deliberate promotion to an assignable role", async () => {
    const u = userEvent.setup();
    openModal(userWithRole("viewer"));

    await u.selectOptions(roleSelect(), "org_admin");
    await u.click(await saveButton());

    await waitFor(() => expect(api.patch).toHaveBeenCalled());
    expect(vi.mocked(api.patch).mock.calls[0][1].role).toBe("org_admin");
  });

  it("lets a mis-click be undone before saving", () => {
    // roleOptions is derived from user.role, not the draft — so picking Org Admin must not
    // make the original option disappear and strand the operator.
    openModal(userWithRole("host"));
    const values = within(roleSelect()).getAllByRole("option").map((o) => o.value);
    expect(values).toContain("host");
  });
});

describe("nothing else was narrowed", () => {
  it("the full role vocabulary is untouched", () => {
    // Mirrors server/app/models/user.py ROLES. The list page filters by this, so an operator
    // can still FIND every existing Billing Admin, Host, Speaker and Viewer.
    expect(ROLES).toEqual(["super_admin", "org_admin", "billing_admin", "host", "speaker", "viewer"]);
  });

  it("every removed role is still a real role, just not one this console grants", () => {
    for (const role of REMOVED) {
      expect(ROLES).toContain(role);
      expect(ASSIGNABLE_PLATFORM_ROLES).not.toContain(role);
    }
  });
});

describe("super admin behaviour is preserved", () => {
  it("still exposes the commercial staff scope for a super admin", () => {
    openModal(userWithRole("super_admin"));
    expect(screen.getByText(/commercial staff scope/i)).toBeInTheDocument();
  });

  it("does not show it for an org admin", () => {
    openModal(userWithRole("org_admin"));
    expect(screen.queryByText(/commercial staff scope/i)).not.toBeInTheDocument();
  });

  it("clears the commercial scope when the role is not super admin", async () => {
    const u = userEvent.setup();
    openModal(userWithRole("super_admin"));

    await u.selectOptions(roleSelect(), "org_admin");
    await u.click(await saveButton());

    await waitFor(() => expect(api.patch).toHaveBeenCalled());
    expect(vi.mocked(api.patch).mock.calls[0][1].staff_commercial_role).toBe("");
  });
});
