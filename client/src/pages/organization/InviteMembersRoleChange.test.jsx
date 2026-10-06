import { render, screen, waitFor, fireEvent } from "@testing-library/react";
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
      interceptors: {
        request: { use: vi.fn() },
        response: { use: vi.fn() },
      },
    },
  };
});

vi.mock("../../ui/Toast", () => ({
  notify: {
    error: vi.fn(),
    success: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
    alert: vi.fn(),
  },
}));

let currentUser = { id: "u1", full_name: "Radha Admin", email: "radha@zoikogroup.com", role: "org_admin" };
vi.mock("../../auth/AuthContext", () => ({
  useAuth: () => ({ user: currentUser, logout: vi.fn() }),
}));

import api, { errMsg, errCode } from "../../api";
import { notify } from "../../ui/Toast";
import InviteMembers from "./InviteMembers";

const MOCK_MEMBERS = [
  { id: "u1", full_name: "Radha Admin", email: "radha@zoikogroup.com", role: "org_admin", is_active: true, created_at: "2026-09-23T00:00:00Z" },
  { id: "u2", full_name: "Sam Host", email: "sam@example.com", role: "host", is_active: true, created_at: "2026-09-24T00:00:00Z" },
];

const MOCK_INVITATIONS = [
  { id: "i1", email: "invitee@example.com", role: "viewer", status: "pending", invited_by: "radha@zoikogroup.com", expires_at: "2026-10-01T00:00:00Z" },
];

function setupApiMocks() {
  vi.clearAllMocks();
  api.get.mockImplementation((url) => {
    if (url === "/organization/users") {
      return Promise.resolve({ data: { items: [...MOCK_MEMBERS], total: 2 } });
    }
    if (url === "/organization/invitations") {
      return Promise.resolve({ data: { items: [...MOCK_INVITATIONS], total: 1 } });
    }
    return Promise.reject(new Error(`Unhandled URL: ${url}`));
  });
}

describe("InviteMembers role-change error handling and state management", () => {
  beforeEach(() => {
    setupApiMocks();
  });

  it("1. Admin changes own role Admin -> Host: blocked by business rule, exactly one toast, role reverts to Admin", async () => {
    api.patch.mockRejectedValueOnce({
      response: {
        status: 400,
        data: {
          code: "SELF_ROLE_CHANGE_FORBIDDEN",
          message: "You cannot demote or deactivate yourself",
          detail: "You cannot demote or deactivate yourself",
        },
      },
    });

    render(
      <MemoryRouter>
        <InviteMembers />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText("Radha Admin")).toBeInTheDocument();
    });

    const adminSelect = screen.getByLabelText("Role for Radha Admin");
    expect(adminSelect.value).toBe("org_admin");

    // Attempt self-demotion to host
    fireEvent.change(adminSelect, { target: { value: "host" } });

    await waitFor(() => {
      expect(api.patch).toHaveBeenCalledTimes(1);
    });

    expect(api.patch).toHaveBeenCalledWith("/organization/users/u1", { role: "host" });

    // Exactly one toast, with correct business message and NO DB unreachable toast
    expect(notify.error).toHaveBeenCalledTimes(1);
    expect(notify.error).toHaveBeenCalledWith("You cannot demote or deactivate yourself");
    expect(notify.error).not.toHaveBeenCalledWith(expect.stringMatching(/database is unreachable/i));
    expect(notify.success).not.toHaveBeenCalled();

    // Dropdown state is cleanly restored to Admin
    expect(adminSelect.value).toBe("org_admin");
  });

  it("2. Admin changes another member's role: succeeds, role updates, no false error", async () => {
    api.patch.mockResolvedValueOnce({
      data: { id: "u2", full_name: "Sam Host", role: "viewer", is_active: true },
    });

    render(
      <MemoryRouter>
        <InviteMembers />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText("Sam Host")).toBeInTheDocument();
    });

    const samSelect = screen.getByLabelText("Role for Sam Host");
    expect(samSelect.value).toBe("host");

    fireEvent.change(samSelect, { target: { value: "viewer" } });

    await waitFor(() => {
      expect(api.patch).toHaveBeenCalledTimes(1);
    });

    expect(api.patch).toHaveBeenCalledWith("/organization/users/u2", { role: "viewer" });

    await waitFor(() => {
      expect(notify.success).toHaveBeenCalledWith("Sam Host is now Viewer");
    });
    expect(notify.error).not.toHaveBeenCalled();
  });

  it("3. Actual database failure: shows service-unavailable toast", async () => {
    api.patch.mockRejectedValueOnce({
      response: {
        status: 503,
        data: {
          detail: "Service temporarily unavailable - the database is unreachable. Please try again.",
        },
      },
    });

    render(
      <MemoryRouter>
        <InviteMembers />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText("Sam Host")).toBeInTheDocument();
    });

    const samSelect = screen.getByLabelText("Role for Sam Host");
    fireEvent.change(samSelect, { target: { value: "speaker" } });

    await waitFor(() => {
      expect(notify.error).toHaveBeenCalledTimes(1);
    });

    expect(notify.error).toHaveBeenCalledWith(
      "Service temporarily unavailable - the database is unreachable. Please try again."
    );
    expect(samSelect.value).toBe("host");
  });

  it("4. Permission failure (403): shows permission error only", async () => {
    api.patch.mockRejectedValueOnce({
      response: {
        status: 403,
        data: {
          detail: "Organization admin access is required to modify member roles",
        },
      },
    });

    render(
      <MemoryRouter>
        <InviteMembers />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText("Sam Host")).toBeInTheDocument();
    });

    const samSelect = screen.getByLabelText("Role for Sam Host");
    fireEvent.change(samSelect, { target: { value: "speaker" } });

    await waitFor(() => {
      expect(notify.error).toHaveBeenCalledTimes(1);
    });

    expect(notify.error).toHaveBeenCalledWith(
      "Organization admin access is required to modify member roles"
    );
    expect(notify.error).not.toHaveBeenCalledWith(expect.stringMatching(/database is unreachable/i));
    expect(samSelect.value).toBe("host");
  });

  it("5. Duplicate request prevention: selecting same value or clicking while in-flight does not double-fire", async () => {
    let resolvePatch;
    const patchPromise = new Promise((resolve) => {
      resolvePatch = resolve;
    });
    api.patch.mockReturnValueOnce(patchPromise);

    render(
      <MemoryRouter>
        <InviteMembers />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText("Sam Host")).toBeInTheDocument();
    });

    const samSelect = screen.getByLabelText("Role for Sam Host");

    // Selecting current role is a no-op
    fireEvent.change(samSelect, { target: { value: "host" } });
    expect(api.patch).not.toHaveBeenCalled();

    // Change to speaker
    fireEvent.change(samSelect, { target: { value: "speaker" } });
    expect(api.patch).toHaveBeenCalledTimes(1);

    // While in-flight, select is disabled and another change does not fire another PATCH
    expect(samSelect).toBeDisabled();
    fireEvent.change(samSelect, { target: { value: "viewer" } });
    expect(api.patch).toHaveBeenCalledTimes(1);

    // Resolve in-flight request
    resolvePatch({ data: { id: "u2", role: "speaker" } });
    await waitFor(() => {
      expect(notify.success).toHaveBeenCalledWith("Sam Host is now Speaker");
    });
    await waitFor(() => {
      const refreshedSelect = screen.getByLabelText("Role for Sam Host");
      expect(refreshedSelect).not.toBeDisabled();
    });
  });

  it("6. Helper errMsg and errCode properly parse structured responses", () => {
    // Structured error detail
    const errStructured = {
      response: {
        data: {
          detail: {
            code: "SELF_ROLE_CHANGE_FORBIDDEN",
            message: "You cannot demote or deactivate yourself",
          },
        },
      },
    };
    expect(errMsg(errStructured)).toBe("You cannot demote or deactivate yourself");
    expect(errCode(errStructured)).toBe("SELF_ROLE_CHANGE_FORBIDDEN");

    // Top-level message and code
    const errTopLevel = {
      response: {
        data: {
          code: "SELF_DELETE_FORBIDDEN",
          message: "You cannot delete your own account",
        },
      },
    };
    expect(errMsg(errTopLevel)).toBe("You cannot delete your own account");
    expect(errCode(errTopLevel)).toBe("SELF_DELETE_FORBIDDEN");

    // Plain string detail
    const errString = {
      response: {
        data: {
          detail: "Invalid role specified",
        },
      },
    };
    expect(errMsg(errString)).toBe("Invalid role specified");
  });
});
