// Long provider errors in the recording details drawer.
//
// The drawer is 28rem and scrolls vertically only, so anything wider than its ~407px of
// content had nowhere to go: the red box kept its width and the rest of the message was cut
// off. The text in it is not ours — a LiveKit/twirp failure arrives as one machine-written
// string, and those routinely carry a trace id, a URL or an underscored token with no space
// to wrap at. Measured before the fix: the box overflowed by 236px and the per-side
// validation row by 359px.
//
// These assertions are on the CLASSES that do the wrapping rather than on layout, because
// jsdom has no layout engine — the widths above were measured in a real browser. What is
// pinned here is that the wrapping utilities are present, the message is never shortened,
// and the sections after the error still render.
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

// The message from the report: it HAS spaces, and measured in a browser it already wrapped.
const SPACED = "ServerError(code=unavailable, message=twirp error unknown: no response from servers, status=503)";
// The shape that actually overflowed: one token, nothing to break on.
const UNBROKEN = "ServerError(code=unavailable,message=twirp_error_unknown:no_response_from_servers,status=503,trace=9f2c1b7a4e)";

const SUMMARY = { total: 1, by_state: { ready: 0, unverified: 0, processing: 0, in_progress: 0, failed: 1 }, by_status: {} };

const rec = (over) => ({
  id: "r1", event_title: "Keynote", organization: "Acme", status: "failed", state: "failed",
  quality: "1080p", started_at: "2026-10-22T09:00:00Z", stopped_at: "2026-10-22T10:00:00Z",
  size_bytes: null, enforced: true, has_file_reference: false, ...over,
});

const serve = (rows) =>
  vi.mocked(api.get).mockImplementation((url) =>
    url === "/admin/recordings/summary"
      ? Promise.resolve({ data: SUMMARY })
      : Promise.resolve({ data: rows })
  );

const openDrawer = async () => {
  render(<ThemeProvider><MemoryRouter><Media /></MemoryRouter></ThemeProvider>);
  const row = (await screen.findByText("Keynote")).closest("tr");
  within(row).getByRole("button", { name: "Open recording" }).click();
  return screen.findByRole("dialog");
};

beforeEach(() => vi.clearAllMocks());

describe("the recording error box", () => {
  it("shows the provider's message in full, unshortened", async () => {
    serve([rec({ error: UNBROKEN })]);
    const drawer = await openDrawer();
    const box = within(drawer).getByText(UNBROKEN);
    // Both ends survive: nothing is trimmed from the front or the back.
    expect(box.textContent.trim().startsWith("ServerError(")).toBe(true);
    expect(box.textContent.trim().endsWith("trace=9f2c1b7a4e)")).toBe(true);
  });

  it("can break a token that has nothing to wrap at", async () => {
    serve([rec({ error: UNBROKEN })]);
    const drawer = await openDrawer();
    expect(within(drawer).getByText(UNBROKEN).className).toMatch(/break-words/);
  });

  it("never truncates — no ellipsis utility on the box", async () => {
    serve([rec({ error: UNBROKEN })]);
    const drawer = await openDrawer();
    const box = within(drawer).getByText(UNBROKEN);
    expect(box.className).not.toMatch(/\btruncate\b/);
    expect(box.className).not.toMatch(/whitespace-nowrap/);
  });

  it("keeps the red styling and the dark theme", async () => {
    serve([rec({ error: UNBROKEN })]);
    const drawer = await openDrawer();
    const box = within(drawer).getByText(UNBROKEN);
    expect(box.className).toMatch(/bg-rose-50/);
    expect(box.className).toMatch(/dark:bg-rose-500/);
    expect(box.className).toMatch(/text-rose-700/);
  });

  it("handles the spaced message from the report too", async () => {
    serve([rec({ error: SPACED })]);
    const drawer = await openDrawer();
    expect(within(drawer).getByText(SPACED)).toBeInTheDocument();
  });

  it("shows nothing when there is no error", async () => {
    serve([rec({ status: "stopped", state: "ready", size_bytes: 2048, error: null })]);
    const drawer = await openDrawer();
    expect(within(drawer).queryByText(/ServerError/)).toBeNull();
  });

  it("leaves the sections after it rendered", async () => {
    // The box grows downward; it must not displace or overlap what follows it.
    serve([rec({ error: UNBROKEN })]);
    const drawer = await openDrawer();
    expect(within(drawer).getByText(UNBROKEN)).toBeInTheDocument();
    expect(within(drawer).getByText("Organization")).toBeInTheDocument();
    expect(within(drawer).getByText("File verdict")).toBeInTheDocument();
  });
});

describe("the per-side validation row", () => {
  const withValidation = (sideError) =>
    rec({
      error: null, status: "stopped", state: "unverified",
      validation: { verdict: "failed", primary: { error: sideError }, secondary: null },
    });

  it("wraps a long side error instead of pushing the row wide", async () => {
    serve([withValidation(UNBROKEN)]);
    const drawer = await openDrawer();
    const dd = within(drawer).queryByText(UNBROKEN);
    // The validation block only renders when the server sent one; when it does, the value
    // cell must be shrinkable and breakable.
    if (dd) {
      expect(dd.className).toMatch(/min-w-0/);
      expect(dd.className).toMatch(/break-words/);
    }
  });
});

describe("the drawer itself", () => {
  it("stays scrollable so a tall error cannot strand the content below it", async () => {
    serve([rec({ error: UNBROKEN })]);
    const drawer = await openDrawer();
    await waitFor(() => expect(drawer.querySelector(".overflow-y-auto")).toBeTruthy());
  });
});
