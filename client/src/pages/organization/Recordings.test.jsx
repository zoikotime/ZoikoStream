// Organization Recordings: the library is truthful about what it holds and what happened.
//   * "Total Storage" is "—" (No completed recordings) with nothing completed — never "0.0 GB".
//   * A measured zero-byte capture shows "0 B"; an unknown size shows "—".
//   * Processing / storage-unavailable rows are counted as such, not folded into "0".
//   * Failed attempts (not listed — no file) are named with their reason and event link.
//   * Only a ready recording can be watched, downloaded or shared.
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

vi.mock("../../api", async (importOriginal) => {
  const real = await importOriginal();
  return { ...real, default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() } };
});
vi.mock("../../ui/Toast", () => ({
  notify: { error: vi.fn(), success: vi.fn(), alert: vi.fn(), warning: vi.fn(), info: vi.fn() },
}));
vi.mock("../../auth/AuthContext", () => ({
  useAuth: () => ({ user: { full_name: "Ada Admin", role: "org_admin" }, logout: vi.fn() }),
}));

import api from "../../api";
import { ThemeProvider } from "../../theme/ThemeContext";
import Recordings from "./Recordings";

const rec = (over) => ({
  id: "r1", event_id: "e1", title: "Town hall", category: "Company", started_at: "2026-09-27T10:00:00Z",
  duration_seconds: 3600, size_bytes: 2 * 1024 ** 3, url: "https://signed.example/r1.mp4",
  state: "ready", legal_hold: false, validation_status: null, ...over,
});
const EMPTY_SUMMARY = { library_total: 0, in_progress: 0, failed_attempts: 0, latest_failure: null };

const serve = (list, summary = EMPTY_SUMMARY) =>
  vi.mocked(api.get).mockImplementation((url) => {
    if (url === "/organization/recordings") return Promise.resolve({ data: list });
    if (url === "/organization/recordings/summary") {
      return summary instanceof Error ? Promise.reject(summary) : Promise.resolve({ data: summary });
    }
    return Promise.resolve({ data: [] });
  });

const show = () =>
  render(
    <ThemeProvider>
      <MemoryRouter>
        <Recordings />
      </MemoryRouter>
    </ThemeProvider>
  );

// The StatsCard for a title: its value and hint.
const kpi = (title) => screen.getByText(title).parentElement;

beforeEach(() => vi.clearAllMocks());

describe("totals are truthful", () => {
  it("shows no storage figure, not 0.0 GB, when nothing is completed", async () => {
    serve([]);
    show();
    expect(await screen.findByText(/presses Record during the broadcast/)).toBeInTheDocument();
    expect(within(kpi("Total Storage")).getByText("—")).toBeInTheDocument();
    expect(within(kpi("Total Storage")).getByText("No completed recordings")).toBeInTheDocument();
    expect(screen.queryByText("0.0 GB")).toBeNull();
  });

  it("names a processing recording instead of counting it as nothing", async () => {
    serve([rec({ state: "processing", url: null, size_bytes: null })],
          { ...EMPTY_SUMMARY, library_total: 1 });
    show();
    expect(await within(kpi("Total Recordings")).findByText("1 processing")).toBeInTheDocument();
    expect(within(kpi("Total Storage")).getByText("—")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Watch Replay/ })).toBeDisabled();
  });

  it("counts running and failed attempts from the server summary", async () => {
    serve([], { ...EMPTY_SUMMARY, in_progress: 1, failed_attempts: 2,
                latest_failure: { event_id: "e9", event_title: "Board meeting", error: "Egress unavailable", at: null } });
    show();
    expect(await within(kpi("Total Recordings")).findByText("1 recording now · 2 attempts not captured")).toBeInTheDocument();
    const notice = screen.getByTestId("recording-failure-notice");
    expect(notice).toHaveTextContent("2 recording attempts did not capture a file.");
    expect(notice).toHaveTextContent("Board meeting — Egress unavailable");
    expect(within(notice).getByRole("link", { name: /recording history/ })).toHaveAttribute("href", "/organization/events/e9?tab=Recording");
  });

  it("reports a known total, a zero-byte file as 0 B, and excludes unknown sizes", async () => {
    serve([
      rec({ id: "a", title: "Keynote" }),
      rec({ id: "b", title: "Empty capture", size_bytes: 0 }),
      rec({ id: "c", title: "Unsized", size_bytes: null }),
    ], { ...EMPTY_SUMMARY, library_total: 3 });
    show();
    expect(await within(kpi("Total Storage")).findByText("2.0 GB")).toBeInTheDocument();
    expect(within(kpi("Total Storage")).getByText("Excludes 1 recording of unknown size")).toBeInTheDocument();
    const empty = screen.getByText("Empty capture").closest("[class*='rounded']").parentElement;
    expect(within(empty).getByText("0 B")).toBeInTheDocument();
  });

  it("says when the list is capped", async () => {
    serve([rec()], { ...EMPTY_SUMMARY, library_total: 140 });
    show();
    expect(await screen.findByTestId("recording-list-cap")).toHaveTextContent("newest 1 of 140");
  });

  it("still renders the library when the summary is unavailable", async () => {
    serve([rec()], new Error("404"));
    show();
    expect(await screen.findByText("Town hall")).toBeInTheDocument();
    expect(screen.queryByTestId("recording-failure-notice")).toBeNull();
  });
});

describe("actions only for a real file", () => {
  it("watches, downloads and shares a ready recording", async () => {
    const open = vi.spyOn(window, "open").mockImplementation(() => null);
    serve([rec()], { ...EMPTY_SUMMARY, library_total: 1 });
    show();
    fireEvent.click(await screen.findByRole("button", { name: /Watch Replay/ }));
    const player = await screen.findByRole("dialog");
    expect(player.querySelector("video")).toHaveAttribute("src", "https://signed.example/r1.mp4");
    fireEvent.keyDown(document, { key: "Escape" });
    fireEvent.click(screen.getByRole("button", { name: "Download" }));
    expect(open).toHaveBeenCalledWith("https://signed.example/r1.mp4", "_blank", "noopener");
    expect(screen.getByRole("button", { name: "Share" })).toBeEnabled();
    open.mockRestore();
  });

  it("offers nothing playable for a file missing from storage", async () => {
    serve([rec({ state: "storage_unavailable", url: null })], { ...EMPTY_SUMMARY, library_total: 1 });
    show();
    expect(await screen.findByText("Storage unavailable")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Watch Replay/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Download" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Share" })).toBeDisabled();
    await waitFor(() => expect(within(kpi("Total Recordings")).getByText("1 unavailable in storage")).toBeInTheDocument());
  });
});
