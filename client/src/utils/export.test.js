// exportTextFile and the native save behind it: the outcome each platform reports, so a
// caller can never announce a file that was not produced. See Analytics.export.test.jsx for
// the page that uses it.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { BLOB_URL_TTL_MS, exportTextFile } from "./export";
import { saveFileNative } from "../native/bridge";

let clicks;
beforeEach(() => {
  clicks = [];
  URL.createObjectURL = vi.fn(() => "blob:test/1");
  URL.revokeObjectURL = vi.fn();
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function click() {
    clicks.push({ href: this.getAttribute("href"), download: this.download, attached: document.body.contains(this) });
  });
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
  delete window.Capacitor;
});

const app = (Plugins) => { window.Capacitor = { isNativePlatform: () => true, Plugins }; };

describe("exportTextFile in the browser", () => {
  it("downloads the text as a file with the given name and type", async () => {
    const result = await exportTextFile("r.csv", "a,b\n1,2", "text/csv;charset=utf-8");
    expect(result).toEqual({ ok: true, via: "download" });
    const blob = URL.createObjectURL.mock.calls[0][0];
    expect(blob.type).toBe("text/csv;charset=utf-8");
    expect(await blob.text()).toBe("a,b\n1,2");
    expect(clicks).toEqual([{ href: "blob:test/1", download: "r.csv", attached: true }]);
    expect(document.querySelector("a[download]")).toBeNull();
  });

  it("revokes the blob URL after the browser has had it, not in the same tick", async () => {
    vi.useFakeTimers();
    await exportTextFile("r.csv", "x", "text/csv");
    expect(URL.revokeObjectURL).not.toHaveBeenCalled();
    vi.advanceTimersByTime(BLOB_URL_TTL_MS);
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:test/1");
  });

  it("reports a refused download instead of throwing", async () => {
    URL.createObjectURL = vi.fn(() => { throw new Error("blocked"); });
    await expect(exportTextFile("r.csv", "x", "text/csv")).resolves.toEqual({ ok: false, reason: "download" });
    expect(clicks).toHaveLength(0);
  });

  it("refuses to save an empty or non-text file", async () => {
    for (const bad of ["", null, undefined, 42, { a: 1 }]) {
      await expect(exportTextFile("r.csv", bad, "text/csv")).resolves.toEqual({ ok: false, reason: "empty" });
    }
    expect(URL.createObjectURL).not.toHaveBeenCalled();
    expect(clicks).toHaveLength(0);
  });
});

describe("exportTextFile in the Android app", () => {
  it("goes through the share sheet and never through a blob download", async () => {
    const writeFile = vi.fn(async () => ({ uri: "file:///cache/r.csv" }));
    const share = vi.fn(async () => ({}));
    app({ Filesystem: { writeFile }, Share: { share } });
    await expect(exportTextFile("r.csv", "a,b", "text/csv")).resolves.toEqual({ ok: true, via: "share" });
    expect(writeFile).toHaveBeenCalledWith({ path: "r.csv", data: "a,b", directory: "CACHE", encoding: "utf8", recursive: true });
    expect(share).toHaveBeenCalledWith({ title: "r.csv", dialogTitle: "r.csv", files: ["file:///cache/r.csv"] });
    expect(URL.createObjectURL).not.toHaveBeenCalled();
    expect(clicks).toHaveLength(0);
  });

  it("does not fall back to the blob download the WebView would drop", async () => {
    app({});
    await expect(exportTextFile("r.csv", "a,b", "text/csv")).resolves.toEqual({ ok: false, reason: "unsupported" });
    expect(clicks).toHaveLength(0);
  });
});

describe("saveFileNative", () => {
  it("is unsupported off the native runtime, or without either plugin", async () => {
    await expect(saveFileNative({ filename: "r.csv", text: "x" })).resolves.toEqual({ ok: false, reason: "unsupported" });
    app({ Filesystem: { writeFile: vi.fn() } });
    await expect(saveFileNative({ filename: "r.csv", text: "x" })).resolves.toEqual({ ok: false, reason: "unsupported" });
    app({ Share: { share: vi.fn() } });
    await expect(saveFileNative({ filename: "r.csv", text: "x" })).resolves.toEqual({ ok: false, reason: "unsupported" });
  });

  it("reports a failed or uri-less write, and does not share", async () => {
    const share = vi.fn();
    app({ Filesystem: { writeFile: vi.fn(async () => { throw new Error("EACCES"); }) }, Share: { share } });
    await expect(saveFileNative({ filename: "r.csv", text: "x" })).resolves.toEqual({ ok: false, reason: "write" });
    app({ Filesystem: { writeFile: vi.fn(async () => ({})) }, Share: { share } });
    await expect(saveFileNative({ filename: "r.csv", text: "x" })).resolves.toEqual({ ok: false, reason: "write" });
    expect(share).not.toHaveBeenCalled();
  });

  it("tells a dismissed share sheet apart from a failed one", async () => {
    const writeFile = vi.fn(async () => ({ uri: "file:///cache/r.csv" }));
    app({ Filesystem: { writeFile }, Share: { share: vi.fn(async () => { throw new Error("Share canceled"); }) } });
    await expect(saveFileNative({ filename: "r.csv", text: "x" })).resolves.toEqual({ ok: false, reason: "cancelled" });
    app({ Filesystem: { writeFile }, Share: { share: vi.fn(async () => { throw new Error("No Activity found to handle Intent"); }) } });
    await expect(saveFileNative({ filename: "r.csv", text: "x" })).resolves.toEqual({ ok: false, reason: "share" });
  });
});
