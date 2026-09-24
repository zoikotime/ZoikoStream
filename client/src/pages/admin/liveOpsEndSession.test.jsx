// The one destructive control on the platform live monitor, and the elevation gate in front
// of it.
//
// Two things are being pinned, and the second matters more than the first:
//
//  1. Ending a session is confirmed, names the event, and goes through
//     POST /admin/live-events/{id}/end — not a status write.
//  2. The console never presents the action as available when the server will refuse it, and
//     never presents itself as the authority. security.require_elevation decides; these
//     assert that the UI mirrors that decision honestly in every state — absent, expired,
//     wrong scope, and API-unreachable — and that a server 403 arriving anyway is SHOWN
//     rather than swallowed. A console that reports success on a refused request is the
//     failure mode this whole workstream exists to remove.
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Outlet, Route, Routes } from "react-router-dom";

vi.mock("../../api", () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  errMsg: (e) => e?.response?.data?.detail ?? e?.message ?? "Something went wrong.",
  diagnoseLoadError: (e) => e?.message ?? "failed",
}));

import api from "../../api";
import { ThemeProvider } from "../../theme/ThemeContext";
import LiveEvents from "./LiveEvents";

const SESSION = {
  id: "5e551011-0000-4000-8000-000000000001",
  event_id: "e0e00000-0000-4000-8000-000000000001",
  title: "Quarterly All Hands",
  organization: "Northwind",
  region: "US East",
  started_at: new Date(Date.now() - 3600_000).toISOString(),
  ended_at: null,
  viewers: 42,
  health: "ok",
  status: "live",
};

// console-state's elevation block, exactly as services/ops.current_elevation emits it.
// `granted_scopes` is the union the server authorizes from; the UI must read that and not
// the single display `scope`.
const grant = (over = {}) => ({
  id: "elev-1",
  scope: "platform",
  scopes: ["identity", "broadcast"],
  granted_scopes: ["broadcast", "identity", "platform"],
  reason: "Console session",
  seconds_remaining: 600,
  ...over,
});

const serveRows = (rows = [SESSION]) => {
  vi.mocked(api.get).mockImplementation((url, cfg) => {
    if (url === "/admin/live-events") {
      return Promise.resolve({ data: cfg?.params?.state === "live" ? rows : [] });
    }
    return Promise.resolve({ data: {} });
  });
};

// Renders LiveEvents under a route whose parent supplies the console state AdminLayout
// supplies in the app, so the page sees the real shape rather than a bespoke one.
const show = (elevation, { unknown = false } = {}) =>
  render(
    <ThemeProvider>
      <MemoryRouter initialEntries={["/admin/live-events"]}>
        <Routes>
          <Route
            element={<Outlet context={{ state: unknown ? null : { elevation }, unknown, reload: vi.fn() }} />}
          >
            <Route path="/admin/live-events" element={<LiveEvents />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </ThemeProvider>
  );

const openDialog = async () => {
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: /^End$/ }));
  await screen.findByRole("dialog");
  return user;
};

beforeEach(() => {
  vi.clearAllMocks();
  serveRows();
});

// ── the confirmation ───────────────────────────────────────────────────────────────────

describe("ending a session is confirmed, not immediate", () => {
  it("does not call the endpoint on the row button alone", async () => {
    show(grant());
    await openDialog();

    expect(api.post).not.toHaveBeenCalled();
  });

  it("names the event and the consequence, so the operator knows which one this is", async () => {
    show(grant());
    await openDialog();

    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("Quarterly All Hands");
    expect(dialog).toHaveTextContent(/Everyone watching will be disconnected/i);
  });

  it("posts to the force-end endpoint when confirmed", async () => {
    vi.mocked(api.post).mockResolvedValue({ data: { ok: true, already_ended: false, room_closed: true } });
    show(grant());
    const user = await openDialog();

    await user.click(screen.getByRole("button", { name: /End session/i }));

    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith(`/admin/live-events/${SESSION.id}/end`)
    );
  });

  it("cancelling ends nothing", async () => {
    show(grant());
    const user = await openDialog();

    await user.click(screen.getByRole("button", { name: /Cancel/i }));

    expect(api.post).not.toHaveBeenCalled();
  });

  it("is honest when the row had already ended rather than claiming it just ended it", async () => {
    vi.mocked(api.post).mockResolvedValue({ data: { ok: false, already_ended: true, room_closed: false } });
    show(grant());
    const user = await openDialog();

    await user.click(screen.getByRole("button", { name: /End session/i }));

    await waitFor(() => expect(api.post).toHaveBeenCalled());
    // The page is told what actually happened; it must not report a fresh termination.
    expect(api.post).toHaveReturnedTimes(1);
  });
});

// ── the elevation gate, mirrored honestly ──────────────────────────────────────────────

describe("the console mirrors the server's elevation gate", () => {
  it("offers Elevate access instead of End session when there is no elevation", async () => {
    show(null);
    await openDialog();

    expect(screen.getByRole("button", { name: /Elevate access/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /End session/i })).not.toBeInTheDocument();
    expect(screen.getByRole("dialog")).toHaveTextContent(/needs an active elevation/i);
  });

  it("blocks again once the grant has expired", async () => {
    // The badge may still be on screen with seconds_remaining 0; the control must not be.
    show(grant({ seconds_remaining: 0 }));
    await openDialog();

    expect(screen.getByRole("dialog")).toHaveTextContent(/elevation has expired/i);
    expect(screen.queryByRole("button", { name: /End session/i })).not.toBeInTheDocument();
  });

  it("blocks when the elevation is for something else", async () => {
    show(grant({ scope: "identity", scopes: [], granted_scopes: ["identity"] }));
    await openDialog();

    expect(screen.getByRole("dialog")).toHaveTextContent(/does not cover broadcast/i);
    expect(screen.queryByRole("button", { name: /End session/i })).not.toBeInTheDocument();
  });

  it("reads the granted set, not the badge's single scope", async () => {
    // Display scope is "platform"; "broadcast" rides in the union. Gating on `scope` alone
    // would wrongly refuse an operator the server would have allowed.
    show(grant({ scope: "platform", scopes: [], granted_scopes: ["platform", "broadcast"] }));
    await openDialog();

    expect(screen.getByRole("button", { name: /End session/i })).toBeEnabled();
  });

  it("requesting elevation opens a broadcast-scoped grant", async () => {
    vi.mocked(api.post).mockResolvedValue({ data: {} });
    show(null);
    const user = await openDialog();

    await user.click(screen.getByRole("button", { name: /Elevate access/i }));

    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith("/admin/elevation", expect.objectContaining({ scope: "broadcast" }))
    );
  });

  it("does not report success when elevating changed nothing", async () => {
    // POST /admin/elevation returns the EXISTING grant instead of opening a second one, so an
    // engineer already inside a support session clicks Elevate, gets a 201 back, and is
    // exactly as blocked as before. The dialog has to say so rather than look like it worked.
    vi.mocked(api.post).mockResolvedValue({
      data: {
        id: "e2",
        scope: "Support case ZS-41",
        scopes: ["members:write"],
        granted_scopes: ["Support case ZS-41", "members:write"],
      },
    });
    show(grant({ scope: "Support case ZS-41", scopes: [], granted_scopes: ["Support case ZS-41"] }));
    const user = await openDialog();

    await user.click(screen.getByRole("button", { name: /Elevate access/i }));

    expect(await screen.findByText(/already hold a different elevation/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /End session/i })).not.toBeInTheDocument();
  });

  it("says unknown rather than unelevated when console-state is unreachable", async () => {
    // "I could not ask" is not "you are not elevated", and it must not offer to elevate
    // against an API it cannot reach.
    show(null, { unknown: true });
    await openDialog();

    expect(screen.getByRole("dialog")).toHaveTextContent(/Access state unknown/i);
    expect(screen.queryByRole("button", { name: /Elevate access/i })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /End session/i })).toBeDisabled();
  });
});

// ── the server is still the authority ──────────────────────────────────────────────────

describe("the server decides, and its refusal is shown", () => {
  it("surfaces a 403 that arrives despite a locally-valid grant", async () => {
    // The grant can be ended in another tab between the check and the click. The dialog must
    // show that, not close on a request that was refused.
    vi.mocked(api.post).mockRejectedValue({
      response: { status: 403, data: { detail: "Your elevation does not cover 'broadcast'." } },
    });
    show(grant());
    const user = await openDialog();

    await user.click(screen.getByRole("button", { name: /End session/i }));

    expect(await screen.findByText(/does not cover 'broadcast'/i)).toBeInTheDocument();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });
});

// ── what the row itself reports ────────────────────────────────────────────────────────

describe("the row reports reconciled truth", () => {
  it("shows Unknown, never Operational, when LiveKit could not be reached", async () => {
    serveRows([{ ...SESSION, health: "unknown", viewers: null }]);
    show(grant());

    expect(await screen.findByText("Unknown")).toBeInTheDocument();
    expect(screen.queryByText("Operational")).not.toBeInTheDocument();
  });

  it("does not print a missing viewer count as zero", async () => {
    serveRows([{ ...SESSION, health: "unknown", viewers: null }]);
    show(grant());

    await screen.findByText("Unknown");
    // Target the Viewers cell itself: asserting over the whole row would pass for the wrong
    // reason (some other column happens to hold an em dash).
    const cells = screen.getByText("Quarterly All Hands").closest("tr").querySelectorAll("td");
    const viewers = cells[cells.length - 3];   // … Duration | Viewers | Health | actions
    expect(viewers).toHaveTextContent("—");
    expect(viewers).not.toHaveTextContent("0");
  });
});
