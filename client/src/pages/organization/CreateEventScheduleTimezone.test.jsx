import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api", () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  errMsg: (e, fallback = "Something went wrong.") => e?.response?.data?.detail || e?.message || fallback,
}));

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
import { zonedTimeToUtc, toZonedISO, isValidTimezone, parseDateParts } from "../../data/timezones";
import { fmtDateTime } from "../../data/events";

const onClose = vi.fn();
const onCreated = vi.fn();

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.get).mockResolvedValue({ data: { items: [], total: 0 } });
  vi.mocked(api.post).mockResolvedValue({ data: { id: "evt-test-1", title: "Test Event", status: "scheduled" } });
  vi.mocked(api.patch).mockResolvedValue({ data: {} });
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

const dialog = () => screen.getByRole("dialog");

describe("Timezone and Schedule logic utilities", () => {
  it("correctly identifies valid and invalid timezones", () => {
    expect(isValidTimezone("UTC")).toBe(true);
    expect(isValidTimezone("Asia/Kolkata")).toBe(true);
    expect(isValidTimezone("America/New_York")).toBe(true);
    expect(isValidTimezone("Europe/London")).toBe(true);
    expect(isValidTimezone("Invalid/Unknown_Zone")).toBe(false);
    expect(isValidTimezone("")).toBe(false);
    expect(isValidTimezone(null)).toBe(false);
  });

  it("safely parses both YYYY-MM-DD and DD-MM-YYYY date formats", () => {
    expect(parseDateParts("2026-10-06")).toEqual({ year: 2026, month: 10, day: 6, iso: "2026-10-06" });
    expect(parseDateParts("06-10-2026")).toEqual({ year: 2026, month: 10, day: 6, iso: "2026-10-06" });
    expect(parseDateParts("invalid-date")).toBeNull();
  });

  it("converts Asia/Kolkata 15:56 to exact UTC independent of browser timezone", () => {
    // 15:56 IST is UTC+5:30 -> 10:26 UTC
    const utcDate = zonedTimeToUtc("2026-10-06", "15:56", "Asia/Kolkata");
    expect(utcDate).not.toBeNull();
    expect(utcDate.toISOString()).toBe("2026-10-06T10:26:00.000Z");

    // Formatting it back in Asia/Kolkata yields 3:56 PM
    expect(fmtDateTime(utcDate.toISOString(), "Asia/Kolkata")).toContain("3:56 PM");
  });

  it("converts America/New_York 15:56 to exact UTC independent of browser timezone", () => {
    // In October (EDT, UTC-4), 15:56 EDT -> 19:56 UTC
    const utcDate = zonedTimeToUtc("2026-10-06", "15:56", "America/New_York");
    expect(utcDate).not.toBeNull();
    expect(utcDate.toISOString()).toBe("2026-10-06T19:56:00.000Z");

    // Formatting it back in America/New_York yields 3:56 PM
    expect(fmtDateTime(utcDate.toISOString(), "America/New_York")).toContain("3:56 PM");
  });

  it("converts UTC 15:56 to exact UTC", () => {
    const utcDate = zonedTimeToUtc("2026-10-06", "15:56", "UTC");
    expect(utcDate.toISOString()).toBe("2026-10-06T15:56:00.000Z");
  });
});

describe("CreateEventModal scheduling and validation flows", () => {
  it("Scenario A & G: creates a scheduled event with accurate timezone payload and refreshes list", async () => {
    const user = userEvent.setup();
    renderModal();

    await user.type(screen.getByPlaceholderText(/q3 product launch/i), "Q4 Summit");

    // Enter future date
    const dateInput = dialog().querySelector('input[type="date"]');
    const futureYear = new Date().getFullYear() + 2;
    await user.type(dateInput, `${futureYear}-10-06`);

    // Enter start and end times
    const timeInputs = dialog().querySelectorAll('input[type="time"]');
    await user.type(timeInputs[0], "14:00");
    await user.type(timeInputs[1], "15:00");

    const scheduleBtn = screen.getByRole("button", { name: /schedule event/i });
    expect(scheduleBtn).toBeEnabled();

    await user.click(scheduleBtn);

    expect(api.post).toHaveBeenCalledTimes(1);
    const [endpoint, payload] = vi.mocked(api.post).mock.calls[0];
    expect(endpoint).toBe("/events");
    expect(payload.title).toBe("Q4 Summit");
    expect(payload.status).toBe("scheduled");
    expect(payload.timezone).toBe("UTC");
    expect(payload.start_time).toBe(`${futureYear}-10-06T14:00:00.000Z`);
    expect(payload.end_time).toBe(`${futureYear}-10-06T15:00:00.000Z`);

    expect(notify.success).toHaveBeenCalledWith('"Test Event" scheduled');
    expect(onCreated).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("Scenario C: rejects past start time and disables Schedule Event button", async () => {
    const user = userEvent.setup();
    renderModal();

    await user.type(screen.getByPlaceholderText(/q3 product launch/i), "Past Event");

    const dateInput = dialog().querySelector('input[type="date"]');
    await user.type(dateInput, "2020-01-01");

    const timeInputs = dialog().querySelectorAll('input[type="time"]');
    await user.type(timeInputs[0], "10:00");

    const scheduleBtn = screen.getByRole("button", { name: /schedule event/i });
    expect(scheduleBtn).toBeDisabled();
    expect(scheduleBtn).toHaveAttribute("title", "Start time must be in the future.");

    expect(api.post).not.toHaveBeenCalled();
  });

  it("Scenario D: future date + start time with NO end time schedules successfully", async () => {
    const user = userEvent.setup();
    renderModal();

    await user.type(screen.getByPlaceholderText(/q3 product launch/i), "No End Event");

    const futureYear = new Date().getFullYear() + 2;
    const dateInput = dialog().querySelector('input[type="date"]');
    await user.type(dateInput, `${futureYear}-11-15`);

    const timeInputs = dialog().querySelectorAll('input[type="time"]');
    await user.type(timeInputs[0], "18:30");

    const scheduleBtn = screen.getByRole("button", { name: /schedule event/i });
    expect(scheduleBtn).toBeEnabled();

    await user.click(scheduleBtn);

    expect(api.post).toHaveBeenCalledTimes(1);
    const [, payload] = vi.mocked(api.post).mock.calls[0];
    expect(payload.title).toBe("No End Event");
    expect(payload.status).toBe("scheduled");
    expect(payload.start_time).toBe(`${futureYear}-11-15T18:30:00.000Z`);
    expect(payload.end_time).toBeNull();
    expect(onCreated).toHaveBeenCalledTimes(1);
  });

  it("Scenario E: end time earlier than start time is rejected without submitting", async () => {
    const user = userEvent.setup();
    renderModal();

    await user.type(screen.getByPlaceholderText(/q3 product launch/i), "Invalid Times");

    const futureYear = new Date().getFullYear() + 2;
    const dateInput = dialog().querySelector('input[type="date"]');
    await user.type(dateInput, `${futureYear}-11-15`);

    const timeInputs = dialog().querySelectorAll('input[type="time"]');
    await user.type(timeInputs[0], "16:00");
    await user.type(timeInputs[1], "15:00");

    const scheduleBtn = screen.getByRole("button", { name: /schedule event/i });
    await user.click(scheduleBtn);

    expect(notify.error).toHaveBeenCalledWith("End time must be after the start time.");
    expect(api.post).not.toHaveBeenCalled();
  });

  it("Scenario F: Save Draft creates Draft without requiring future start time", async () => {
    const user = userEvent.setup();
    vi.mocked(api.post).mockResolvedValue({ data: { id: "evt-draft-1", title: "My Draft", status: "draft" } });
    renderModal();

    await user.type(screen.getByPlaceholderText(/q3 product launch/i), "My Draft");

    const draftBtn = screen.getByRole("button", { name: /save draft/i });
    expect(draftBtn).toBeEnabled();

    await user.click(draftBtn);

    expect(api.post).toHaveBeenCalledTimes(1);
    const [, payload] = vi.mocked(api.post).mock.calls[0];
    expect(payload.title).toBe("My Draft");
    expect(payload.status).toBe("draft");
    expect(payload.start_time).toBeNull();
    expect(payload.end_time).toBeNull();

    expect(notify.success).toHaveBeenCalledWith("Draft saved");
    expect(onCreated).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("Save Draft preserves entered schedule values when provided", async () => {
    const user = userEvent.setup();
    vi.mocked(api.post).mockResolvedValue({ data: { id: "evt-draft-2", title: "Draft with Schedule", status: "draft" } });
    renderModal();

    await user.type(screen.getByPlaceholderText(/q3 product launch/i), "Draft with Schedule");

    const futureYear = new Date().getFullYear() + 2;
    const dateInput = dialog().querySelector('input[type="date"]');
    await user.type(dateInput, `${futureYear}-08-20`);

    const timeInputs = dialog().querySelectorAll('input[type="time"]');
    await user.type(timeInputs[0], "09:00");
    await user.type(timeInputs[1], "10:30");

    await user.click(screen.getByRole("button", { name: /save draft/i }));

    expect(api.post).toHaveBeenCalledTimes(1);
    const [, payload] = vi.mocked(api.post).mock.calls[0];
    expect(payload.title).toBe("Draft with Schedule");
    expect(payload.status).toBe("draft");
    expect(payload.start_time).toBe(`${futureYear}-08-20T09:00:00.000Z`);
    expect(payload.end_time).toBe(`${futureYear}-08-20T10:30:00.000Z`);
    expect(onCreated).toHaveBeenCalledTimes(1);
  });

  it("prevents duplicate submissions while request is pending", async () => {
    let resolvePost;
    vi.mocked(api.post).mockImplementation(() => new Promise((res) => { resolvePost = res; }));

    const user = userEvent.setup();
    renderModal();

    await user.type(screen.getByPlaceholderText(/q3 product launch/i), "Duplicate Test");
    const futureYear = new Date().getFullYear() + 2;
    const dateInput = dialog().querySelector('input[type="date"]');
    await user.type(dateInput, `${futureYear}-05-01`);
    const timeInputs = dialog().querySelectorAll('input[type="time"]');
    await user.type(timeInputs[0], "11:00");

    const scheduleBtn = screen.getByRole("button", { name: /schedule event/i });
    const draftBtn = screen.getByRole("button", { name: /save draft/i });

    // First click initiates request
    await user.click(scheduleBtn);

    // During in-flight request, buttons must be disabled
    expect(scheduleBtn).toBeDisabled();
    expect(draftBtn).toBeDisabled();

    // Second click should not call post again
    await user.click(scheduleBtn);
    expect(api.post).toHaveBeenCalledTimes(1);

    // Resolve request
    resolvePost({ data: { id: "evt-dup-1", title: "Duplicate Test", status: "scheduled" } });
    await waitFor(() => expect(onCreated).toHaveBeenCalledTimes(1));
  });
});
