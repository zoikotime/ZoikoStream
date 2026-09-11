// The Create Event modal, after the Registration section was removed.
//
// Two things are worth pinning and they pull in opposite directions: the section really is
// gone from the form, AND the three fields it owned are absent from the POST body rather
// than sent as hardcoded falsy values. Sending `registration_required: false` would look
// identical on screen while quietly making the client, not the server, the source of that
// default.
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api", () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  errMsg: (e) => e?.message ?? "Something went wrong.",
}));

vi.mock("../../ui/Toast", () => ({
  notify: { error: vi.fn(), success: vi.fn(), alert: vi.fn() },
}));

import api from "../../api";
import { notify } from "../../ui/Toast";
import { ThemeProvider } from "../../theme/ThemeContext";
import CreateEventModal from "./CreateEventModal";

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.get).mockResolvedValue({ data: { items: [] } });
  vi.mocked(api.post).mockResolvedValue({ data: { id: "evt-1", title: "Launch" } });
  vi.mocked(api.patch).mockResolvedValue({ data: {} });
});

function open() {
  return render(
    <ThemeProvider>
      <MemoryRouter>
        <CreateEventModal open onClose={vi.fn()} onCreated={vi.fn()} />
      </MemoryRouter>
    </ThemeProvider>
  );
}

const dialog = () => screen.getByRole("dialog");
const sectionTitles = () =>
  within(dialog())
    .getAllByRole("heading")
    .map((h) => h.textContent.trim());

async function fillTitleAndSubmit(buttonName) {
  await userEvent.type(screen.getByPlaceholderText(/q3 product launch/i), "Launch");
  await userEvent.click(screen.getByRole("button", { name: buttonName }));
}

describe("the Registration section is gone", () => {
  it("renders none of its controls or copy", async () => {
    open();
    expect(within(dialog()).queryByText(/^registration$/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/expected audience/i)).not.toBeInTheDocument();
    expect(screen.queryByPlaceholderText(/peak concurrent viewers/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/capacity approval/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/registration required/i)).not.toBeInTheDocument();
    // The capacity-limit input only ever appeared behind that toggle.
    expect(screen.queryByText(/capacity limit/i)).not.toBeInTheDocument();
  });

  it("flows Visibility -> Features -> Hosts with nothing between them", () => {
    open();
    const titles = sectionTitles();
    expect(titles).toEqual(["Create Event", "Basic Information", "Schedule", "Visibility", "Features", "Hosts"]);
    expect(titles.indexOf("Features")).toBe(titles.indexOf("Visibility") + 1);
  });
});

describe("the create payload", () => {
  it("omits the three removed fields so the server's defaults apply", async () => {
    open();
    await fillTitleAndSubmit(/schedule event/i);

    expect(api.post).toHaveBeenCalledTimes(1);
    const [url, body] = vi.mocked(api.post).mock.calls[0];
    expect(url).toBe("/events");
    // Absent, not false/null: EventCreate declares its own defaults for these, and sending
    // them from here would move that decision to the client.
    expect(body).not.toHaveProperty("registration_required");
    expect(body).not.toHaveProperty("registration_limit");
    expect(body).not.toHaveProperty("expected_audience");
  });

  it("still sends everything the remaining sections own", async () => {
    open();
    await fillTitleAndSubmit(/schedule event/i);

    const [, body] = vi.mocked(api.post).mock.calls[0];
    expect(body).toMatchObject({
      title: "Launch",
      visibility: "public",
      recording_enabled: true,
      chat_enabled: false,
      polls_enabled: false,
      qa_enabled: false,
      status: "scheduled",
    });
  });

  it("Save Draft still posts, as a draft", async () => {
    open();
    await fillTitleAndSubmit(/save draft/i);
    const [, body] = vi.mocked(api.post).mock.calls[0];
    expect(body.status).toBe("draft");
  });
});

describe("what must not have regressed", () => {
  it("keeps all three visibility choices, with Public selected by default", async () => {
    open();
    for (const label of [/^public$/i, /^unlisted$/i, /^private$/i]) {
      expect(within(dialog()).getByText(label)).toBeInTheDocument();
    }
    await fillTitleAndSubmit(/schedule event/i);
    expect(vi.mocked(api.post).mock.calls[0][1].visibility).toBe("public");
  });

  it("still applies a chosen visibility", async () => {
    open();
    await userEvent.type(screen.getByPlaceholderText(/q3 product launch/i), "Launch");
    await userEvent.click(within(dialog()).getByText(/^private$/i));
    await userEvent.click(screen.getByRole("button", { name: /schedule event/i }));
    expect(vi.mocked(api.post).mock.calls[0][1].visibility).toBe("private");
  });

  it("still blocks scheduling without a title", () => {
    open();
    expect(screen.getByRole("button", { name: /schedule event/i })).toBeDisabled();
  });

  it("still rejects an end time before the start time, without posting", async () => {
    open();
    await userEvent.type(screen.getByPlaceholderText(/q3 product launch/i), "Launch");
    const date = dialog().querySelector('input[type="date"]');
    await userEvent.type(date, "2026-12-01");
    // TimeField wraps a real <input type="time">, so start and end are these two.
    // Asserted unconditionally — guarding this behind a length check would let the whole
    // case pass by doing nothing if the field ever stopped rendering one.
    const times = [...dialog().querySelectorAll('input[type="time"]')];
    expect(times).toHaveLength(2);

    await userEvent.type(times[0], "10:00");
    await userEvent.type(times[1], "09:00");
    await userEvent.click(screen.getByRole("button", { name: /schedule event/i }));

    expect(notify.error).toHaveBeenCalledWith("End time must be after start time");
    expect(api.post).not.toHaveBeenCalled();
  });
});
