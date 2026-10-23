// Analytics "Export Report": the toast said "Report exported" and no file appeared.
//
// The CSV was built in the page and handed to an <a download> on a blob: URL, then success
// was announced unconditionally. In a browser that is a real download; in the Android app's
// WebView the click is silently dropped, so the only thing that happened was the toast.
//
// What these pin: the file that is produced (name, content, quoting, unmeasured figures), that
// success is said ONLY after the file was actually downloaded or handed to the share sheet,
// the distinct failures (generating vs saving), the in-app path through the native bridge,
// and one export at a time. The page, utils/export and native/bridge are all REAL; only the
// API, the toasts and — for the app — window.Capacitor are stand-ins.
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

vi.mock("../../api", async (importOriginal) => {
  const real = await importOriginal();
  return { ...real, default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() } };
});
vi.mock("../../ui/Toast", () => ({
  notify: { error: vi.fn(), success: vi.fn(), alert: vi.fn(), warning: vi.fn(), info: vi.fn() },
}));

import api from "../../api";
import { notify } from "../../ui/Toast";
import { ThemeProvider } from "../../theme/ThemeContext";
import Analytics from "./Analytics";

const REPORTS = [
  { id: "e1", event: 'Keynote, "Live" 2026', date: "2026-10-02", viewers: 1200, watch_hours: 845.256, total_watch_seconds: 3042921, engagement: 71, measurement_state: "measured" },
  { id: "e2", event: "Town hall", date: "2026-10-09", viewers: 80, watch_hours: 12.5, total_watch_seconds: 45000, engagement: null, measurement_state: "measured" },
  { id: "e3", event: "Dry run", date: "2026-10-12", viewers: 0, watch_hours: null, total_watch_seconds: null, engagement: null, measurement_state: "not_measured" },
];
const PAYLOAD = {
  range: "30d", timezone: "UTC",
  summary: { peak_viewers_summed: 1280, watch_hours: 857.756, total_watch_seconds: 3087921, peak: 1200, engagement: 71 },
  trends: { viewership: [{ label: "Oct 2", value: 1200 }], watch_time: [{ label: "Oct 2", value: 845.3 }] },
  top_events: [{ label: "Keynote", value: 1200 }],
  reports: REPORTS, devices: null, locations: null, traffic_sources: null, session_summary: null,
  breakdowns_note: "Not measured yet.",
};
const EXPECTED_CSV = [
  '"Event","Date","Peak Viewers","Watch Time (hrs)","Engagement Score (%)"',
  '"Keynote, ""Live"" 2026","2026-10-02","1200","845.26","71"',
  '"Town hall","2026-10-09","80","12.50","Not measured"',
  '"Dry run","2026-10-12","0","Not measured","Not measured"',
].join("\n");
const FILENAME = "zoikostream-analytics-30d.csv";

// The browser side of a download, observed rather than performed: every blob handed to
// createObjectURL, and every anchor click (with whether the anchor was in the document).
let blobs;
let clicks;
beforeEach(() => {
  vi.clearAllMocks();
  blobs = [];
  clicks = [];
  URL.createObjectURL = vi.fn((blob) => { blobs.push(blob); return `blob:test/${blobs.length}`; });
  URL.revokeObjectURL = vi.fn();
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function click() {
    clicks.push({ href: this.getAttribute("href"), download: this.download, attached: document.body.contains(this) });
  });
  api.get.mockResolvedValue({ data: PAYLOAD });
});
afterEach(() => {
  vi.restoreAllMocks();
  delete window.Capacitor;
});

const open = () =>
  render(
    <ThemeProvider>
      <MemoryRouter initialEntries={["/organization/analytics"]}>
        <Analytics />
      </MemoryRouter>
    </ThemeProvider>
  );
const exportButton = () => screen.findByRole("button", { name: /export report/i });
const ready = async () => {
  const button = await exportButton();
  await waitFor(() => expect(button).toBeEnabled());
  return button;
};

/** The Android app: a native runtime with fake Filesystem/Share plugins recording each call. */
function nativeApp({ filesystem = true, share = true, writeFile, shareImpl } = {}) {
  const calls = { writeFile: [], share: [] };
  const Plugins = {};
  if (filesystem) {
    Plugins.Filesystem = {
      writeFile: vi.fn(async (opts) => {
        calls.writeFile.push(opts);
        return writeFile ? writeFile(opts) : { uri: `file:///data/user/0/app/cache/${opts.path}` };
      }),
    };
  }
  if (share) {
    Plugins.Share = {
      share: vi.fn(async (opts) => {
        calls.share.push(opts);
        return shareImpl ? shareImpl(opts) : { activityType: "drive" };
      }),
    };
  }
  window.Capacitor = { isNativePlatform: () => true, getPlatform: () => "android", Plugins };
  return calls;
}

describe("exporting in the browser", () => {
  it("downloads a real CSV of the loaded report, then says so once", async () => {
    open();
    fireEvent.click(await ready());
    await waitFor(() => expect(notify.success).toHaveBeenCalledTimes(1));
    expect(notify.success).toHaveBeenCalledWith("Report exported", { id: "analytics-export" });
    expect(notify.error).not.toHaveBeenCalled();

    // The data the page already fetched for this org — no second request, no other source.
    expect(api.get).toHaveBeenCalledTimes(1);
    expect(api.get).toHaveBeenCalledWith("/organization/analytics", { params: { range: "30d" } });

    expect(blobs).toHaveLength(1);
    expect(blobs[0].type).toBe("text/csv;charset=utf-8");
    expect(await blobs[0].text()).toBe(EXPECTED_CSV);

    expect(clicks).toEqual([{ href: "blob:test/1", download: FILENAME, attached: true }]);
    // The temporary anchor is gone again.
    expect(document.querySelector("a[download]")).toBeNull();
  });

  it("the Recent Reports button exports the same file", async () => {
    open();
    await ready();
    fireEvent.click(screen.getByRole("button", { name: /export csv/i }));
    await waitFor(() => expect(notify.success).toHaveBeenCalledTimes(1));
    expect(clicks).toHaveLength(1);
    expect(clicks[0].download).toBe(FILENAME);
    expect(await blobs[0].text()).toBe(EXPECTED_CSV);
  });

  it("does not revoke the file's URL in the same tick as the click", async () => {
    open();
    fireEvent.click(await ready());
    await waitFor(() => expect(notify.success).toHaveBeenCalled());
    expect(clicks).toHaveLength(1);
    expect(URL.revokeObjectURL).not.toHaveBeenCalled();
  });

  it("names the file for the window the rows came from, even mid range-switch", async () => {
    open();
    await ready();
    // Switch to 7 days and hold that request: the page still shows (and exports) 30 days.
    api.get.mockImplementation(() => new Promise(() => {}));
    fireEvent.change(screen.getByRole("combobox", { name: "Date range" }), { target: { value: "7d" } });
    fireEvent.click(await exportButton());
    await waitFor(() => expect(clicks).toHaveLength(1));
    expect(clicks[0].download).toBe(FILENAME);
  });

  it("an analytics request that failed leaves nothing to export and claims nothing", async () => {
    api.get.mockRejectedValue(Object.assign(new Error("boom"), { response: { status: 500 } }));
    open();
    await waitFor(() => expect(api.get).toHaveBeenCalled());
    const button = screen.queryByRole("button", { name: /export report/i });
    if (button) {
      expect(button).toBeDisabled();
      fireEvent.click(button);
    }
    await act(async () => {});
    expect(notify.success).not.toHaveBeenCalled();
    expect(blobs).toHaveLength(0);
  });

  it("a window with no events has nothing to export: both buttons are disabled", async () => {
    api.get.mockResolvedValue({ data: { ...PAYLOAD, reports: [] } });
    open();
    const button = await exportButton();
    await screen.findAllByText("No events in this window yet.");
    expect(button).toBeDisabled();
    expect(screen.getByRole("button", { name: /export csv/i })).toBeDisabled();
    fireEvent.click(button);
    await act(async () => {});
    expect(blobs).toHaveLength(0);
    expect(notify.success).not.toHaveBeenCalled();
  });

  it("data the report cannot be built from is a generation error, not a success", async () => {
    // A row the export cannot format (watch_hours serialised as a string) on the table's
    // SECOND page, so the screen renders and only the export meets it.
    const fine = Array.from({ length: 10 }, (_, i) => ({ ...REPORTS[1], id: `ok${i}`, event: `Event ${i}`, viewers: 100 + i }));
    const bad = { ...REPORTS[1], id: "bad", event: "Malformed", viewers: 1, watch_hours: "12.5" };
    api.get.mockResolvedValue({ data: { ...PAYLOAD, reports: [...fine, bad] } });
    open();
    fireEvent.click(await ready());
    await waitFor(() => expect(notify.error).toHaveBeenCalledTimes(1));
    expect(notify.error.mock.calls[0][0]).toMatch(/couldn't generate the report/i);
    expect(notify.success).not.toHaveBeenCalled();
    expect(blobs).toHaveLength(0);
    expect(await exportButton()).toBeEnabled();
  });

  it("a download the browser refuses is a save error, not a success, and the button recovers", async () => {
    URL.createObjectURL = vi.fn(() => { throw new Error("blocked"); });
    open();
    const button = await ready();
    fireEvent.click(button);
    await waitFor(() => expect(notify.error).toHaveBeenCalledTimes(1));
    expect(notify.error.mock.calls[0][0]).toBe("Couldn't download the report file.");
    expect(notify.success).not.toHaveBeenCalled();
    await waitFor(() => expect(button).toBeEnabled());
  });

  it("taps that land together start one export", async () => {
    open();
    const button = await ready();
    // One act: React has not re-rendered (so nothing is disabled yet) when the later taps land.
    act(() => {
      fireEvent.click(button);
      fireEvent.click(button);
      fireEvent.click(screen.getByRole("button", { name: /export csv/i }));
    });
    await waitFor(() => expect(notify.success).toHaveBeenCalled());
    await act(async () => {});
    expect(clicks).toHaveLength(1);
    expect(blobs).toHaveLength(1);
    expect(notify.success).toHaveBeenCalledTimes(1);
  });
});

describe("exporting in the Android app", () => {
  it("writes the CSV to the app cache and opens the share sheet, then says so", async () => {
    const calls = nativeApp();
    open();
    fireEvent.click(await ready());
    await waitFor(() => expect(notify.success).toHaveBeenCalledWith("Report exported", { id: "analytics-export" }));
    expect(calls.writeFile).toEqual([{ path: FILENAME, data: EXPECTED_CSV, directory: "CACHE", encoding: "utf8", recursive: true }]);
    expect(calls.share).toEqual([{ title: FILENAME, dialogTitle: FILENAME, files: [`file:///data/user/0/app/cache/${FILENAME}`] }]);
    // The browser path that the WebView drops is not used at all.
    expect(clicks).toHaveLength(0);
    expect(blobs).toHaveLength(0);
  });

  it("says export is unavailable — never 'exported' — when the app lacks the file plugins", async () => {
    nativeApp({ filesystem: false, share: false });
    open();
    fireEvent.click(await ready());
    await waitFor(() => expect(notify.error).toHaveBeenCalledTimes(1));
    expect(notify.error.mock.calls[0][0]).toMatch(/isn't available in the app yet/i);
    expect(notify.success).not.toHaveBeenCalled();
    expect(clicks).toHaveLength(0);
  });

  it("a file that could not be written is a save error", async () => {
    const calls = nativeApp({ writeFile: () => { throw new Error("disk full"); } });
    open();
    fireEvent.click(await ready());
    await waitFor(() => expect(notify.error).toHaveBeenCalledWith("Couldn't save the report file on this device.", { id: "analytics-export" }));
    expect(calls.share).toHaveLength(0);
    expect(notify.success).not.toHaveBeenCalled();
  });

  it("a share sheet that fails is a save error; one the user dismisses is just cancelled", async () => {
    nativeApp({ shareImpl: () => { throw new Error("No Activity found"); } });
    const first = open();
    fireEvent.click(await ready());
    await waitFor(() => expect(notify.error).toHaveBeenCalledWith("Couldn't open the share sheet to save the report.", { id: "analytics-export" }));
    first.unmount();

    vi.clearAllMocks();
    api.get.mockResolvedValue({ data: PAYLOAD });
    nativeApp({ shareImpl: () => { throw new Error("Share canceled"); } });
    open();
    fireEvent.click(await ready());
    await waitFor(() => expect(notify.info).toHaveBeenCalledWith("Export cancelled.", { id: "analytics-export" }));
    expect(notify.error).not.toHaveBeenCalled();
    expect(notify.success).not.toHaveBeenCalled();
  });

  it("shows the export as busy while the share sheet is open, ignores taps, then recovers", async () => {
    let finish;
    const calls = nativeApp({ shareImpl: () => new Promise((resolve) => { finish = resolve; }) });
    open();
    const button = await ready();
    fireEvent.click(button);
    await waitFor(() => expect(calls.share).toHaveLength(1));
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("aria-busy", "true");
    fireEvent.click(button);
    fireEvent.click(screen.getByRole("button", { name: /export csv/i }));
    expect(calls.writeFile).toHaveLength(1);
    await act(async () => finish({ activityType: "drive" }));
    await waitFor(() => expect(button).toBeEnabled());
    expect(button).not.toHaveAttribute("aria-busy");
    expect(notify.success).toHaveBeenCalledTimes(1);
  });
});
