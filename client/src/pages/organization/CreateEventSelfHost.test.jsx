// Scheduling an event you put yourself on opens the console in a NEW TAB, leaving the
// Organization Console where it was.
//
// The tab is a CONVENIENCE, never a grant: /host/dashboard resolves access on arrival
// through the backend (services/moderation.resolve_ctx), so these cases are about whether a
// tab is opened, where it points, and — just as importantly — when none is opened at all.
//
// The tab is claimed SYNCHRONOUSLY in the click (Chrome only allows window.open while the
// click still holds transient activation, and both API calls here are awaited), then
// pointed at the console once the server confirms. That is why the assertions look for
// window.open("", "_blank") followed by location.replace, rather than one open(url).
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api", () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  errMsg: (e, fallback) => e?.response?.data?.detail ?? fallback ?? "Something went wrong.",
}));

vi.mock("../../ui/Toast", () => ({
  notify: { error: vi.fn(), success: vi.fn(), alert: vi.fn() },
}));

const CREATOR = { id: "creator-uuid-1", full_name: "Vihari", email: "vihari@example.com", role: "org_admin" };
const OTHER = { id: "other-uuid-2", full_name: "Nani", email: "nani@example.com", role: "host" };

vi.mock("../../auth/AuthContext", () => ({
  useAuth: () => ({ user: CREATOR, logout: vi.fn() }),
}));

import api from "../../api";
import { notify } from "../../ui/Toast";
import { ThemeProvider } from "../../theme/ThemeContext";
import CreateEventModal from "./CreateEventModal";

// The id the SERVER returns. Deliberately unlike anything in local state, so a test can tell
// whether the tab used the authoritative value or invented one.
const CREATED_ID = "server-assigned-event-id-9f3";

const MEMBERS = { items: [CREATOR, OTHER], total: 2, page: 1, page_size: 100 };

// Reports the current location, so "the Organization tab never moved" is assertable.
function LocationProbe() {
  const loc = useLocation();
  return <div data-testid="loc">{loc.pathname + loc.search}</div>;
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.get).mockResolvedValue({ data: MEMBERS });
  vi.mocked(api.post).mockResolvedValue({ data: { id: CREATED_ID, title: "Launch" } });
  vi.mocked(api.patch).mockResolvedValue({ data: [] });
});

const renderModal = () =>
  render(
    <ThemeProvider>
      <MemoryRouter initialEntries={["/organization/events"]}>
        <LocationProbe />
        <Routes>
          <Route
            path="/organization/events"
            element={<CreateEventModal open onClose={vi.fn()} onCreated={vi.fn()} />}
          />
          <Route path="/host/dashboard" element={<div>Producer Console</div>} />
        </Routes>
      </MemoryRouter>
    </ThemeProvider>
  );

const where = () => screen.getByTestId("loc").textContent;

// A stand-in for the tab the browser hands back, so a test can see where it was pointed and
// whether it was closed again.
function fakeTab() {
  return {
    opener: {},                       // the component sets this to null
    location: { replace: vi.fn() },
    close: vi.fn(),
  };
}

const pointedAt = (tab) => tab.location.replace.mock.calls[0]?.[0];
const consoleUrl = `${window.location.origin}/host/dashboard?event=${CREATED_ID}`;

async function fillTitle(user) {
  // By placeholder: the modal's <Label>Event Title</Label> carries no htmlFor and the
  // <Input> no id, so the two are not associated and getByLabelText cannot find it. That is
  // a real accessibility gap in the component, noted rather than quietly worked around.
  await user.type(screen.getByPlaceholderText(/Q3 Product Launch/i), "Launch");
}

async function pickHost(user, person) {
  // Each member row IS a button, so this is the real affordance rather than a label.
  const row = await screen.findByRole("button", { name: new RegExp(person.full_name, "i") });
  await user.click(row);
}

const schedule = (user) => user.click(screen.getByRole("button", { name: /schedule event/i }));
const saveDraft = (user) => user.click(screen.getByRole("button", { name: /save draft/i }));

describe("the creator put themselves on the host list", () => {
  it("opens the Producer Console in a NEW TAB", async () => {
    const tab = fakeTab();
    const open = vi.spyOn(window, "open").mockReturnValue(tab);
    const user = userEvent.setup();
    renderModal();
    await fillTitle(user);
    await pickHost(user, CREATOR);
    await schedule(user);

    await waitFor(() => expect(tab.location.replace).toHaveBeenCalled());
    // Claimed blank inside the click, then pointed once the server answered.
    expect(open).toHaveBeenCalledWith("", "_blank");
    expect(pointedAt(tab)).toBe(consoleUrl);
    open.mockRestore();
  });

  it("leaves the Organization tab exactly where it was", async () => {
    const open = vi.spyOn(window, "open").mockReturnValue(fakeTab());
    const user = userEvent.setup();
    renderModal();
    await fillTitle(user);
    await pickHost(user, CREATOR);
    await schedule(user);

    await waitFor(() => expect(api.patch).toHaveBeenCalled());
    // The whole point of the new tab: this one never navigates.
    expect(where()).toBe("/organization/events");
    expect(screen.queryByText("Producer Console")).not.toBeInTheDocument();
    open.mockRestore();
  });

  it("severs the new tab's back-reference to this one", async () => {
    const tab = fakeTab();
    const open = vi.spyOn(window, "open").mockReturnValue(tab);
    const user = userEvent.setup();
    renderModal();
    await fillTitle(user);
    await pickHost(user, CREATOR);
    await schedule(user);

    await waitFor(() => expect(tab.location.replace).toHaveBeenCalled());
    // `noopener` cannot go in the feature string here — it makes window.open return null,
    // which would destroy the handle this strategy needs — so the protection it would have
    // provided is applied directly instead.
    expect(tab.opener).toBeNull();
    open.mockRestore();
  });

  it("uses the id the server returned, not anything held locally", async () => {
    const tab = fakeTab();
    const open = vi.spyOn(window, "open").mockReturnValue(tab);
    const user = userEvent.setup();
    renderModal();
    await fillTitle(user);
    await pickHost(user, CREATOR);
    await schedule(user);

    await waitFor(() => expect(tab.location.replace).toHaveBeenCalled());
    expect(pointedAt(tab)).toContain(CREATED_ID);
    expect(vi.mocked(api.post).mock.calls[0][0]).toBe("/events");
    open.mockRestore();
  });

  it("assigns the host through the existing endpoint", async () => {
    const open = vi.spyOn(window, "open").mockReturnValue(fakeTab());
    const user = userEvent.setup();
    renderModal();
    await fillTitle(user);
    await pickHost(user, CREATOR);
    await schedule(user);

    await waitFor(() => expect(api.patch).toHaveBeenCalledTimes(1));
    const [url, body] = vi.mocked(api.patch).mock.calls[0];
    expect(url).toBe(`/events/${CREATED_ID}/hosts`);
    expect(body.user_ids).toEqual([CREATOR.id]);
    open.mockRestore();
  });

  it("opens exactly ONE tab when another host is selected alongside them", async () => {
    const tab = fakeTab();
    const open = vi.spyOn(window, "open").mockReturnValue(tab);
    const user = userEvent.setup();
    renderModal();
    await fillTitle(user);
    await pickHost(user, CREATOR);
    await pickHost(user, OTHER);
    await schedule(user);

    await waitFor(() => expect(tab.location.replace).toHaveBeenCalled());
    expect(open).toHaveBeenCalledTimes(1);
    expect(tab.location.replace).toHaveBeenCalledTimes(1);
    // The other person is assigned in the same call and gets their own email from the
    // backend; nothing opens in THIS browser for them.
    const [, body] = vi.mocked(api.patch).mock.calls[0];
    expect(new Set(body.user_ids)).toEqual(new Set([CREATOR.id, OTHER.id]));
    open.mockRestore();
  });

  it("says the tab was opened", async () => {
    const open = vi.spyOn(window, "open").mockReturnValue(fakeTab());
    const user = userEvent.setup();
    renderModal();
    await fillTitle(user);
    await pickHost(user, CREATOR);
    await schedule(user);

    await waitFor(() => expect(notify.success).toHaveBeenCalled());
    expect(vi.mocked(notify.success).mock.calls[0][0]).toMatch(/opened in a new tab/i);
    open.mockRestore();
  });
});

describe("when no tab should open", () => {
  it("opens none for Save Draft, even when self-hosting", async () => {
    const open = vi.spyOn(window, "open").mockReturnValue(fakeTab());
    const user = userEvent.setup();
    renderModal();
    await fillTitle(user);
    await pickHost(user, CREATOR);
    await saveDraft(user);

    await waitFor(() => expect(api.post).toHaveBeenCalled());
    expect(vi.mocked(api.post).mock.calls[0][1].status).toBe("draft");
    expect(open).not.toHaveBeenCalled();
    expect(where()).toBe("/organization/events");
    open.mockRestore();
  });

  it("still records the host assignment on a draft", async () => {
    const open = vi.spyOn(window, "open").mockReturnValue(fakeTab());
    const user = userEvent.setup();
    renderModal();
    await fillTitle(user);
    await pickHost(user, CREATOR);
    await saveDraft(user);

    await waitFor(() => expect(api.patch).toHaveBeenCalledTimes(1));
    open.mockRestore();
  });

  it("opens none when somebody else is the only host", async () => {
    const open = vi.spyOn(window, "open").mockReturnValue(fakeTab());
    const user = userEvent.setup();
    renderModal();
    await fillTitle(user);
    await pickHost(user, OTHER);
    await schedule(user);

    await waitFor(() => expect(api.patch).toHaveBeenCalled());
    expect(open).not.toHaveBeenCalled();
    expect(where()).toBe("/organization/events");
    open.mockRestore();
  });

  it("opens none when no host is chosen at all", async () => {
    const open = vi.spyOn(window, "open").mockReturnValue(fakeTab());
    const user = userEvent.setup();
    renderModal();
    await fillTitle(user);
    await schedule(user);

    await waitFor(() => expect(api.post).toHaveBeenCalled());
    expect(api.patch).not.toHaveBeenCalled();
    expect(open).not.toHaveBeenCalled();
    open.mockRestore();
  });
});

describe("a failure leaves no orphan tab", () => {
  it("closes the claimed tab when the event could not be created", async () => {
    vi.mocked(api.post).mockRejectedValueOnce({ response: { status: 400, data: { detail: "nope" } } });
    const tab = fakeTab();
    const open = vi.spyOn(window, "open").mockReturnValue(tab);
    const user = userEvent.setup();
    renderModal();
    await fillTitle(user);
    await pickHost(user, CREATOR);
    await schedule(user);

    await waitFor(() => expect(notify.error).toHaveBeenCalled());
    expect(tab.close).toHaveBeenCalledTimes(1);
    expect(tab.location.replace).not.toHaveBeenCalled();
    expect(where()).toBe("/organization/events");
    open.mockRestore();
  });

  it("closes it when the host assignment failed", async () => {
    // The event exists but the creator is not on it — pointing a tab at a console they may
    // not hold is the one case where the tab would outrun the grant.
    vi.mocked(api.patch).mockRejectedValueOnce({ response: { status: 400, data: { detail: "nope" } } });
    const tab = fakeTab();
    const open = vi.spyOn(window, "open").mockReturnValue(tab);
    const user = userEvent.setup();
    renderModal();
    await fillTitle(user);
    await pickHost(user, CREATOR);
    await schedule(user);

    await waitFor(() => expect(notify.error).toHaveBeenCalled());
    expect(tab.close).toHaveBeenCalledTimes(1);
    expect(tab.location.replace).not.toHaveBeenCalled();
    open.mockRestore();
  });
});

describe("when the popup blocker wins", () => {
  it("falls back to a direct open with noopener,noreferrer", async () => {
    // The pre-claim returned null (blocked). A direct attempt can still land when the
    // requests were fast enough to stay inside the click's activation window, and no handle
    // is needed there — so the flags can be used properly.
    const open = vi.spyOn(window, "open").mockReturnValue(null);
    const user = userEvent.setup();
    renderModal();
    await fillTitle(user);
    await pickHost(user, CREATOR);
    await schedule(user);

    await waitFor(() => expect(open).toHaveBeenCalledTimes(2));
    expect(open.mock.calls[1]).toEqual([consoleUrl, "_blank", "noopener,noreferrer"]);
    open.mockRestore();
  });

  it("does not claim a tab was opened when none was", async () => {
    const open = vi.spyOn(window, "open").mockReturnValue(null);
    const user = userEvent.setup();
    renderModal();
    await fillTitle(user);
    await pickHost(user, CREATOR);
    await schedule(user);

    await waitFor(() => expect(notify.success).toHaveBeenCalled());
    const message = vi.mocked(notify.success).mock.calls[0][0];
    expect(message).not.toMatch(/opened in a new tab/i);
    expect(message).toMatch(/allow pop-ups/i);
    open.mockRestore();
  });
});
