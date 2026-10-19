// Members page: rapid clicks, several members at once, duplicate toasts and rollback.
//
// The real toast system is rendered (AppToaster) so "one visible toast" is asserted on the
// screen, not inferred from call counts; notify is spied on, not replaced.
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import toast from "react-hot-toast";

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

vi.mock("../../auth/AuthContext", () => ({
  useAuth: () => ({
    user: { id: "u1", full_name: "Radha Admin", email: "radha@zoikogroup.com", role: "org_admin" },
    logout: vi.fn(),
  }),
}));

import api from "../../api";
import { notify } from "../../ui/Toast";
import AppToaster from "../../ui/AppToaster";
import InviteMembers from "./InviteMembers";

beforeAll(() => {
  // react-hot-toast reads prefers-reduced-motion; jsdom has no matchMedia.
  window.matchMedia ??= () => ({ matches: false, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} });
});

let server;
const member = (id, full_name, role) => ({
  id, full_name, role, email: `${id}@example.com`, is_active: true, created_at: "2026-09-23T00:00:00Z",
});
const USERS = "/organization/users";

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

const httpError = (status, detail) => ({ response: { status, data: { detail } } });
const UNAVAILABLE = "Service temporarily unavailable - the database is unreachable. Please try again.";
const SELF = { code: "SELF_ROLE_CHANGE_FORBIDDEN", message: "You cannot demote or deactivate yourself", detail: "You cannot demote or deactivate yourself" };

let gate = null; // when set, the next member-list GET waits on it (to hold a reload open)

beforeEach(() => {
  vi.clearAllMocks();
  vi.spyOn(notify, "error");
  vi.spyOn(notify, "success");
  gate = null;
  server = [
    { ...member("u1", "Radha Admin", "org_admin"), email: "radha@zoikogroup.com" },
    member("u2", "Sam Host", "host"),
    member("u3", "Vic Viewer", "viewer"),
  ];
  api.get.mockImplementation((url) => {
    if (url === USERS) {
      // Read when the server ANSWERS, so a held reload reports the state at that moment.
      const answer = () => ({ data: { items: server.map((m) => ({ ...m })), total: server.length } });
      return gate ? gate.promise.then(answer) : Promise.resolve(answer());
    }
    if (url === "/organization/invitations") return Promise.resolve({ data: { items: [], total: 0 } });
    return Promise.reject(new Error(`Unhandled URL: ${url}`));
  });
});

afterEach(() => {
  act(() => toast.remove());
  vi.restoreAllMocks();
});

async function renderPage() {
  render(
    <MemoryRouter>
      <AppToaster />
      <InviteMembers />
    </MemoryRouter>
  );
  await screen.findByText("Sam Host");
}
const select = (name) => screen.getByLabelText(`Role for ${name}`);
const choose = (name, role) => fireEvent.change(select(name), { target: { value: role } });
const patchesFor = (id) => api.patch.mock.calls.filter(([url]) => url === `${USERS}/${id}`);
const visible = (text) => screen.queryAllByText(text);

describe("one change at a time per member", () => {
  it("three rapid attempts on the same member send exactly one PATCH", async () => {
    const first = deferred();
    api.patch.mockReturnValueOnce(first.promise);
    await renderPage();

    // All three inside ONE act: React has not re-rendered (the select is not disabled yet),
    // so only the synchronous per-member guard can stop the second and third.
    act(() => {
      choose("Sam Host", "speaker");
      choose("Sam Host", "viewer");
      choose("Sam Host", "speaker");
    });
    expect(api.patch).toHaveBeenCalledTimes(1);
    expect(api.patch).toHaveBeenCalledWith(`${USERS}/u2`, { role: "speaker" });
    expect(select("Sam Host")).toBeDisabled();

    // And while it is pending, further attempts are ignored too.
    choose("Sam Host", "viewer");
    expect(api.patch).toHaveBeenCalledTimes(1);

    server[1].role = "speaker";
    await act(async () => first.resolve({ data: { ...server[1] } }));
    await waitFor(() => expect(select("Sam Host")).not.toBeDisabled());
    expect(select("Sam Host").value).toBe("speaker");
    expect(api.patch).toHaveBeenCalledTimes(1);
  });

  it("a second member is independent: B finishing never unlocks A, and each settles on its own", async () => {
    const a = deferred();
    api.patch.mockImplementation((url, body) => {
      if (url === `${USERS}/u2`) return a.promise;
      server[2].role = body.role;                 // the server applies B's change
      return Promise.resolve({ data: { ...server[2] } });
    });
    await renderPage();

    choose("Sam Host", "speaker");               // A: held open
    choose("Vic Viewer", "host");                // B: answers at once
    expect(api.patch).toHaveBeenCalledTimes(2);
    expect(select("Sam Host")).toBeDisabled();

    await waitFor(() => expect(notify.success).toHaveBeenCalledWith("Vic Viewer is now Host", { id: "member-role:u3" }));
    await waitFor(() => expect(select("Vic Viewer")).not.toBeDisabled());
    expect(select("Vic Viewer").value).toBe("host");
    expect(select("Sam Host")).toBeDisabled();   // B's completion did not release A
    expect(select("Sam Host").value).toBe("speaker");
    choose("Sam Host", "viewer");                // still locked
    expect(patchesFor("u2")).toHaveLength(1);

    await act(async () => a.reject(httpError(503, UNAVAILABLE)));
    await waitFor(() => expect(select("Sam Host")).not.toBeDisabled());
    expect(select("Sam Host").value).toBe("host"); // A rolled back alone
    expect(select("Vic Viewer").value).toBe("host"); // B kept its confirmed role
    expect(notify.error).toHaveBeenCalledTimes(1);
    expect(notify.error).toHaveBeenCalledWith(UNAVAILABLE, { id: "member-role:u2" });
  });
});

describe("duplicate toasts", () => {
  it("the same failure three times shows ONE visible error toast", async () => {
    api.patch.mockRejectedValue(httpError(503, UNAVAILABLE));
    await renderPage();

    for (let i = 0; i < 3; i += 1) {
      choose("Sam Host", "speaker");
      await waitFor(() => expect(notify.error).toHaveBeenCalledTimes(i + 1));
      await waitFor(() => expect(select("Sam Host")).not.toBeDisabled());
    }
    expect(api.patch).toHaveBeenCalledTimes(3);
    expect(await screen.findByText(UNAVAILABLE)).toBeInTheDocument();
    expect(visible(UNAVAILABLE)).toHaveLength(1);
  });

  it("self-demotion: refused by the server, one toast however often it is tried, role stays Admin", async () => {
    api.patch.mockRejectedValue(httpError(400, SELF));
    await renderPage();

    choose("Radha Admin", "host");
    await waitFor(() => expect(notify.error).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(select("Radha Admin")).not.toBeDisabled());
    choose("Radha Admin", "viewer");
    await waitFor(() => expect(notify.error).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(select("Radha Admin")).not.toBeDisabled());

    expect(notify.error).toHaveBeenNthCalledWith(1, "You cannot demote or deactivate yourself", { id: "member-role:self-change-forbidden" });
    expect(notify.error).toHaveBeenNthCalledWith(2, "You cannot demote or deactivate yourself", { id: "member-role:self-change-forbidden" });
    expect(await screen.findByText("You cannot demote or deactivate yourself")).toBeInTheDocument();
    expect(visible("You cannot demote or deactivate yourself")).toHaveLength(1);
    expect(notify.success).not.toHaveBeenCalled();
    expect(select("Radha Admin").value).toBe("org_admin");
  });
});

describe("same role, success and rollback", () => {
  it("choosing the role a member already has sends nothing", async () => {
    await renderPage();
    choose("Sam Host", "host");
    choose("Vic Viewer", "viewer");
    expect(api.patch).not.toHaveBeenCalled();
    expect(select("Sam Host")).not.toBeDisabled();
  });

  it("success: the new role sticks with no flicker back, and fresh server data wins afterwards", async () => {
    api.patch.mockResolvedValueOnce({ data: { id: "u2", role: "speaker" } });
    await renderPage();
    gate = deferred();                              // hold the post-save reload open

    choose("Sam Host", "speaker");
    await waitFor(() => expect(notify.success).toHaveBeenCalledWith("Sam Host is now Speaker", { id: "member-role:u2" }));
    await waitFor(() => expect(select("Sam Host")).not.toBeDisabled());
    // The reload has not landed and the old list still says "host": the confirmed role shows.
    expect(select("Sam Host").value).toBe("speaker");
    // Re-choosing the now-confirmed role is a no-op.
    choose("Sam Host", "speaker");
    expect(api.patch).toHaveBeenCalledTimes(1);

    // The server moved on in the meantime (someone else made Sam a Viewer): that wins.
    server[1].role = "viewer";
    await act(async () => gate.resolve());
    await waitFor(() => expect(select("Sam Host").value).toBe("viewer"));
  });

  it("failure rolls back to the last CONFIRMED role, not the role the page first loaded", async () => {
    api.patch
      .mockResolvedValueOnce({ data: { id: "u2", role: "speaker" } })
      .mockRejectedValueOnce(httpError(503, UNAVAILABLE));
    await renderPage();
    gate = deferred();                              // keep the stale "host" list on screen

    choose("Sam Host", "speaker");
    await waitFor(() => expect(select("Sam Host")).not.toBeDisabled());
    expect(select("Sam Host").value).toBe("speaker");

    choose("Sam Host", "viewer");
    expect(select("Sam Host").value).toBe("viewer"); // optimistic while pending
    await waitFor(() => expect(notify.error).toHaveBeenCalledWith(UNAVAILABLE, { id: "member-role:u2" }));
    await waitFor(() => expect(select("Sam Host")).not.toBeDisabled());
    expect(select("Sam Host").value).toBe("speaker");
  });

  it("a rollback leaves no permanent override: a later server change still shows", async () => {
    api.patch
      .mockRejectedValueOnce(httpError(503, UNAVAILABLE))
      .mockResolvedValueOnce({ data: { id: "u3", role: "speaker" } });
    await renderPage();

    choose("Sam Host", "speaker");
    await waitFor(() => expect(select("Sam Host")).not.toBeDisabled());
    expect(select("Sam Host").value).toBe("host");

    // Elsewhere, Sam becomes a Viewer; the next reload (triggered by another member's
    // successful change) must show it.
    server[1].role = "viewer";
    server[2].role = "speaker";
    choose("Vic Viewer", "speaker");
    await waitFor(() => expect(select("Sam Host").value).toBe("viewer"));
    expect(select("Vic Viewer").value).toBe("speaker");
  });
});
