// The Media console's two status columns.
//
// They printed the same word twice: a failed recording read "• failed" under STATUS and
// "Failed" under FILE. That is not two columns saying different things badly — it is the
// verdict being DERIVED from the status. crud.event.recording_list_state maps
// recording/paused -> in_progress, failed -> failed, processing -> processing, and only a
// `stopped` row carries evidence of its own (ready | unverified | failed-when-unenforced).
//
// So both columns stay — "stopped + unverified" is the fact this console exists to show, and
// one column cannot express it — but the File column speaks only when it has something the
// Capture column has not already said.
import { render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

vi.mock("../../api", () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  errMsg: (e) => e?.message ?? "failed",
  diagnoseLoadError: (e) => e?.message ?? "failed to load",
  AUTH_EXPIRED_EVENT: "zoiko:auth-expired",
}));
vi.mock("../../auth/AuthContext", () => ({
  useAuth: () => ({ user: { id: "u-me", full_name: "Ada Ops", role: "super_admin" }, logout: vi.fn() }),
}));

import api from "../../api";
import { ThemeProvider } from "../../theme/ThemeContext";
import Media from "./Media";

const SUMMARY = { total: 6, by_state: { ready: 1, unverified: 1, processing: 1, in_progress: 2, failed: 1 }, by_status: {} };

const rec = (over) => ({
  id: "r", event_title: "Event", organization: "Acme", status: "stopped", state: "ready",
  quality: "1080p", started_at: "2026-10-22T09:00:00Z", stopped_at: "2026-10-22T10:00:00Z",
  size_bytes: 2048, enforced: true, has_file_reference: true, ...over,
});

const serve = (rows) =>
  vi.mocked(api.get).mockImplementation((url) =>
    url === "/admin/recordings/summary"
      ? Promise.resolve({ data: SUMMARY })
      : Promise.resolve({ data: rows })
  );

const show = () =>
  render(<ThemeProvider><MemoryRouter><Media /></MemoryRouter></ThemeProvider>);

const row = async (title) => (await screen.findByText(title)).closest("tr");

beforeEach(() => vi.clearAllMocks());

describe("the two columns are named for what they mean", () => {
  it("separates the capture from the file", async () => {
    serve([rec({ event_title: "Recital" })]);
    show();
    await row("Recital");
    expect(screen.getByRole("columnheader", { name: "Capture" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "File" })).toBeInTheDocument();
  });

  it("keeps every other column", async () => {
    serve([rec({ event_title: "Recital" })]);
    show();
    await row("Recital");
    for (const name of ["Event", "Organization", "Quality", "Started", "Duration", "Size", "Action"]) {
      expect(screen.getByRole("columnheader", { name })).toBeInTheDocument();
    }
  });
});

describe("a stopped recording — the only row whose file verdict is its own", () => {
  it("reports ready alongside the capture status", async () => {
    serve([rec({ event_title: "Recital", status: "stopped", state: "ready" })]);
    show();
    const tr = await row("Recital");
    expect(within(tr).getByText("stopped")).toBeInTheDocument();
    expect(within(tr).getByText("Ready")).toBeInTheDocument();
  });

  it("reports unverified — the fact one column could not carry", async () => {
    serve([rec({ event_title: "Gala", status: "stopped", state: "unverified", size_bytes: null })]);
    show();
    const tr = await row("Gala");
    expect(within(tr).getByText("stopped")).toBeInTheDocument();
    expect(within(tr).getByText("Unverified")).toBeInTheDocument();
  });

  it("reports a stopped-but-unenforced capture as a failed file", async () => {
    // status says the capture stopped; the file verdict says nothing was kept. Genuinely two
    // different facts, and the row must show both.
    serve([rec({ event_title: "Recital", status: "stopped", state: "failed", enforced: false })]);
    show();
    const tr = await row("Recital");
    expect(within(tr).getByText("stopped")).toBeInTheDocument();
    expect(within(tr).getByText("Failed")).toBeInTheDocument();
  });
});

describe("the duplicated badge is gone", () => {
  it("does not print Failed twice for a failed capture — the reported bug", async () => {
    serve([rec({ event_title: "Crash", status: "failed", state: "failed", size_bytes: null })]);
    show();
    const tr = await row("Crash");
    expect(within(tr).getByText("failed")).toBeInTheDocument();          // Capture
    expect(within(tr).queryByText("Failed")).toBeNull();                  // File, no longer echoed
    expect(within(tr).getAllByText(/^failed$/i)).toHaveLength(1);
  });

  it.each([
    ["recording", "in_progress", "In progress"],
    ["paused", "in_progress", "In progress"],
    ["processing", "processing", "Processing"],
  ])("does not echo the capture status for %s rows", async (status, state, label) => {
    serve([rec({ event_title: "Live", status, state, size_bytes: null })]);
    show();
    const tr = await row("Live");
    expect(within(tr).getByText(status)).toBeInTheDocument();
    expect(within(tr).queryByText(label)).toBeNull();
  });

  it("says why the file cell is empty instead of leaving a bare dash", async () => {
    serve([rec({ event_title: "Crash", status: "failed", state: "failed", size_bytes: null })]);
    show();
    const tr = await row("Crash");
    expect(within(tr).getByTitle(/no file was produced/i)).toBeInTheDocument();
  });

  it("gives an in-flight recording an honest reason too", async () => {
    serve([rec({ event_title: "Live", status: "recording", state: "in_progress", size_bytes: null })]);
    show();
    const tr = await row("Live");
    expect(within(tr).getByTitle(/Still recording/i)).toBeInTheDocument();
  });
});

describe("the rest of the table still works", () => {
  it("keeps the search and the status filter wired to the server", async () => {
    serve([rec({ event_title: "Recital" })]);
    show();
    await row("Recital");
    expect(screen.getByLabelText("Search recordings")).toBeInTheDocument();
    expect(screen.getByLabelText("Filter by status")).toBeInTheDocument();
  });

  it("keeps the row action", async () => {
    serve([rec({ event_title: "Recital" })]);
    show();
    const tr = await row("Recital");
    expect(within(tr).getByRole("button", { name: "Open recording" })).toBeInTheDocument();
  });

  it("still shows the unverified hint an operator relies on", async () => {
    serve([rec({ event_title: "Gala", status: "stopped", state: "unverified", size_bytes: null })]);
    show();
    const tr = await row("Gala");
    await waitFor(() => expect(within(tr).getByTitle(/never reported a file size/i)).toBeInTheDocument());
  });
});
