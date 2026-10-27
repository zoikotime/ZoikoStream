// BUG-009: host emails sent during event creation went missing and nobody was told. The
// server now sends them inside PATCH /events/{id}/hosts and returns `email_sent` per host;
// the form warns about the ones that did not go out, and only about those.
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api", () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  errMsg: (e, fallback) => e?.response?.data?.detail ?? fallback ?? "Something went wrong.",
}));
vi.mock("../../ui/Toast", () => ({
  notify: { error: vi.fn(), success: vi.fn(), warning: vi.fn(), alert: vi.fn() },
}));

const CREATOR = { id: "creator-1", full_name: "Vihari", email: "vihari@example.com", role: "org_admin" };
const NANI = { id: "host-2", full_name: "Nani", email: "nani@example.com", role: "host" };
const RAVI = { id: "host-3", full_name: "Ravi", email: "ravi@example.com", role: "host" };

vi.mock("../../auth/AuthContext", () => ({
  useAuth: () => ({ user: CREATOR, logout: vi.fn() }),
}));

import api from "../../api";
import { notify } from "../../ui/Toast";
import { ThemeProvider } from "../../theme/ThemeContext";
import CreateEventModal from "./CreateEventModal";

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.get).mockResolvedValue({ data: { items: [CREATOR, NANI, RAVI], total: 3, page: 1, page_size: 100 } });
  vi.mocked(api.post).mockResolvedValue({ data: { id: "evt-1", title: "Launch" } });
});

const renderModal = () =>
  render(
    <ThemeProvider>
      <MemoryRouter>
        <CreateEventModal open onClose={vi.fn()} onCreated={vi.fn()} />
      </MemoryRouter>
    </ThemeProvider>,
  );

async function createWithHosts(user, people) {
  renderModal();
  await user.type(screen.getByPlaceholderText(/Q3 Product Launch/i), "Launch");
  const dialog = screen.getByRole("dialog");
  await user.type(dialog.querySelector('input[type="date"]'), `${new Date().getFullYear() + 2}-06-15`);
  await user.type(dialog.querySelectorAll('input[type="time"]')[0], "10:00");
  for (const p of people) await user.click(await screen.findByRole("button", { name: new RegExp(p.full_name, "i") }));
  await user.click(screen.getByRole("button", { name: /schedule event/i }));
  await waitFor(() => expect(notify.success).toHaveBeenCalled());
}

describe("host assignment emails", () => {
  it("says nothing extra when every email went out", async () => {
    vi.mocked(api.patch).mockResolvedValue({ data: [{ ...NANI, email_sent: true }, { ...RAVI, email_sent: true }] });
    await createWithHosts(userEvent.setup(), [NANI, RAVI]);
    expect(notify.warning).not.toHaveBeenCalled();
  });

  it("names the host whose email could not be sent — and only them", async () => {
    vi.mocked(api.patch).mockResolvedValue({ data: [{ ...NANI, email_sent: false }, { ...RAVI, email_sent: true }] });
    await createWithHosts(userEvent.setup(), [NANI, RAVI]);
    expect(notify.warning).toHaveBeenCalledTimes(1);
    const msg = vi.mocked(notify.warning).mock.calls[0][0];
    expect(msg).toMatch(/^Nani is assigned, but the email couldn't be sent/);
    expect(msg).not.toMatch(/Ravi/);
  });

  it("names every host whose email failed", async () => {
    vi.mocked(api.patch).mockResolvedValue({ data: [{ ...NANI, email_sent: false }, { ...RAVI, email_sent: false }] });
    await createWithHosts(userEvent.setup(), [NANI, RAVI]);
    expect(vi.mocked(notify.warning).mock.calls[0][0]).toMatch(/^Nani, Ravi are assigned/);
  });

  it("does not warn about hosts nothing was sent to (already assigned: email_sent null)", async () => {
    vi.mocked(api.patch).mockResolvedValue({ data: [{ ...NANI, email_sent: null }] });
    await createWithHosts(userEvent.setup(), [NANI]);
    expect(notify.warning).not.toHaveBeenCalled();
  });

  it("tolerates an older server that returns no email_sent at all", async () => {
    vi.mocked(api.patch).mockResolvedValue({ data: [{ ...NANI }] });
    await createWithHosts(userEvent.setup(), [NANI]);
    expect(notify.warning).not.toHaveBeenCalled();
  });
});
