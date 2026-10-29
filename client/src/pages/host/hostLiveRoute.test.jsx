// The Producer Console's URL says whether it is on air.
//
// Production report: an active live session sat at /host/dashboard?event=<id>. The console now
// lives at /host/live/<id> while its broadcast is live (or paused), and at
// /host/dashboard?event=<id> otherwise — preparation, after it ends, and every existing link,
// email and bookmark (EventLiveControl, MyEvents, the create-event tab), which keep working
// and are moved to the live URL once the console knows it is on air.
//
// The route table here mirrors App.jsx: the real EventConsoleRoute guard over both routes,
// rendering one console component. The console is a probe that counts its own mounts and runs
// the real useHostConsoleUrl, so "the URL changes without remounting" — without dropping the
// socket and the LiveKit room — is measured, not assumed.
import { act, render, screen, waitFor } from "@testing-library/react";
import { useEffect, useState } from "react";
import { MemoryRouter, Route, Routes, useLocation, useNavigationType } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api", () => ({
  default: { get: vi.fn() },
  errMsg: (e) => e?.message ?? "Something went wrong.",
}));
// One stable object, as AuthContext's state is: a fresh `user` per render would make the guard
// re-check access on every navigation, which the real context never does.
const AUTH = vi.hoisted(() => ({ user: { id: "u1", role: "host" }, loading: false, unreachable: false }));
vi.mock("../../auth/AuthContext", () => ({ useAuth: () => AUTH }));
vi.mock("../../ui/Toast", () => ({ notify: { error: vi.fn(), success: vi.fn() } }));

import api from "../../api";
import EventConsoleRoute from "../../components/EventConsoleRoute";
import { consoleFor, eventIdFrom, resolvePostLogin } from "../../auth/destination";
import { eventIdFrom as guideEventIdFrom } from "../../operator-guide/eligibility";
import useConsoleEventId, { useHostConsoleUrl } from "../../hooks/useConsoleEventId";

const E1 = "11111111-1111-4111-8111-111111111111";
const E2 = "22222222-2222-4222-8222-222222222222";

let mounts = 0;
let startOnAir = false;
let ready = true;
const seen = { location: null, type: null };

function ConsoleProbe() {
  const eventId = useConsoleEventId();
  const [onAir, setOnAir] = useState(startOnAir);
  useEffect(() => { mounts += 1; }, []);
  useHostConsoleUrl({ ready, onAir });
  return (
    <div>
      <h1>console for {eventId}</h1>
      <button type="button" onClick={() => setOnAir((v) => !v)}>toggle on air</button>
    </div>
  );
}
function Spy() {
  const location = useLocation();
  const type = useNavigationType();
  useEffect(() => { seen.location = location; seen.type = type; });
  return null;
}
const url = () => `${seen.location.pathname}${seen.location.search}`;

function renderAt(path) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Spy />
      <Routes>
        <Route element={<EventConsoleRoute capability={["can_host", "can_moderate"]} />}>
          <Route path="/host/dashboard" element={<ConsoleProbe />} />
          <Route path="/host/live/:eventId" element={<ConsoleProbe />} />
        </Route>
        <Route path="*" element={<h1>account home</h1>} />
      </Routes>
    </MemoryRouter>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mounts = 0;
  startOnAir = false;
  ready = true;
  vi.mocked(api.get).mockImplementation((path) =>
    Promise.resolve({ data: path.includes(E1) ? { can_host: true } : { can_host: false, can_moderate: false } }));
});

describe("the live URL", () => {
  it("opens the console for the event in the path, after the same assignment check", async () => {
    startOnAir = true;
    renderAt(`/host/live/${E1}`);
    expect(await screen.findByRole("heading", { name: `console for ${E1}` })).toBeInTheDocument();
    expect(api.get).toHaveBeenCalledWith(`/events/${E1}/assignment`);
    expect(url()).toBe(`/host/live/${E1}`);
  });

  it("refuses an event the user is not assigned to, as the dashboard URL does", async () => {
    startOnAir = true;
    renderAt(`/host/live/${E2}`);
    expect(await screen.findByRole("heading", { name: "account home" })).toBeInTheDocument();
    expect(screen.queryByText(/console for/)).toBeNull();
  });

  it("an existing dashboard link to an on-air event moves to the live URL, replacing it", async () => {
    startOnAir = true;
    renderAt(`/host/dashboard?event=${E1}`);
    await screen.findByRole("heading", { name: `console for ${E1}` });
    await waitFor(() => expect(url()).toBe(`/host/live/${E1}`));
    expect(seen.type).toBe("REPLACE");
    expect(mounts).toBe(1);                 // same console instance: nothing reconnected
  });

  it("an event that is not on air does not sit on the live URL", async () => {
    startOnAir = false;
    renderAt(`/host/live/${E1}`);
    await screen.findByRole("heading", { name: `console for ${E1}` });
    await waitFor(() => expect(url()).toBe(`/host/dashboard?event=${E1}`));
    expect(mounts).toBe(1);
  });

  it("follows the broadcast both ways without remounting the console", async () => {
    startOnAir = false;
    renderAt(`/host/dashboard?event=${E1}`);
    await screen.findByRole("heading", { name: `console for ${E1}` });
    expect(url()).toBe(`/host/dashboard?event=${E1}`);

    act(() => screen.getByRole("button", { name: "toggle on air" }).click());   // goes live
    await waitFor(() => expect(url()).toBe(`/host/live/${E1}`));
    act(() => screen.getByRole("button", { name: "toggle on air" }).click());   // ends
    await waitFor(() => expect(url()).toBe(`/host/dashboard?event=${E1}`));
    expect(screen.getByRole("heading", { name: `console for ${E1}` })).toBeInTheDocument();
    expect(mounts).toBe(1);
  });

  it("keeps other query parameters", async () => {
    startOnAir = true;
    renderAt(`/host/dashboard?event=${E1}&tab=polls`);
    await screen.findByRole("heading", { name: `console for ${E1}` });
    await waitFor(() => expect(url()).toBe(`/host/live/${E1}?tab=polls`));
  });

  it("waits for the live snapshot before deciding", async () => {
    startOnAir = true;
    ready = false;
    renderAt(`/host/dashboard?event=${E1}`);
    await screen.findByRole("heading", { name: `console for ${E1}` });
    await act(async () => {});
    expect(url()).toBe(`/host/dashboard?event=${E1}`);
  });
});

describe("post-login deep links and the Operator Guide know the live URL", () => {
  it("recognises /host/live/<id> as the host console, with its event", () => {
    expect(consoleFor(`/host/live/${E1}`)).toMatchObject({ path: "/host/live", capability: "can_host" });
    expect(consoleFor("/host/dashboard")).toEqual({ path: "/host/dashboard", capability: "can_host" });
    expect(consoleFor("/host/live")).toBeNull();
    expect(eventIdFrom("", `/host/live/${E1}`)).toBe(E1);
    expect(eventIdFrom(`?event=${E2}`, "/host/dashboard")).toBe(E2);
    expect(guideEventIdFrom(`/host/live/${E1}`, "")).toBe(E1);
  });

  it("honours a saved live link only after the server confirms the assignment", async () => {
    await expect(resolvePostLogin({ role: "host" }, { pathname: `/host/live/${E1}`, search: "" }))
      .resolves.toEqual({ to: `/host/live/${E1}`, reason: null });
    const refused = await resolvePostLogin({ role: "host" }, { pathname: `/host/live/${E2}`, search: "" });
    expect(refused.to).not.toContain("/host/live");
    expect(refused.reason).toMatch(/aren't assigned/);
  });
});
