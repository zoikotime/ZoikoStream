// Event Details -> Recording.
//
// ── THE BUG ────────────────────────────────────────────────────────────────────────────
// This tab used to be hardcoded markup — <OrganizationEmptyState title="No recording
// available" /> with no fetch behind it. It printed that sentence for every event forever:
// for an event nobody recorded, for an event still uploading, for an event whose capture
// failed with a real reason sitting in the database, and for an event whose file was
// finished and downloadable. Four different situations, one indistinguishable screen.
//
// So the assertions that matter here are about TELLING THEM APART, and in particular:
//   - a failed capture states its real reason instead of rendering as an absence, and
//   - a row with no file gets NO Watch link, because a dead link is its own kind of lie.
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import RecordingPanel from "./RecordingPanel";
import { recordingState } from "../../data/recordingState";

const EVENT = { id: "e1", recording_enabled: true };

let seq = 0;
const rec = (over = {}) => ({
  id: `r${++seq}`,
  event_id: "e1",
  status: "stopped",
  enforced: true,
  error: null,
  quality: "1080p",
  role: null,
  duration_seconds: 3600,
  size_bytes: 1024 ** 3,
  url: "https://storage.example/signed/file.mp4",
  legal_hold: false,
  validation_status: "valid",
  ...over,
});

const show = (recordings, event = EVENT) =>
  render(<RecordingPanel event={event} recordings={recordings} />);

const watchLink = () => screen.queryByRole("link", { name: /watch/i });

// ── the four states are four different screens ─────────────────────────────────────────

describe("a completed recording", () => {
  it("is offered for playback and download", () => {
    show([rec()]);

    expect(watchLink()).toHaveAttribute("href", "https://storage.example/signed/file.mp4");
    expect(screen.getByRole("link", { name: /download/i })).toBeInTheDocument();
    expect(screen.getByText("Ready")).toBeInTheDocument();
  });

  it("no longer says no recording is available", () => {
    show([rec()]);
    expect(screen.queryByText(/no recording available/i)).not.toBeInTheDocument();
  });

  it("states the facts an operator checks — quality, duration and size", () => {
    show([rec({ quality: "1080p", duration_seconds: 5400, size_bytes: 2.5 * 1024 ** 3 })]);

    const facts = screen.getByText(/1080p/);
    expect(facts.textContent).toMatch(/1h 30m/);
    expect(facts.textContent).toMatch(/2\.50 GB/);
  });
});

describe("a recording still being finalised", () => {
  it("says it is processing rather than implying a missing file", () => {
    show([rec({ url: null })]);

    expect(screen.getByText("Processing")).toBeInTheDocument();
    expect(screen.getByText(/still being finalised/i)).toBeInTheDocument();
  });

  it("offers no link, because there is nothing to open yet", () => {
    show([rec({ url: null })]);
    expect(watchLink()).not.toBeInTheDocument();
  });
});

describe("a recording still running", () => {
  it("is reported as in progress", () => {
    show([rec({ status: "recording", url: null })]);

    expect(screen.getByText("Recording")).toBeInTheDocument();
    expect(watchLink()).not.toBeInTheDocument();
  });

  it("distinguishes paused from recording", () => {
    show([rec({ status: "paused", url: null })]);
    expect(screen.getByText("Paused")).toBeInTheDocument();
  });
});

// ── the failure the report was actually about ──────────────────────────────────────────

describe("a capture that produced no file", () => {
  const failed = (over = {}) =>
    rec({
      status: "stopped",
      enforced: false,
      url: null,
      size_bytes: null,
      error: "GCS_CREDENTIALS_PATH is a URL, not a path to a service-account key file.",
      ...over,
    });

  it("shows the server's real reason verbatim", () => {
    show([failed()]);

    expect(screen.getByText(/service-account key file/i)).toBeInTheDocument();
    expect(screen.getByText("Not captured")).toBeInTheDocument();
  });

  it("does not render as an empty state", () => {
    show([failed()]);
    expect(screen.queryByText(/no recording available/i)).not.toBeInTheDocument();
  });

  it("offers no Watch link for a file that does not exist", () => {
    show([failed()]);
    expect(watchLink()).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /download/i })).not.toBeInTheDocument();
  });

  it("falls back to a plain statement when the server sent no reason", () => {
    show([rec({ status: "failed", enforced: false, url: null, error: null })]);
    expect(screen.getByText(/did not produce a file/i)).toBeInTheDocument();
  });

  it("is summarised at the top of the panel, where it cannot be missed", () => {
    show([failed()]);
    expect(screen.getByText(/did not capture a file/i)).toBeInTheDocument();
  });

  it("counts itself when only one path of a dual recording failed", () => {
    show([rec({ role: "primary" }), failed({ role: "secondary" })]);

    expect(screen.getByText(/1 of 2 recording paths did not capture a file/i)).toBeInTheDocument();
    // ...and the good path is still playable.
    expect(watchLink()).toBeInTheDocument();
  });

  it("raises no alarm when everything captured", () => {
    show([rec(), rec()]);
    expect(screen.queryByText(/did not capture a file/i)).not.toBeInTheDocument();
  });
});

// ── absence, and the difference between kinds of absence ───────────────────────────────

describe("when there is nothing to show", () => {
  it("says so plainly for an event nobody recorded", () => {
    show([]);

    expect(screen.getByText(/no recording available/i)).toBeInTheDocument();
    expect(screen.getByText(/start a recording from the producer console/i)).toBeInTheDocument();
  });

  it("explains instead that recording was switched off for the event", () => {
    show([], { id: "e1", recording_enabled: false });
    expect(screen.getByText(/recording is disabled for this event/i)).toBeInTheDocument();
  });

  it("does NOT claim there is no recording when the request failed", () => {
    // EventDetails passes null when the fetch rejects. Reporting that as "no recording
    // available" would be the original bug wearing a new hat: an error described as a fact
    // about the event.
    show(null);

    expect(screen.getByText(/couldn't load recordings/i)).toBeInTheDocument();
    expect(screen.queryByText(/no recording available/i)).not.toBeInTheDocument();
  });
});

// ── extras that must survive ───────────────────────────────────────────────────────────

describe("details carried through from the row", () => {
  it("lists every attempt the server returned", () => {
    show([rec({ role: "primary" }), rec({ role: "secondary" })]);
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
  });

  it("marks a recording under legal hold", () => {
    show([rec({ legal_hold: true })]);
    expect(screen.getByText(/legal hold/i)).toBeInTheDocument();
  });
});

// ── the verdict function on its own ─────────────────────────────────────────────────────

describe("recordingState", () => {
  it.each([
    ["a finished, enforced row with a url", { status: "stopped", enforced: true, url: "u" }, "ready"],
    ["a finished, enforced row without a url", { status: "stopped", enforced: true }, "processing"],
    ["an explicit failure", { status: "failed", enforced: true }, "failed"],
    ["stopped but never enforced", { status: "stopped", enforced: false }, "failed"],
    ["still recording", { status: "recording", enforced: true }, "in_progress"],
  ])("reads %s as %s", (_label, row, key) => {
    expect(recordingState(row).key).toBe(key);
  });

  it("treats an unenforced stop as a failure even though the status says stopped", () => {
    // The single subtlety in the whole feature: status alone is not enough. enforced=false
    // means egress never accepted the job, so "stopped" describes our own bookkeeping, not
    // a captured file.
    expect(recordingState({ status: "stopped", enforced: false }).key).toBe("failed");
  });

  it("returns null for no row at all", () => {
    expect(recordingState(null)).toBeNull();
  });
});
