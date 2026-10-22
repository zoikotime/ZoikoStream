// Deleting an organization from the Super Admin console.
//
// The flow used to be: window.confirm("Delete X? This cannot be undone.") -> DELETE -> a toast
// carrying whatever the server said. Two things were wrong with it. The confirmation was the
// browser's, not this console's, and it could not say anything about the organization. And it
// was shown BEFORE anything checked whether the delete could succeed — so an operator
// confirmed a permanent, destructive action that the server was always going to refuse,
// because the organization still owned users, events, tickets or commercial records.
//
// Pinned here: the dialog is the design system's and it states the preconditions from the
// row's own counts; one DELETE per confirmation; success is only ever claimed after the
// server returns; a refusal is shown with the server's own explanation; and the list and the
// counters above it reload from the same request.
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

vi.mock("../../api", () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  errMsg: (e) => e?.response?.data?.detail ?? e?.message ?? "failed",
  diagnoseLoadError: (e) => e?.message ?? "failed to load",
  AUTH_EXPIRED_EVENT: "zoiko:auth-expired",
}));
vi.mock("react-hot-toast", () => ({
  default: { success: vi.fn(), error: vi.fn() },
}));
vi.mock("../../auth/AuthContext", () => ({
  useAuth: () => ({ user: { id: "u-me", full_name: "Ada Ops", role: "super_admin" }, logout: vi.fn() }),
}));

import api from "../../api";
import toast from "react-hot-toast";
import { ThemeProvider } from "../../theme/ThemeContext";
import Organizations from "./Organizations";

const org = (over = {}) => ({
  id: "o-1", name: "e2e-org-10800c1f", domain: null, plan: "Starter", status: "active",
  users_count: 0, events_count: 0, bandwidth_gb: 0, storage_used_gb: 0, region: "US East",
  subscription_status: "active", created_at: "2026-10-01T00:00:00Z", ...over,
});

const serve = (rows) =>
  vi.mocked(api.get).mockImplementation((url) =>
    url.includes("/admin/organizations")
      ? Promise.resolve({ data: { items: rows, total: rows.length, page: 1, page_size: 20 } })
      : Promise.resolve({ data: [] })        // /admin/plans is a plain array
  );

const show = () =>
  render(
    <ThemeProvider><MemoryRouter><Organizations /></MemoryRouter></ThemeProvider>
  );

// Open the confirmation for the first row.
const openDelete = async (name = "e2e-org-10800c1f") => {
  await screen.findByText(name);
  fireEvent.click(screen.getByRole("button", { name: `Delete ${name}` }));
  return screen.findByRole("dialog");
};

const confirmButton = (dialog) => within(dialog).getByRole("button", { name: "Delete organization" });

const rejection = (statusCode, detail) =>
  Object.assign(new Error(detail), { response: { status: statusCode, data: { detail } } });

beforeEach(() => {
  vi.clearAllMocks();
  serve([org()]);
  vi.mocked(api.delete).mockResolvedValue({ status: 204 });
});

describe("the confirmation", () => {
  it("is the console's dialog, not the browser's", async () => {
    const native = vi.spyOn(window, "confirm");
    show();
    const dialog = await openDelete();
    expect(dialog).toBeInTheDocument();
    expect(native).not.toHaveBeenCalled();
    native.mockRestore();
  });

  it("names the organization and says the delete is permanent", async () => {
    show();
    const dialog = await openDelete();
    expect(within(dialog).getByText(/Delete e2e-org-10800c1f\?/)).toBeInTheDocument();
    expect(dialog.textContent).toMatch(/cannot be undone/i);
  });

  it("warns up front when the organization still owns records", async () => {
    // These are exactly the counts the server refuses the delete over, and the row already
    // carries them — so the operator is told before confirming, not after.
    serve([org({ users_count: 3, events_count: 2 })]);
    show();
    const dialog = await openDelete();
    expect(dialog.textContent).toMatch(/3 user\(s\)/);
    expect(dialog.textContent).toMatch(/2 event\(s\)/);
    expect(dialog.textContent).toMatch(/refuses to delete/i);
  });

  it("sends nothing until it is confirmed", async () => {
    show();
    await openDelete();
    expect(api.delete).not.toHaveBeenCalled();
  });

  it("sends nothing when it is dismissed", async () => {
    show();
    const dialog = await openDelete();
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(api.delete).not.toHaveBeenCalled();
  });
});

describe("a delete the server accepts", () => {
  it("calls the right endpoint for the right organization", async () => {
    show();
    const dialog = await openDelete();
    fireEvent.click(confirmButton(dialog));
    await waitFor(() => expect(api.delete).toHaveBeenCalledWith("/admin/organizations/o-1"));
  });

  it("reports success only after the server confirmed it", async () => {
    let resolve;
    vi.mocked(api.delete).mockImplementation(() => new Promise((r) => { resolve = () => r({ status: 204 }); }));
    show();
    const dialog = await openDelete();
    fireEvent.click(confirmButton(dialog));
    // In flight: nothing claimed yet.
    expect(toast.success).not.toHaveBeenCalled();
    resolve();
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("e2e-org-10800c1f deleted"));
  });

  it("reloads the list and the counters without a page refresh", async () => {
    show();
    const before = vi.mocked(api.get).mock.calls.length;
    const dialog = await openDelete();
    fireEvent.click(confirmButton(dialog));
    await waitFor(() => expect(vi.mocked(api.get).mock.calls.length).toBeGreaterThan(before));
  });

  it("closes the dialog", async () => {
    show();
    const dialog = await openDelete();
    fireEvent.click(confirmButton(dialog));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("issues ONE request however many times confirm is pressed", async () => {
    let resolve;
    vi.mocked(api.delete).mockImplementation(() => new Promise((r) => { resolve = () => r({ status: 204 }); }));
    show();
    const dialog = await openDelete();
    const button = confirmButton(dialog);
    fireEvent.click(button);
    fireEvent.click(button);
    fireEvent.click(button);
    expect(api.delete).toHaveBeenCalledTimes(1);
    resolve();
    await waitFor(() => expect(toast.success).toHaveBeenCalled());
  });
});

describe("a delete the server refuses", () => {
  it.each([
    [409, "Organization still owns 2 event(s), 1 invitation(s). Remove or reassign them first, or suspend the organization instead of deleting it."],
    [409, "Organization has 3 user(s); remove them before deleting"],
    [403, "This action needs elevated access. Start an elevation session and try again."],
    [404, "Organization not found"],
    [400, "Cannot delete your own organization"],
  ])("shows the server's own explanation (%i)", async (code, detail) => {
    vi.mocked(api.delete).mockRejectedValue(rejection(code, detail));
    show();
    const dialog = await openDelete();
    fireEvent.click(confirmButton(dialog));
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith(detail));
    expect(toast.success).not.toHaveBeenCalled();
  });

  it("never claims success, and leaves the row on the page", async () => {
    vi.mocked(api.delete).mockRejectedValue(rejection(409, "Organization has 3 user(s); remove them before deleting"));
    show();
    const dialog = await openDelete();
    fireEvent.click(confirmButton(dialog));
    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    expect(toast.success).not.toHaveBeenCalled();
    expect(screen.getByText("e2e-org-10800c1f")).toBeInTheDocument();
  });

  it("lets the operator try again — the control is not left stuck", async () => {
    vi.mocked(api.delete).mockRejectedValue(rejection(409, "Organization has 3 user(s); remove them before deleting"));
    show();
    const first = await openDelete();
    fireEvent.click(confirmButton(first));
    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

    vi.mocked(api.delete).mockResolvedValue({ status: 204 });
    const second = await openDelete();
    fireEvent.click(confirmButton(second));
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("e2e-org-10800c1f deleted"));
  });
});
