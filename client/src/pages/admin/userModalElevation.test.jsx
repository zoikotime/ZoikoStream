// Granting Super Admin from Identity & Access, pinned as the step-up it is.
//
// The server refuses a platform role change without an active "identity" elevation
// (routers/admin._is_high_risk_user_change), and that is the intended control. What was
// wrong was the console: the modal offered Save, the operator clicked it, and only then
// learned — from a toast — that an elevation was needed and where to get one. These assert
// the modal now says so BEFORE the click and offers the way out, without ever becoming the
// authority itself, and that ordinary edits are not swept into the step-up.
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Outlet, Route, Routes } from "react-router-dom";

vi.mock("../../api", () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  errMsg: (e) => e?.response?.data?.detail ?? e?.message ?? "Something went wrong.",
}));
vi.mock("../../ui/Toast", () => ({
  notify: { error: vi.fn(), success: vi.fn(), alert: vi.fn() },
}));

import api from "../../api";
import { notify } from "../../ui/Toast";
import { ThemeProvider } from "../../theme/ThemeContext";
import UserModal from "./UserModal";

const ORG_ADMIN = {
  id: "u1", full_name: "Naveen", email: "naveen@example.com", role: "org_admin",
  is_active: true, org_id: "o1", organization_name: "Vihar", staff_commercial_role: null,
};

const grant = (over = {}) => ({
  id: "elev-1", scope: "platform", scopes: ["identity", "broadcast"],
  granted_scopes: ["broadcast", "identity", "platform"], reason: "Console session",
  seconds_remaining: 600, ...over,
});

const show = (elevation, { unknown = false, user = ORG_ADMIN, reload = vi.fn() } = {}) =>
  render(
    <ThemeProvider>
      <MemoryRouter initialEntries={["/admin/users"]}>
        <Routes>
          <Route element={<Outlet context={{ state: unknown ? null : { elevation }, unknown, reload }} />}>
            <Route path="/admin/users"
                   element={<UserModal open user={user} onClose={vi.fn()} onSaved={vi.fn()} />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </ThemeProvider>
  );

const roleSelect = () => screen.getAllByRole("combobox")[0];
const promote = async (u) => u.selectOptions(roleSelect(), "super_admin");

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.get).mockResolvedValue({ data: null });          // support-access/state
  vi.mocked(api.patch).mockResolvedValue({ data: {} });
});

describe("promoting to Super Admin without an identity elevation", () => {
  it("explains the requirement and offers Elevate instead of Save", async () => {
    const u = userEvent.setup();
    show(null);
    await promote(u);

    expect(screen.getByRole("status")).toHaveTextContent(/needs an active identity elevation/i);
    expect(screen.getByRole("button", { name: /elevate access/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /save changes/i })).not.toBeInTheDocument();
    expect(api.patch).not.toHaveBeenCalled();
  });

  it("asks the server for an identity grant — nothing broader — and does not save", async () => {
    const u = userEvent.setup();
    const reload = vi.fn();
    vi.mocked(api.post).mockResolvedValue({ data: grant({ scope: "identity", scopes: [], granted_scopes: ["identity"] }) });
    show(null, { reload });
    await promote(u);
    await u.click(screen.getByRole("button", { name: /elevate access/i }));

    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(1));
    const [url, body] = vi.mocked(api.post).mock.calls[0];
    expect(url).toBe("/admin/elevation");
    expect(body.scope).toBe("identity");
    expect(body.scopes).toBeUndefined();
    expect(reload).toHaveBeenCalled();
    expect(api.patch).not.toHaveBeenCalled();
  });

  it("says plainly when an existing, different elevation prevents a new one", async () => {
    const u = userEvent.setup();
    // POST /admin/elevation returns the EXISTING grant when one is open.
    vi.mocked(api.post).mockResolvedValue({ data: grant({ scope: "broadcast", scopes: [], granted_scopes: ["broadcast"] }) });
    show(grant({ scope: "broadcast", scopes: [], granted_scopes: ["broadcast"] }));
    await promote(u);

    expect(screen.getByRole("status")).toHaveTextContent(/does not cover identity/i);
    await u.click(screen.getByRole("button", { name: /elevate access/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/end it from the rail/i);
    expect(api.patch).not.toHaveBeenCalled();
  });

  it("treats an expired grant as expired, not as active", async () => {
    const u = userEvent.setup();
    show(grant({ seconds_remaining: 0 }));
    await promote(u);

    expect(screen.getByRole("status")).toHaveTextContent(/expired/i);
    expect(screen.queryByRole("button", { name: /save changes/i })).not.toBeInTheDocument();
  });
});

describe("with an active identity elevation", () => {
  it("saves the promotion through the real endpoint", async () => {
    const u = userEvent.setup();
    show(grant());
    await promote(u);

    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(screen.getByText(/identity elevation active/i)).toBeInTheDocument();
    await u.click(screen.getByRole("button", { name: /save changes/i }));

    await waitFor(() => expect(api.patch).toHaveBeenCalledTimes(1));
    const [url, body] = vi.mocked(api.patch).mock.calls[0];
    expect(url).toBe("/admin/users/u1");
    expect(body.role).toBe("super_admin");
  });

  it("still surfaces the server's refusal — the modal is a mirror, not the authority", async () => {
    const u = userEvent.setup();
    vi.mocked(api.patch).mockRejectedValue({
      response: { status: 403, data: { detail: "This change (platform role change org_admin -> super_admin) needs an active 'identity' elevation." } },
    });
    show(grant());
    await promote(u);
    await u.click(screen.getByRole("button", { name: /save changes/i }));

    await waitFor(() => expect(notify.error).toHaveBeenCalledWith(expect.stringMatching(/identity/)));
    expect(notify.success).not.toHaveBeenCalled();
  });
});

describe("the step-up stays proportionate", () => {
  it("does not demand elevation to rename an Org Admin", async () => {
    const u = userEvent.setup();
    show(null);
    const name = screen.getAllByRole("textbox")[0];
    await u.clear(name);
    await u.type(name, "Naveen K");

    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    await u.click(screen.getByRole("button", { name: /save changes/i }));
    await waitFor(() => expect(api.patch).toHaveBeenCalledTimes(1));
    expect(vi.mocked(api.patch).mock.calls[0][1]).not.toHaveProperty("staff_commercial_role");
  });

  it("does demand it to deactivate a Super Admin", async () => {
    const u = userEvent.setup();
    show(null, { user: { ...ORG_ADMIN, role: "super_admin" } });
    await u.click(screen.getByRole("switch"));

    expect(screen.getByRole("status")).toHaveTextContent(/needs an active identity elevation/i);
    expect(screen.getByRole("button", { name: /elevate access/i })).toBeInTheDocument();
  });

  it("leaves Save to the server when the console state is unknown", async () => {
    const u = userEvent.setup();
    show(null, { unknown: true });
    await promote(u);

    expect(screen.getByRole("status")).toHaveTextContent(/access state unknown/i);
    await u.click(screen.getByRole("button", { name: /save changes/i }));
    await waitFor(() => expect(api.patch).toHaveBeenCalledTimes(1));
  });
});
