// Platform Configuration -> "Save changes" and the platform elevation it needs.
//
// The report: "This action needs elevated access. Start an elevation session and try again."
// appeared as a red error on this page. Traced: loading the page sends only GETs, and no GET is
// elevation-gated; the toast was the server's refusal of PATCH /admin/settings, which requires
// a platform elevation (security.require_elevation). The form offered Save to everyone, let the
// operator edit, sent the doomed request, and only then said to elevate - with no way to do it
// from the form. The retention panel on this same page already used the console's elevation
// workflow (useElevation + its note); these pin the form doing the same.
import { useCallback, useState } from "react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Outlet, Route, Routes } from "react-router-dom";

vi.mock("../../api", () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  errMsg: (e) => e?.response?.data?.detail ?? e?.message ?? "failed",
  AUTH_EXPIRED_EVENT: "zoiko:auth-expired",
}));
vi.mock("../../ui/Toast", () => ({
  notify: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
}));
vi.mock("../../auth/AuthContext", () => ({
  useAuth: () => ({ user: { id: "u-me", full_name: "Ada Ops", role: "super_admin" } }),
}));

import api from "../../api";
import { notify } from "../../ui/Toast";
import { ThemeProvider } from "../../theme/ThemeContext";
import Settings from "./Settings";

const SETTINGS = {
  brand: { value: { name: "ZoikoStream", primary_color: "#8b5cf6", support_email: "ops@zoikostream.com" } },
  storage_limits: { value: {} },
  streaming_limits: { value: {} },
  global: { value: { signups_enabled: true, maintenance_mode: false } },
};
const RETENTION = {
  effective: { version: "default-v1", retention_days: 365, warning_days: 14, effective_at: null, approved_by_email: null },
  proposal: null,
  bounds: { min_retention_days: 30, max_retention_days: 3650, min_warning_days: 1 },
  impact: "Applies to recordings finalized after approval.",
};
const PLATFORM = { id: "e1", scope: "platform", granted_scopes: ["platform"], seconds_remaining: 600 };
const NEED = "Saving these settings needs platform elevation. Your edits are kept while you elevate.";
const ELEVATION_REFUSAL = "This action needs elevated access. Start an elevation session and try again.";

// The admin shell, as AdminLayout provides it: console state over Outlet context. `reload` is
// the shell's /admin/console-state refresh; `afterReload` is what that refresh reports next.
let afterReload;
const reloads = vi.fn();

function Shell({ start, unknown }) {
  const [elevation, setElevation] = useState(start);
  const reload = useCallback(async () => {
    reloads();
    if (afterReload !== undefined) setElevation(afterReload);
  }, []);
  return <Outlet context={{ state: unknown ? null : { elevation }, unknown, reload }} />;
}

const show = ({ elevation = null, unknown = false } = {}) =>
  render(
    <ThemeProvider>
      <MemoryRouter>
        <Routes>
          <Route element={<Shell start={elevation} unknown={unknown} />}>
            <Route path="/" element={<Settings />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </ThemeProvider>
  );

const nameField = () => screen.findByDisplayValue(/^ZoikoStream/);
const saveButton = () => screen.getByRole("button", { name: "Save changes" });
const formNote = () => screen.getByText(NEED).parentElement;

beforeEach(() => {
  vi.clearAllMocks();
  afterReload = undefined;
  vi.mocked(api.get).mockImplementation((url) =>
    Promise.resolve({ data: url === "/admin/settings" ? SETTINGS : RETENTION }));
  vi.mocked(api.patch).mockResolvedValue({ data: {} });
});

describe("Platform Configuration — loading and editing need no elevation", () => {
  it.each([["none", null], ["active", PLATFORM]])("loads with reads only and no toast (elevation: %s)", async (_, elevation) => {
    show({ elevation });
    await nameField();
    // The retention panel fetches from its own mount effect; wait for it to have loaded rather
    // than assume that effect has already run when the form appears.
    await screen.findByTestId("retention-effective");
    expect(vi.mocked(api.get).mock.calls.map(([url]) => url).sort()).toEqual(["/admin/retention-policy", "/admin/settings"]);
    expect(api.patch).not.toHaveBeenCalled();
    expect(api.post).not.toHaveBeenCalled();
    expect(notify.error).not.toHaveBeenCalled();
  });

  it("says what Save needs before anything is sent, and lets the form be edited", async () => {
    show();
    fireEvent.change(await nameField(), { target: { value: "ZoikoStream Live" } });
    expect(screen.getByDisplayValue("ZoikoStream Live")).toBeInTheDocument();
    expect(screen.getByText(NEED)).toBeInTheDocument();
    expect(saveButton()).toBeDisabled();
    fireEvent.click(saveButton());
    expect(api.patch).not.toHaveBeenCalled();
    expect(notify.error).not.toHaveBeenCalled();
  });

  it("names an expired elevation", async () => {
    show({ elevation: { ...PLATFORM, seconds_remaining: 0 } });
    await nameField();
    expect(screen.getAllByText("Your platform elevation has expired.").length).toBeGreaterThan(0);
    expect(saveButton()).toBeDisabled();
  });

  it("names an elevation for a different scope", async () => {
    show({ elevation: { ...PLATFORM, scope: "identity", granted_scopes: ["identity"] } });
    await nameField();
    expect(screen.getAllByText("Your current elevation does not cover platform configuration.").length).toBeGreaterThan(0);
    expect(saveButton()).toBeDisabled();
  });

  it("does not block Save when the elevation state is unknown — the server decides", async () => {
    show({ unknown: true });
    await nameField();
    expect(saveButton()).toBeEnabled();
  });
});

describe("Platform Configuration — saving", () => {
  it("with platform elevation, sends one PATCH carrying the edits", async () => {
    show({ elevation: PLATFORM });
    fireEvent.change(await nameField(), { target: { value: "ZoikoStream Live" } });
    expect(screen.queryByText(NEED)).not.toBeInTheDocument();
    fireEvent.click(saveButton());
    await waitFor(() => expect(notify.success).toHaveBeenCalledWith("Platform settings saved"));
    expect(api.patch).toHaveBeenCalledTimes(1);
    expect(api.patch.mock.calls[0][1].values.brand.name).toBe("ZoikoStream Live");
  });

  it("elevating from the form keeps the edits, then saves them", async () => {
    show();
    fireEvent.change(await nameField(), { target: { value: "ZoikoStream Live" } });
    vi.mocked(api.post).mockResolvedValue({ data: { scope: "platform", granted_scopes: ["platform"] } });
    afterReload = PLATFORM;
    fireEvent.click(within(formNote()).getByRole("button", { name: "Elevate for platform" }));
    await waitFor(() => expect(saveButton()).toBeEnabled());
    expect(api.post).toHaveBeenCalledWith("/admin/elevation",
      { scope: "platform", reason: "Change platform configuration" });
    expect(screen.getByDisplayValue("ZoikoStream Live")).toBeInTheDocument();
    expect(api.patch).not.toHaveBeenCalled();      // elevating is not saving
    fireEvent.click(saveButton());
    await waitFor(() => expect(api.patch).toHaveBeenCalledTimes(1));
    expect(api.patch.mock.calls[0][1].values.brand.name).toBe("ZoikoStream Live");
  });

  it("a refused elevation sends nothing and keeps the edits", async () => {
    show();
    fireEvent.change(await nameField(), { target: { value: "ZoikoStream Live" } });
    vi.mocked(api.post).mockRejectedValue({ response: { status: 403, data: { detail: "Elevation refused" } } });
    fireEvent.click(within(formNote()).getByRole("button", { name: "Elevate for platform" }));
    await waitFor(() => expect(notify.error).toHaveBeenCalledWith("Elevation refused"));
    expect(api.patch).not.toHaveBeenCalled();
    expect(saveButton()).toBeDisabled();
    expect(screen.getByDisplayValue("ZoikoStream Live")).toBeInTheDocument();
  });

  it("a grant the server no longer honours is reported once, and the shell re-checks it", async () => {
    // Ended from another tab between the console's last refresh and this click.
    show({ elevation: PLATFORM });
    fireEvent.change(await nameField(), { target: { value: "ZoikoStream Live" } });
    vi.mocked(api.patch).mockRejectedValue({ response: { status: 403, data: { detail: ELEVATION_REFUSAL } } });
    afterReload = null;
    fireEvent.click(saveButton());
    await waitFor(() => expect(notify.error).toHaveBeenCalledWith(ELEVATION_REFUSAL));
    expect(notify.error).toHaveBeenCalledTimes(1);
    expect(notify.success).not.toHaveBeenCalled();
    // Wait for the note, not for a disabled Save: Save is ALSO disabled while the request is in
    // flight, so that would pass before the re-check has been rendered.
    expect(await screen.findByText(NEED)).toBeInTheDocument();   // the re-check found no grant
    expect(reloads).toHaveBeenCalled();
    expect(saveButton()).toBeDisabled();
    expect(screen.getByDisplayValue("ZoikoStream Live")).toBeInTheDocument();
  });

  it("sends one PATCH however often Save is pressed while saving", async () => {
    let finish;
    vi.mocked(api.patch).mockReturnValue(new Promise((resolve) => { finish = resolve; }));
    show({ elevation: PLATFORM });
    await nameField();
    fireEvent.click(saveButton());
    fireEvent.click(saveButton());
    expect(api.patch).toHaveBeenCalledTimes(1);
    finish({ data: {} });
    await waitFor(() => expect(notify.success).toHaveBeenCalledTimes(1));
  });
});
