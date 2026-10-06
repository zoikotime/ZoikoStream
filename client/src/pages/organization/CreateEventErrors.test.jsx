// What the organiser is told when Create Event fails — with the REAL errMsg, so these tests
// see exactly what reaches the toast.
//
// The bug this pins: a server error used to come back without CORS headers, the browser hid
// it, and the page could only say "Network Error". The API now answers every failure with
// readable JSON, and the modal must surface that message — "Network Error" is reserved for
// the case where genuinely no response arrived.
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api", async (importOriginal) => {
  const real = await importOriginal();
  return { ...real, default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() } };
});
vi.mock("../../ui/Toast", () => ({
  notify: { error: vi.fn(), success: vi.fn(), alert: vi.fn(), warning: vi.fn() },
}));
vi.mock("../../auth/AuthContext", () => ({
  useAuth: () => ({ user: { id: "creator-1", full_name: "Vihari", role: "org_admin" }, logout: vi.fn() }),
}));

import api from "../../api";
import { notify } from "../../ui/Toast";
import { ThemeProvider } from "../../theme/ThemeContext";
import CreateEventModal from "./CreateEventModal";

const HOST = { id: "host-2", full_name: "Nani", email: "nani@example.com", role: "host" };
const onCreated = vi.fn();
const onClose = vi.fn();

// An axios-shaped error WITH a response (the server answered) or without one (it did not).
const answered = (status, detail) =>
  Object.assign(new Error(`Request failed with status code ${status}`), { response: { status, data: { detail } } });
const unreachable = () => Object.assign(new Error("Network Error"), { code: "ERR_NETWORK", request: {} });

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.get).mockResolvedValue({ data: { items: [HOST], total: 1, page: 1, page_size: 100 } });
  vi.mocked(api.post).mockResolvedValue({ data: { id: "evt-1", title: "Launch" } });
  vi.mocked(api.patch).mockResolvedValue({ data: [] });
});

function renderModal() {
  return render(
    <ThemeProvider>
      <MemoryRouter>
        <CreateEventModal open onClose={onClose} onCreated={onCreated} />
      </MemoryRouter>
    </ThemeProvider>
  );
}

// Scheduling needs a real future start (the server refuses a Scheduled event without one,
// crud.event.schedule_error), so every scheduled create in this file picks a date and time.
const FUTURE_DATE = `${new Date().getFullYear() + 2}-06-15`;

async function fill(user) {
  await user.type(screen.getByPlaceholderText(/Q3 Product Launch/i), "Launch");
  const dialog = screen.getByRole("dialog");
  await user.type(dialog.querySelector('input[type="date"]'), FUTURE_DATE);
  await user.type(dialog.querySelectorAll('input[type="time"]')[0], "10:00");
}
const schedule = (user) => user.click(screen.getByRole("button", { name: /schedule event/i }));
const saveDraft = (user) => user.click(screen.getByRole("button", { name: /save draft/i }));

describe("a failed create says what the server said", () => {
  it("shows the schema-out-of-date message instead of Network Error", async () => {
    // main.py's ProgrammingError handler, verbatim.
    const message = "The database schema is not up to date, so this request could not be completed. Please try again later or contact support.";
    vi.mocked(api.post).mockRejectedValue(answered(503, { code: "schema_out_of_date", message }));
    const user = userEvent.setup();
    renderModal();
    await fill(user);
    await schedule(user);
    await waitFor(() => expect(notify.error).toHaveBeenCalledWith(message));
    expect(notify.error).not.toHaveBeenCalledWith(expect.stringMatching(/network error/i));
    expect(onCreated).not.toHaveBeenCalled();                   // nothing was created
  });

  it("shows the readable server-error message for a draft too", async () => {
    // main.py's ReadableServerErrors body, verbatim.
    const message = "Something went wrong on our side, so this request could not be completed. Please try again.";
    vi.mocked(api.post).mockRejectedValue(answered(500, { code: "server_error", message }));
    const user = userEvent.setup();
    renderModal();
    await fill(user);
    await saveDraft(user);
    await waitFor(() => expect(notify.error).toHaveBeenCalledWith(message));
  });

  it("shows a validation message as the server phrased it", async () => {
    vi.mocked(api.post).mockRejectedValue(answered(422, [{ loc: ["body", "title"], msg: "String should have at most 200 characters" }]));
    const user = userEvent.setup();
    renderModal();
    await fill(user);
    await schedule(user);
    await waitFor(() => expect(notify.error).toHaveBeenCalledWith("String should have at most 200 characters"));
  });

  it("says Network Error only when no response arrived at all — and warns it may exist", async () => {
    vi.mocked(api.post).mockRejectedValue(unreachable());
    const user = userEvent.setup();
    renderModal();
    await fill(user);
    await schedule(user);
    await waitFor(() => expect(notify.error).toHaveBeenCalledWith(expect.stringMatching(/^Network Error — .*couldn't confirm whether the event was saved/)));
    // The outcome is unknown, so the list is refreshed for the organiser to check first.
    expect(onCreated).toHaveBeenCalledTimes(1);
  });
});

describe("the event was created but the hosts were not", () => {
  it("says the event exists, refreshes the list and closes — no duplicate on retry", async () => {
    vi.mocked(api.patch).mockRejectedValue(answered(403, "You may only assign members of your organization"));
    const user = userEvent.setup();
    renderModal();
    await fill(user);
    await user.click(await screen.findByRole("button", { name: /Nani/i }));
    await schedule(user);
    await waitFor(() => expect(notify.error).toHaveBeenCalledWith(
      '"Launch" was created, but its hosts could not be assigned: You may only assign members of your organization'));
    expect(api.post).toHaveBeenCalledTimes(1);
    expect(onCreated).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalled();
    expect(notify.success).not.toHaveBeenCalled();
  });

  it("assigns the selected hosts after a successful create", async () => {
    const user = userEvent.setup();
    renderModal();
    await fill(user);
    await user.click(await screen.findByRole("button", { name: /Nani/i }));
    await schedule(user);
    await waitFor(() => expect(api.patch).toHaveBeenCalledWith("/events/evt-1/hosts", { user_ids: ["host-2"] }));
    expect(notify.success).toHaveBeenCalledWith('"Launch" scheduled');
    expect(api.post.mock.calls[0][1]).toMatchObject({ title: "Launch", status: "scheduled" });
  });
});
